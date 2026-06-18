"""LangGraph state machine for the authoring loop.

Replaces the hand-rolled while-loop split between `session.py` and `service.py`.
The graph is built per-session/per-call (`build_authoring_graph(...)`) so the
nodes can close over the runtime context (registry, repair-lock, client, output
dir, validator) without forcing them through LangGraph's serialization layer.

Every routing decision and every appended message corresponds 1:1 to a branch
in the original session loop; see `tests/test_authoring_session.py` for the
behavior contract this preserves.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from .lc_tools import make_lc_tools
from .models import (
    ApprovalRequest,
    AuthoringResult,
    AuthoringStatus,
    ClarificationQuestion,
    FailureCategory,
)
from .repair_lock import RepairLockState
from .tools import PARALLEL_SAFE_TOOLS, AuthoringToolRegistry
from .trace import ModelTraceRecorder
from .validator import AuthoringValidator


@dataclass(frozen=True)
class AuthoringConcurrencyConfig:
    """Toggles for the parallelization phases.

    Each phase can be disabled to bisect a regression. Defaults are tuned
    for live use; tests that care about ordering pass the all-off config.
    """
    parallel_tool_dispatch: bool = True
    speculative_compile: bool = True
    prefetch_deterministic: bool = True
    orchestrator_grounding: bool = True
    prefetch_subagent: bool = field(
        default_factory=lambda: os.environ.get("FLUENTVIBE_PREFETCH_LM") == "1"
    )
    worker_pool_size: int = field(
        default_factory=lambda: min(8, (os.cpu_count() or 4))
    )

    @classmethod
    def all_off(cls) -> "AuthoringConcurrencyConfig":
        return cls(
            parallel_tool_dispatch=False,
            speculative_compile=False,
            prefetch_deterministic=False,
            orchestrator_grounding=False,
            prefetch_subagent=False,
        )


# ── Graph state ──────────────────────────────────────────────────────

class GraphState(TypedDict, total=False):
    """Mutable state threaded through every node.

    `messages` accumulates LangChain BaseMessage objects (System/Human/AI/Tool).
    `result` is set by terminal nodes; presence ends the graph.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    iterations: int
    tool_call_count: int
    best_code: str | None
    last_validation: Any
    current_group_index: int
    last_accepted_source_hash: str | None
    result: AuthoringResult | None
    prompt: str
    adherence_nudges: int
    # Last draft that compiled+simulated cleanly, captured before an adherence
    # nudge, as a ready success result. Used as the accept-with-gaps fallback if
    # the model cannot produce another compiling draft after being nudged.
    fallback_result: AuthoringResult | None


# ── Public API ───────────────────────────────────────────────────────

def build_authoring_graph(
    *,
    registry: AuthoringToolRegistry,
    client: Any,
    output_dir: Path,
    retry_budget: int,
    validator: AuthoringValidator | None = None,
    repair_lock: RepairLockState | None = None,
    system_prompt: str | None = None,
    helpers: Any | None = None,
    concurrency: AuthoringConcurrencyConfig | None = None,
    trace_recorder: ModelTraceRecorder | None = None,
) -> Any:
    """Compile a LangGraph state machine for one authoring run/session.

    Returns the compiled graph; the caller invokes it with an initial state.
    """
    validator = validator or AuthoringValidator()
    repair_lock = repair_lock or RepairLockState()
    if helpers is None:
        from .service import PromptAuthoringService
        helpers = PromptAuthoringService.__new__(PromptAuthoringService)  # no LM init
    client = adapt_client(client)
    _scope = getattr(registry, "lab_scope", None)
    _denied = _scope.denied_tools() if _scope is not None else None
    # Enforce mode: the LM may call only simulate_python_draft and
    # compile_and_simulate (everything the other tools fetched now lives in
    # the cheatsheet/reference doc). Translate the allow-list into a deny-set
    # against the full tool universe.
    if _scope is not None and _scope.allowed_tools() is not None:
        from .tools import tool_definitions
        allowed = _scope.allowed_tools() or frozenset()
        _denied = frozenset(
            d["function"]["name"] for d in tool_definitions()
        ) - allowed
    if registry.workflow_plan is not None:
        registry.staged_drafting = _should_stage(registry)
    lc_tools = make_lc_tools(registry, denied=_denied or None)
    try:
        client_with_tools = client.bind_tools(lc_tools)
    except NotImplementedError:
        # Fake chat models in unit tests don't implement bind_tools — fall back
        # to the raw client. Test fixtures supply pre-baked AIMessage tool_calls
        # so this is harmless.
        client_with_tools = client

    max_iterations = max(8, retry_budget + 8)
    max_tool_calls = max(12, retry_budget * 4 + 12)

    nodes = _Nodes(
        registry=registry,
        client_with_tools=client_with_tools,
        validator=validator,
        repair_lock=repair_lock,
        output_dir=output_dir,
        max_iterations=max_iterations,
        max_tool_calls=max_tool_calls,
        helpers=helpers,
        concurrency=concurrency or AuthoringConcurrencyConfig(),
        trace_recorder=trace_recorder,
    )

    builder = StateGraph(GraphState)
    builder.add_node("intent_nudge", nodes.intent_nudge)
    builder.add_node("model_call", nodes.model_call)
    builder.add_node("dispatch_tools", nodes.dispatch_tools)
    builder.add_node("extract_python", nodes.extract_python)
    builder.add_node("validate_locally", nodes.validate_locally)

    builder.add_edge(START, "intent_nudge")
    builder.add_edge("intent_nudge", "model_call")
    builder.add_conditional_edges("model_call", nodes.route_after_model)
    # dispatch_tools / extract_python / validate_locally use Command(goto=...)
    # to route, so no explicit edges needed.

    return builder.compile()


def run_graph(
    *,
    prompt: str,
    output_dir: Path,
    retry_budget: int,
    registry: AuthoringToolRegistry,
    client: Any,
    system_prompt: str,
    initial_messages: list[BaseMessage] | None = None,
    initial_state: GraphState | None = None,
    validator: AuthoringValidator | None = None,
    concurrency: AuthoringConcurrencyConfig | None = None,
    trace_recorder: ModelTraceRecorder | None = None,
) -> AuthoringResult:
    """Compile and run the graph; return the terminal AuthoringResult."""
    graph = build_authoring_graph(
        registry=registry,
        client=client,
        output_dir=output_dir,
        retry_budget=retry_budget,
        validator=validator,
        concurrency=concurrency,
        trace_recorder=trace_recorder,
    )
    if initial_state is None:
        registry.set_authoring_context(
            original_prompt=prompt,
            latest_user_text=prompt,
            user_history_text=prompt,
        )
        messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        if initial_messages:
            messages.extend(initial_messages)
        messages.append(HumanMessage(content=prompt))

        # Enforce removes the intent gate too — the model drafts directly from
        # the cheatsheet/reference and the user's prose, no declare_intent.
        if not _gates_off(registry):
            from .service import _intent_axis_message, _missing_intent_axes
            missing_axes = _missing_intent_axes(prompt)
            if missing_axes and not registry.current_intent.is_specified():
                messages.append(_to_lc_message(_intent_axis_message(missing_axes)))

        initial_state = GraphState(
            messages=messages,
            iterations=0,
            tool_call_count=0,
            best_code=None,
            last_validation=None,
            current_group_index=0,
            last_accepted_source_hash=None,
            result=None,
            prompt=prompt,
            adherence_nudges=0,
            fallback_result=None,
        )

    final_state: GraphState = graph.invoke(initial_state)
    result = _prefer_fallback(final_state.get("result"), final_state.get("fallback_result"))
    if result is None:
        # Graph fell off the end without a terminal — should not happen, but
        # produce a defensive failure rather than crashing.
        return _build_failure(
            registry=registry,
            state=final_state,
            category=FailureCategory.MODEL_AUTHORING_FAILURE,
            message="Graph terminated without setting a result.",
        )
    return result


# ── Node implementations ─────────────────────────────────────────────

@dataclass
class _Nodes:
    registry: AuthoringToolRegistry
    client_with_tools: Any
    validator: AuthoringValidator
    repair_lock: RepairLockState
    output_dir: Path
    max_iterations: int
    max_tool_calls: int
    helpers: Any
    concurrency: AuthoringConcurrencyConfig = field(default_factory=AuthoringConcurrencyConfig)
    trace_recorder: ModelTraceRecorder | None = None

    def _effective_max_iterations(self) -> int:
        """Iteration cap, scaled up for staged drafting.

        Staged mode needs ~1+ model turn per declared functional group; the
        base ``max_iterations`` (tuned for one-shot) would cut a multi-stage
        protocol off mid-plan. Scale with the declared group count.
        """
        if getattr(self.registry, "staged_drafting", False):
            groups = len(_workflow_groups(self.registry))
            if groups:
                return max(self.max_iterations, groups * 2 + 8)
        return self.max_iterations

    def _effective_max_tool_calls(self) -> int:
        """Tool-call cap, scaled up for staged drafting (declare + per-group
        simulate + repairs + compile)."""
        if getattr(self.registry, "staged_drafting", False):
            groups = len(_workflow_groups(self.registry))
            if groups:
                return max(self.max_tool_calls, groups * 3 + 12)
        return self.max_tool_calls

    # Intent-axis nudge — runs once at the start of each `invoke`. Kept as a
    # node (not in run_graph) so multi-turn callers re-evaluate per send().
    def intent_nudge(self, state: GraphState) -> dict[str, Any]:
        # Already prepended by run_graph for the first turn; this node exists
        # so the graph has a canonical entry seam. No-op when invoked.
        return {}

    def model_call(self, state: GraphState) -> dict[str, Any]:
        iterations = state.get("iterations", 0) + 1
        if iterations > self._effective_max_iterations():
            return {
                "iterations": iterations,
                "result": _build_failure(
                    registry=self.registry,
                    state=state,
                    category=FailureCategory.RETRY_BUDGET_EXHAUSTED,
                    message="Model authoring loop exhausted its iteration budget.",
                ),
            }
        import time as _time
        request_id = ""
        if self.trace_recorder is not None:
            request_id = self.trace_recorder.begin_request(
                model=_client_attr(self.client_with_tools, "model"),
                endpoint=_client_attr(self.client_with_tools, "endpoint")
                or _client_attr(self.client_with_tools, "base_url"),
                iteration=iterations,
            )
            self.trace_recorder.record(
                "request_payload",
                request_id=request_id,
                messages=[_message_to_trace_dict(m) for m in state["messages"]],
            )
        t0 = _time.monotonic()
        try:
            response = self.client_with_tools.invoke(state["messages"])
        except Exception as exc:
            if self.trace_recorder is not None:
                self.trace_recorder.record(
                    "request_error",
                    request_id=request_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
            return {
                "iterations": iterations,
                "result": _build_failure(
                    registry=self.registry,
                    state=state,
                    category=FailureCategory.MODEL_AUTHORING_FAILURE,
                    message=str(exc),
                ),
            }
        dt = _time.monotonic() - t0
        n_tool_calls = len(getattr(response, "tool_calls", None) or [])
        self.registry.record_model_turn(
            iteration=iterations,
            elapsed_ms=dt * 1000.0,
            tool_calls=list(getattr(response, "tool_calls", None) or []),
        )
        if self.trace_recorder is not None:
            self.trace_recorder.record(
                "model_turn",
                request_id=request_id,
                iteration=iterations,
                duration_ms=dt * 1000.0,
                model=_client_attr(self.client_with_tools, "model"),
                endpoint=_client_attr(self.client_with_tools, "endpoint")
                or _client_attr(self.client_with_tools, "base_url"),
                assistant=_message_to_trace_dict(response),
                tool_calls=list(getattr(response, "tool_calls", None) or []),
                response_metadata=getattr(response, "response_metadata", None) or {},
            )
        names = ", ".join((tc.get("name") or "?") for tc in (getattr(response, "tool_calls", None) or []))
        import sys as _sys
        print(
            f"[lm] turn {iterations}: {dt:.1f}s, {n_tool_calls} tool_call(s)"
            + (f" [{names}]" if names else ""),
            file=_sys.stderr,
            flush=True,
        )
        return {"iterations": iterations, "messages": [response]}

    def route_after_model(self, state: GraphState) -> str:
        if state.get("result") is not None:
            return END
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls:
            return "dispatch_tools"
        return "extract_python"

    # ── dispatch_tools ────────────────────────────────────────────

    def dispatch_tools(self, state: GraphState) -> Command:
        return _dispatch_tools_with_concurrency(self, state)

    def _dispatch_tools_serial_body(
        self,
        state: GraphState,
        *,
        parallel_futures: "dict[int, Future[dict[str, Any]]]",
        speculative_compiles: "dict[str, Future[dict[str, Any]]]",
    ) -> Command:
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        appended: list[BaseMessage] = []
        tool_call_count = state.get("tool_call_count", 0)
        best_code = state.get("best_code")
        last_validation = state.get("last_validation")
        current_group_index = state.get("current_group_index", 0)
        last_accepted_source_hash = state.get("last_accepted_source_hash")
        adherence_nudges = state.get("adherence_nudges", 0)
        fallback_result = state.get("fallback_result")

        for _enum_idx, call in enumerate(tool_calls):
            tool_call_count += 1
            if tool_call_count > self._effective_max_tool_calls():
                return Command(
                    update={
                        "tool_call_count": tool_call_count,
                        "messages": appended,
                        "best_code": best_code,
                        "last_validation": last_validation,
                        "current_group_index": current_group_index,
                        "last_accepted_source_hash": last_accepted_source_hash,
                        "result": _build_failure(
                            registry=self.registry,
                            state=state,
                            category=FailureCategory.RETRY_BUDGET_EXHAUSTED,
                            message="Model exceeded the tool-call budget.",
                            best_code=best_code,
                            last_validation=last_validation,
                        ),
                    },
                    goto=END,
                )

            name = _tool_call_name(call)
            arguments = _tool_call_args(call)
            tool_call_id = _tool_call_id(call) or f"tool-{tool_call_count}"

            block_reason = self.repair_lock.block_reason(name)
            if block_reason is not None:
                appended.append(ToolMessage(
                    content=json.dumps({
                        "ok": False,
                        "category": "repair_lock_violation",
                        "message": block_reason,
                        "active_failure_category": self.repair_lock.category,
                    }, default=str),
                    tool_call_id=tool_call_id,
                    name=name,
                ))
                appended.append(HumanMessage(content=block_reason))
                continue

            approval_block = _approval_stage_block(
                registry=self.registry,
                tool_name=name,
            )
            if approval_block is not None:
                appended.append(ToolMessage(
                    content=json.dumps(approval_block, default=str),
                    tool_call_id=tool_call_id,
                    name=name,
                ))
                appended.append(HumanMessage(content=approval_block["message"]))
                continue

            stage_block = _workflow_stage_block(
                registry=self.registry,
                tool_name=name,
                arguments=arguments,
                current_group_index=current_group_index,
            )
            if stage_block is not None:
                appended.append(ToolMessage(
                    content=json.dumps(stage_block, default=str),
                    tool_call_id=tool_call_id,
                    name=name,
                ))
                appended.append(HumanMessage(content=stage_block["message"]))
                continue

            result = _resolve_dispatch(
                registry=self.registry,
                name=name,
                arguments=arguments,
                index=_enum_idx,
                parallel_futures=parallel_futures,
                speculative_compiles=speculative_compiles,
            )
            if result.get("status") == "needs_approval":
                return Command(
                    update={
                        "tool_call_count": tool_call_count,
                        "messages": appended + [ToolMessage(
                            content=json.dumps(result, default=str),
                            tool_call_id=tool_call_id,
                            name=name,
                        )],
                        "best_code": best_code,
                        "last_validation": last_validation,
                        "current_group_index": current_group_index,
                        "last_accepted_source_hash": last_accepted_source_hash,
                        "result": _build_approval(
                            registry=self.registry,
                            state=state,
                            approval=result.get("approval") or {},
                            best_code=best_code,
                            last_validation=last_validation,
                        ),
                    },
                    goto=END,
                )
            if name == "declare_intent" and result.get("ok") is True:
                grounding = _run_orchestrator_grounding_if_ready(self)
                if grounding is not None:
                    appended.append(HumanMessage(content=(
                        "Automatic parallel grounding completed after intent declaration. "
                        f"Result: {json.dumps(grounding, default=str)}"
                    )))
            if name == "declare_protocol_workflow" and result.get("ok") is True:
                # Decide now whether this declared plan needs per-group staging
                # (multi-stage) or may be drafted in one pass (skills-simple).
                self.registry.staged_drafting = _should_stage(self.registry)
                appended.append(ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=tool_call_id,
                    name=name,
                ))
                if getattr(self.registry, "staged_drafting", False):
                    appended.append(_workflow_next_group_message(self.registry, current_group_index))
                else:
                    appended.append(HumanMessage(content=(
                        "Workflow recorded. This protocol is simple enough to draft in one "
                        "pass: write the complete build_worktable() source for all groups and "
                        "call simulate_python_draft, then compile_and_simulate once it passes."
                    )))
                continue

            repair_guidance = None
            if name in {"simulate_python_draft", "compile_and_simulate"} and result.get("ok") is False:
                repair_guidance = _missing_method_recipe_guidance(
                    result=result,
                    arguments=arguments,
                    prompt=state.get("prompt", ""),
                )
                if repair_guidance is not None:
                    result = dict(result)
                    result["retrieved_recipes"] = repair_guidance["recipes"]
            if name in {"present_source_protocol_plan", "present_object_draft", "present_functional_group_plan"}:
                _supersede_prior_drafts(name, list(state["messages"]) + appended)
            appended.append(ToolMessage(
                content=json.dumps(result, default=str),
                tool_call_id=tool_call_id,
                name=name,
            ))

            terminal = self.repair_lock.observe_tool_result(name, arguments, result)
            if terminal is not None:
                if repair_guidance is not None:
                    terminal = dict(terminal)
                    terminal.setdefault("retrieved_recipes", repair_guidance["recipes"])
                return Command(
                    update={
                        "tool_call_count": tool_call_count,
                        "messages": appended,
                        "best_code": best_code,
                        "last_validation": last_validation,
                        "current_group_index": current_group_index,
                        "last_accepted_source_hash": last_accepted_source_hash,
                        "result": _build_failure(
                            registry=self.registry,
                            state=state,
                            category=FailureCategory.MODEL_AUTHORING_FAILURE,
                            message=json.dumps(terminal, default=str),
                            best_code=best_code,
                            last_validation=last_validation,
                        ),
                    },
                    goto=END,
                )

            if repair_guidance is not None:
                appended.append(HumanMessage(content=repair_guidance["message"]))

            if result.get("status") == "needs_user":
                question = str(result.get("question") or "The model needs more information.")
                return Command(
                    update={
                        "tool_call_count": tool_call_count,
                        "messages": appended,
                        "best_code": best_code,
                        "last_validation": last_validation,
                        "current_group_index": current_group_index,
                        "last_accepted_source_hash": last_accepted_source_hash,
                        "result": _build_clarification(
                            registry=self.registry,
                            state=state,
                            key="model_clarification_request",
                            question=question,
                            best_code=best_code,
                            last_validation=last_validation,
                        ),
                    },
                    goto=END,
                )

            if name == "compile_and_simulate":
                last_validation = self.helpers._report_from_tool_result(result)
                source = self.registry.calls[-1]["arguments"].get("source")
                if isinstance(source, str):
                    best_code = source
                if result.get("ok") is True:
                    if not _workflow_complete(self.registry, current_group_index):
                        appended.append(_workflow_next_group_message(self.registry, current_group_index))
                        continue
                    missing = _missing_grounding(self.registry)
                    if missing:
                        appended.append(_grounding_nudge_message(missing))
                        continue
                    gaps = _coverage_gaps(result)
                    if gaps and adherence_nudges < ADHERENCE_NUDGE_BUDGET:
                        adherence_nudges += 1
                        fallback_result = _build_success(
                            registry=self.registry, state=state, code=best_code,
                            tool_result=result, last_validation=last_validation,
                            coverage_gaps=gaps,
                        )
                        appended.append(_adherence_nudge_message(gaps))
                        continue
                    return Command(
                        update={
                            "tool_call_count": tool_call_count,
                            "messages": appended,
                            "best_code": best_code,
                            "last_validation": last_validation,
                            "current_group_index": current_group_index,
                            "last_accepted_source_hash": last_accepted_source_hash,
                            "adherence_nudges": adherence_nudges,
                            "result": _build_success(
                                registry=self.registry,
                                state=state,
                                code=best_code,
                                tool_result=result,
                                last_validation=last_validation,
                                coverage_gaps=gaps,
                            ),
                        },
                        goto=END,
                    )

            if name == "simulate_python_draft" and result.get("ok") is True:
                # Prefer an autogrounded source (e.g. pipetting volume literals
                # rewritten to their approved variable) over the model's raw
                # draft, so compile_and_simulate runs the corrected version.
                source = result.get("source") or self.registry.calls[-1]["arguments"].get("source")
                if isinstance(source, str):
                    best_code = source
                    last_accepted_source_hash = _source_hash(source)
                    current_group_index = _advance_workflow_index(self.registry, current_group_index, source=source)
                    if not _workflow_complete(self.registry, current_group_index):
                        appended.append(_workflow_next_group_message(self.registry, current_group_index))
                        continue
                    compile_result = _consume_speculative_compile(
                        registry=self.registry,
                        source=source,
                        speculative_compiles=speculative_compiles,
                    )
                    last_validation = self.helpers._report_from_tool_result(compile_result)
                    appended.append(HumanMessage(content=(
                        "I ran compile_and_simulate on the draft after successful simulation. "
                        f"Result: {json.dumps(compile_result, default=str)}"
                    )))
                    if compile_result.get("ok") is True:
                        missing = _missing_grounding(self.registry)
                        if missing:
                            appended.append(_grounding_nudge_message(missing))
                            continue
                        gaps = _coverage_gaps(compile_result)
                        if gaps and adherence_nudges < ADHERENCE_NUDGE_BUDGET:
                            adherence_nudges += 1
                            fallback_result = _build_success(
                                registry=self.registry, state=state, code=best_code,
                                tool_result=compile_result, last_validation=last_validation,
                                coverage_gaps=gaps,
                            )
                            appended.append(_adherence_nudge_message(gaps))
                            continue
                        return Command(
                            update={
                                "tool_call_count": tool_call_count,
                                "messages": appended,
                                "best_code": best_code,
                                "last_validation": last_validation,
                                "current_group_index": current_group_index,
                                "last_accepted_source_hash": last_accepted_source_hash,
                                "adherence_nudges": adherence_nudges,
                                "result": _build_success(
                                    registry=self.registry,
                                    state=state,
                                    code=best_code,
                                    tool_result=compile_result,
                                    last_validation=last_validation,
                                    coverage_gaps=gaps,
                                ),
                            },
                            goto=END,
                        )

        # End of tool-call loop — apply draft-pressure nudge if applicable
        # and fall back to model_call.
        if not _gates_off(self.registry):
            from .service import _draft_pressure_message
            pressure = _draft_pressure_message(
                tuple(self.registry.calls),
                iteration=state.get("iterations", 0),
            )
            if pressure is not None:
                appended.append(_to_lc_message(pressure))
        return Command(
            update={
                "tool_call_count": tool_call_count,
                "messages": appended,
                "best_code": best_code,
                "last_validation": last_validation,
                "current_group_index": current_group_index,
                "last_accepted_source_hash": last_accepted_source_hash,
                "adherence_nudges": adherence_nudges,
                "fallback_result": fallback_result,
            },
            goto="model_call",
        )

    # ── extract_python (no-tool branch) ──────────────────────────

    def extract_python(self, state: GraphState) -> Command:
        last = state["messages"][-1]
        content = (getattr(last, "content", "") or "").strip()
        code = self.helpers._extract_python(content)

        if code is None:
            if _looks_like_question(content):
                return Command(
                    update={
                        "result": _build_clarification(
                            registry=self.registry,
                            state=state,
                            key="model_question",
                            question=content,
                            best_code=state.get("best_code"),
                            last_validation=state.get("last_validation"),
                        ),
                    },
                    goto=END,
                )
            # Skills staged drafting: a turn with neither a tool call nor a fenced
            # draft is the model "thinking out loud" mid-plan. Re-nudge with the
            # current group's instruction instead of aborting the whole run; the
            # (staging-scaled) iteration budget remains the hard backstop. Scoped
            # to skills so enforce/default keep their immediate-failure behavior.
            _scope = getattr(self.registry, "lab_scope", None)
            if _scope is not None and _scope.mode == "skills":
                return Command(
                    update={
                        "messages": [_workflow_next_group_message(
                            self.registry, state.get("current_group_index", 0)
                        )],
                    },
                    goto="model_call",
                )
            return Command(
                update={
                    "result": _build_failure(
                        registry=self.registry,
                        state=state,
                        category=FailureCategory.MODEL_AUTHORING_FAILURE,
                        message=content or "Model returned no Python draft.",
                        best_code=state.get("best_code"),
                        last_validation=state.get("last_validation"),
                    ),
                },
                goto=END,
            )

        approval_block = _approval_stage_block(
            registry=self.registry,
            tool_name="python_draft",
        )
        if approval_block is not None:
            return Command(
                update={
                    "best_code": code,
                    "messages": [HumanMessage(content=approval_block["message"])],
                },
                goto="model_call",
            )

        missing = _missing_grounding(self.registry)
        if missing:
            return Command(
                update={
                    "best_code": code,
                    "messages": [_grounding_nudge_message(missing)],
                },
                goto="model_call",
            )

        if _requires_workflow_declaration(self.registry) and self.registry.workflow_plan is None:
            return Command(
                update={
                    "best_code": code,
                    "messages": [_workflow_next_group_message(self.registry, state.get("current_group_index", 0))],
                },
                goto="model_call",
            )

        return Command(update={"best_code": code}, goto="validate_locally")

    # ── validate_locally ─────────────────────────────────────────

    def validate_locally(self, state: GraphState) -> Command:
        code = state["best_code"]
        iteration = state.get("iterations", 1)
        validation = self.validator.validate(
            code,
            output_dir=self.output_dir,
            stem=f"lm_authoring_final_attempt{iteration}",
            attempt_index=iteration,
            prompt=state.get("prompt", ""),
        )
        if validation.success:
            gaps = _coverage_gaps_for_source(state.get("prompt", ""), code)
            success = AuthoringResult(
                status=AuthoringStatus.SUCCESS,
                prompt=state.get("prompt", ""),
                spec=None,
                generated_code=code,
                validation=validation,
                compiled_xscr=validation.xscr_path,
                attempts=iteration,
                tool_calls=tuple(self.registry.calls),
                coverage_gaps=tuple(gaps),
            )
            adherence_nudges = state.get("adherence_nudges", 0)
            if gaps and adherence_nudges < ADHERENCE_NUDGE_BUDGET:
                return Command(
                    update={
                        "last_validation": validation,
                        "best_code": code,
                        "adherence_nudges": adherence_nudges + 1,
                        # Keep this clean draft as the accept-with-gaps fallback.
                        "fallback_result": success,
                        "messages": [_adherence_nudge_message(gaps)],
                    },
                    goto="model_call",
                )
            return Command(
                update={"last_validation": validation, "result": success},
                goto=END,
            )
        repair_msg = HumanMessage(content=(
            "The final Python draft failed deterministic validation. "
            "Repair it and call simulate_python_draft then compile_and_simulate. "
            f"Validation report: {json.dumps(validation.to_dict(), default=str)}"
        ))
        return Command(
            update={
                "last_validation": validation,
                "messages": [repair_msg],
            },
            goto="model_call",
        )


# ── Concurrency orchestration ───────────────────────────────────────

def _dispatch_tools_with_concurrency(nodes: "_Nodes", state: GraphState) -> Command:
    """Pre-launch parallel-safe lookups + speculative compiles, then run the
    existing serial dispatch_tools body with futures threaded through.

    The body relies on `parallel_futures` and `speculative_compiles` being
    consulted via `_resolve_dispatch` and `_consume_speculative_compile`.
    Pool ownership is local to this call: futures still in flight after the
    body returns are abandoned (their results discarded). Catalog lookups
    are short SQLite reads, so this leak is bounded and harmless.
    """
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None) or []

    cfg = nodes.concurrency
    pool: ThreadPoolExecutor | None = None
    parallel_futures: dict[int, Future[dict[str, Any]]] = {}
    speculative_compiles: dict[str, Future[dict[str, Any]]] = {}

    if (cfg.parallel_tool_dispatch or cfg.speculative_compile) and tool_calls:
        try:
            pool = ThreadPoolExecutor(
                max_workers=max(2, cfg.worker_pool_size),
                thread_name_prefix="authoring-parallel",
            )
        except RuntimeError:
            pool = None

    if pool is not None and cfg.parallel_tool_dispatch:
        cache_hits: list[str] = []
        for idx, call in enumerate(tool_calls):
            name = _tool_call_name(call)
            if name not in PARALLEL_SAFE_TOOLS:
                continue
            payload = nodes.registry._parse_arguments(_tool_call_args(call))
            if nodes.registry.cache_lookup(name, payload) is not None:
                cache_hits.append(name)
                continue
            parallel_futures[idx] = pool.submit(
                nodes.registry._dispatch_pure, name, payload
            )
        if parallel_futures or cache_hits:
            import sys as _sys
            _msg_parts = []
            if parallel_futures:
                _names = [_tool_call_name(tool_calls[i]) for i in parallel_futures]
                _msg_parts.append(f"{len(parallel_futures)} parallel: {_names}")
            if cache_hits:
                _msg_parts.append(f"{len(cache_hits)} cache-hit: {cache_hits}")
            print(
                f"[parallel] dispatch: {', '.join(_msg_parts)}",
                file=_sys.stderr,
                flush=True,
            )

    if pool is not None and cfg.speculative_compile:
        for call in tool_calls:
            if _tool_call_name(call) != "simulate_python_draft":
                continue
            payload = nodes.registry._parse_arguments(_tool_call_args(call))
            source = payload.get("source")
            if not isinstance(source, str):
                continue
            shash = _source_hash(source)
            if shash in speculative_compiles:
                continue
            speculative_compiles[shash] = pool.submit(
                nodes.registry._dispatch_pure,
                "compile_and_simulate",
                {"source": source},
            )

    try:
        return nodes._dispatch_tools_serial_body(
            state,
            parallel_futures=parallel_futures,
            speculative_compiles=speculative_compiles,
        )
    finally:
        for fut in parallel_futures.values():
            fut.cancel()
        for fut in speculative_compiles.values():
            fut.cancel()
        if pool is not None:
            pool.shutdown(wait=False)


def _run_orchestrator_grounding_if_ready(nodes: "_Nodes") -> dict[str, Any] | None:
    cfg = nodes.concurrency
    if not cfg.orchestrator_grounding:
        return None
    registry = nodes.registry
    # enforce mode: the curated whitelist is the only grounding source —
    # never auto-fan catalog-search subagents.
    _scope = getattr(registry, "lab_scope", None)
    if _scope is not None and _scope.enforces:
        return None
    client = getattr(registry, "_subagent_client", None)
    if client is None:
        return None
    intent = registry.current_intent
    if not all(
        value is not None
        for value in (
            intent.target_volume_ul,
            intent.source_label,
            intent.destination_label,
            intent.destination_wells,
            intent.liquid_class,
        )
    ):
        return None
    from .grounding_coordinator import GroundingCoordinator

    return GroundingCoordinator(
        registry=registry,
        client=client,
        pool_size=cfg.worker_pool_size,
        timeout_s=getattr(registry, "_subagent_timeout_s", 240.0),
    ).run(registry.authoring_context())


_SUPERSEDED_STUB = json.dumps({"superseded": True})


def _supersede_prior_drafts(name: str, messages: list[BaseMessage]) -> None:
    """A re-presented draft/plan supersedes every prior echo of the same
    kind. Those stale payloads carry no decision value once a newer draft
    exists but are re-sent verbatim every turn — replace their body with a
    tiny stub (the ToolMessage structure / `tool_call_id` is preserved so
    the transcript stays well-formed). Only the newest payload is kept
    verbatim. Mutates the message objects in place.
    """
    for msg in messages:
        if (
            isinstance(msg, ToolMessage)
            and getattr(msg, "name", None) == name
            and msg.content != _SUPERSEDED_STUB
        ):
            msg.content = _SUPERSEDED_STUB


def _resolve_dispatch(
    *,
    registry: AuthoringToolRegistry,
    name: str,
    arguments: Any,
    index: int,
    parallel_futures: "dict[int, Future[dict[str, Any]]]",
    speculative_compiles: "dict[str, Future[dict[str, Any]]]",
) -> dict[str, Any]:
    """Run a tool, preferring (in order): prefetch cache, parallel future,
    speculative-compile future, fresh dispatch.

    Always records the call on the registry's `calls` log so the rest of the
    authoring code can introspect tool history as if `dispatch` had run.
    """
    payload = registry._parse_arguments(arguments)

    if name in PARALLEL_SAFE_TOOLS:
        t0 = time.monotonic()
        cached = registry.cache_lookup(name, payload)
        if cached is not None:
            return registry._record_call(
                name,
                payload,
                cached,
                elapsed_ms=(time.monotonic() - t0) * 1000.0,
                dispatch_source="cache",
            )
        future = parallel_futures.pop(index, None)
        if future is not None:
            try:
                result = future.result()
            except Exception as exc:
                result = {"ok": False, "category": "tool_error", "message": str(exc)}
            registry.cache_store(name, payload, result)
            return registry._record_call(
                name,
                payload,
                result,
                elapsed_ms=(time.monotonic() - t0) * 1000.0,
                dispatch_source="parallel",
            )
        # Cache miss + no pre-launch (concurrency disabled): run directly.
        result = registry._dispatch_pure(name, payload)
        registry.cache_store(name, payload, result)
        return registry._record_call(
            name,
            payload,
            result,
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
            dispatch_source="live",
        )

    if name == "compile_and_simulate":
        source = payload.get("source")
        if isinstance(source, str):
            shash = _source_hash(source)
            future = speculative_compiles.pop(shash, None)
            if future is not None:
                t0 = time.monotonic()
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"ok": False, "category": "tool_error", "message": str(exc)}
                return registry._record_call(
                    name,
                    payload,
                    result,
                    elapsed_ms=(time.monotonic() - t0) * 1000.0,
                    dispatch_source="speculative",
                )

    # Fallback: serial dispatch (preserves all existing behavior).
    return registry.dispatch(name, arguments)


def _consume_speculative_compile(
    *,
    registry: AuthoringToolRegistry,
    source: str,
    speculative_compiles: "dict[str, Future[dict[str, Any]]]",
) -> dict[str, Any]:
    """Used by the inline simulate→compile path inside dispatch_tools.

    Same semantics as `registry.dispatch("compile_and_simulate", {"source": source})`,
    but consumes a speculative future when one exists for this source.
    """
    shash = _source_hash(source)
    future = speculative_compiles.pop(shash, None)
    payload = {"source": source}
    if future is not None:
        t0 = time.monotonic()
        try:
            result = future.result()
        except Exception as exc:
            result = {"ok": False, "category": "tool_error", "message": str(exc)}
        return registry._record_call(
            "compile_and_simulate",
            payload,
            result,
            elapsed_ms=(time.monotonic() - t0) * 1000.0,
            dispatch_source="speculative",
        )
    return registry.dispatch("compile_and_simulate", payload)


# ── Helpers ──────────────────────────────────────────────────────────

def _missing_grounding(registry: AuthoringToolRegistry) -> list[str]:
    # Enforce: all grounding lives in the cheatsheet/reference doc — no tool
    # call is mandatory before accepting a draft.
    if _gates_off(registry):
        return []
    from .service import missing_authoring_grounding
    _scope = getattr(registry, "lab_scope", None)
    return missing_authoring_grounding(
        tuple(registry.calls),
        lab_scope_enforces=bool(_scope is not None and _scope.enforces),
    )


def _grounding_nudge_message(missing: list[str]) -> HumanMessage:
    from .service import _missing_grounding_message
    return _to_lc_message(_missing_grounding_message(missing))


# Soft-gate budget: how many times the loop nudges the model to close
# source-document coverage gaps before accepting the protocol with the gaps
# attached. The `max_iterations` budget remains the hard backstop.
ADHERENCE_NUDGE_BUDGET = 3


def _prefer_fallback(
    result: AuthoringResult | None, fallback: AuthoringResult | None
) -> AuthoringResult | None:
    """Choose the terminal result, applying accept-with-gaps.

    If the run failed *after* an adherence nudge dropped a cleanly-compiling
    draft, return that draft (with its gaps surfaced) rather than failing —
    nudging must never make a usable protocol worse. Returns None only when both
    are None (caller builds a defensive failure).
    """
    if result is None:
        return fallback
    if result.status is AuthoringStatus.FAILURE and fallback is not None:
        return fallback
    return result


def _coverage_gaps(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    """Gating source-document coverage gaps from a compile/simulate result."""
    from .document_adherence import coverage_gaps
    return coverage_gaps((tool_result or {}).get("document_adherence"))


def _coverage_gaps_for_source(prompt: str, code: str | None) -> list[dict[str, Any]]:
    """Compute gating coverage gaps directly from prompt + code.

    Used on the direct-Python (validate_locally) path, which produces a
    ValidationReport rather than the compile tool result that carries an
    adherence report. Only runs when the prompt carries attached source text.
    """
    if not code or "Attached file context:" not in (prompt or ""):
        return []
    from .document_adherence import coverage_gaps, document_adherence_report
    report = document_adherence_report(
        source_text=prompt or "",
        protocol_source=code,
        source_name="attached file context",
        approved_plan=None,
    )
    return coverage_gaps(report)


def _adherence_nudge_message(gaps: list[dict[str, Any]]) -> HumanMessage:
    lines = "\n".join(
        f"- {gap.get('message') or gap.get('code')}" for gap in gaps
    )
    return HumanMessage(content=(
        "The protocol simulates, but it does not cover every stage the source "
        "document describes. Each of these library-prep stages is missing:\n"
        f"{lines}\n\n"
        "Cover each one — either author it on-deck where the deck allows, or add "
        "an explicit `wt.add_comment(...)` stating that stage is performed "
        "manually/off-deck. Do not silently drop any stage. Then re-run "
        "compile_and_simulate."
    ))


def _to_lc_message(raw: dict[str, Any]) -> BaseMessage:
    role = raw.get("role")
    content = raw.get("content") or ""
    if role == "user":
        return HumanMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=raw.get("tool_call_id") or "",
            name=raw.get("name"),
        )
    return HumanMessage(content=content)


def _looks_like_question(content: str) -> bool:
    if not content:
        return False
    lowered = content.lower()
    if "?" in content:
        return True
    return bool(re.search(
        r"\b(please clarify|need to know|which|what|how many|provide|confirm)\b",
        lowered,
    ))


def _source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _gates_off(registry: AuthoringToolRegistry) -> bool:
    """Enforce mode removes every pre-simulation gate (grounding, intent,
    object-draft / functional-group approval, staged-group workflow,
    draft-pressure). The model writes the complete protocol in one pass and
    is judged solely by simulate_python_draft / compile_and_simulate."""
    scope = getattr(registry, "lab_scope", None)
    return bool(scope is not None and scope.enforces)


def _workflow_groups(registry: AuthoringToolRegistry) -> list[str]:
    plan = getattr(registry, "workflow_plan", None)
    if plan is None:
        return []
    return [group.name for group in plan.groups]


_SCAFFOLD_GROUPS = ("Variables", "Labware Placement")
# Stage names that signal a protocol whose late stages carry order- and
# volume-dependent detail the one-shot draft tends to botch — bead cleanups,
# washes, elutions. Any match forces staged drafting in skills mode.
_STAGE_TRIGGER_RE = re.compile(r"bead|clean|elut|wash|magnet|spri|ampure", re.IGNORECASE)


def _requires_workflow_declaration(registry: AuthoringToolRegistry) -> bool:
    """True for every active mode except ``enforce``.

    Enforce writes the whole protocol in one pass with no plan; every other mode
    (off/cheatsheet/skills) declares the ordered functional groups before
    drafting. (``skills`` then stages only when the plan is multi-stage — see
    :func:`_should_stage`.)
    """
    scope = getattr(registry, "lab_scope", None)
    return not (scope is not None and scope.mode == "enforce")


def _should_stage(registry: AuthoringToolRegistry) -> bool:
    """Whether to enforce per-group checkpoints for the declared plan.

    off/cheatsheet always stage (preserved baseline). ``skills`` stages only when
    the plan is genuinely multi-stage — ≥3 non-scaffold groups, or any group that
    names a bead/cleanup/wash/elution stage — so simple protocols still one-shot
    after the (cheap) declaration.
    """
    scope = getattr(registry, "lab_scope", None)
    if scope is None or scope.mode != "skills":
        return True
    non_scaffold = [g for g in _workflow_groups(registry) if g not in _SCAFFOLD_GROUPS]
    if len(non_scaffold) >= 3:
        return True
    return any(_STAGE_TRIGGER_RE.search(g) for g in non_scaffold)


def _workflow_complete(registry: AuthoringToolRegistry, current_group_index: int) -> bool:
    # Enforce: the single full-source draft is always "complete" — there is no
    # staged group plan, so a passing simulate goes straight to compile.
    if not _requires_workflow_declaration(registry):
        return True
    groups = _workflow_groups(registry)
    if not groups:
        # Declaration required but not yet made — not complete (must declare).
        return False
    if not getattr(registry, "staged_drafting", False):
        # Declared but simple (skills): one full-source pass is "complete".
        return True
    return current_group_index >= len(groups)


def _advance_workflow_index(
    registry: AuthoringToolRegistry,
    current_group_index: int,
    source: str | None = None,
) -> int:
    groups = _workflow_groups(registry)
    if not groups:
        return current_group_index
    if source:
        group_call_count = len(re.findall(r"wt\.group\(\s*['\"]([^'\"]+)['\"]\s*\)", source))
        return min(len(groups), max(current_group_index, 1 + group_call_count))
    if current_group_index < 2:
        return min(2, len(groups))
    return min(current_group_index + 1, len(groups))


def _workflow_next_group_message(registry: AuthoringToolRegistry, current_group_index: int) -> HumanMessage:
    groups = _workflow_groups(registry)
    if not groups:
        return HumanMessage(content=(
            "Before drafting Python, call declare_protocol_workflow with the ordered "
            "functional groups. The first two must be Variables and Labware Placement."
        ))
    if current_group_index < 2:
        plan = registry.workflow_plan
        variables = [variable.name for variable in (plan.variables if plan else ())]
        return HumanMessage(content=(
            "Draft the first simulator checkpoint now: Variables plus Labware Placement. "
            "Declare variables with wt.declare_variable and wt.set_sim_value before any wt.group. "
            "Then call wt.group('Labware Placement'), place all planned labware, and call "
            "simulate_python_draft. Planned variables: "
            + ", ".join(variables)
        ))
    if current_group_index < len(groups):
        group_name = groups[current_group_index]
        return HumanMessage(content=(
            f"The checkpoint through `{groups[current_group_index - 1]}` passed. "
            f"Extend the same draft with the next functional group only: `{group_name}`. "
            "Keep prior accepted code, submit the changed full build_worktable source, "
            "and call simulate_python_draft."
        ))
    return HumanMessage(content=(
        "All functional groups have passed draft simulation. Call compile_and_simulate "
        "on the final full source."
    ))


def _workflow_stage_block(
    *,
    registry: AuthoringToolRegistry,
    tool_name: str,
    arguments: dict[str, Any],
    current_group_index: int,
) -> dict[str, Any] | None:
    if not _requires_workflow_declaration(registry):
        return None  # enforce: one-shot, no staging gate
    if tool_name not in {"simulate_python_draft", "compile_and_simulate"}:
        return None
    groups = _workflow_groups(registry)
    if not groups:
        return {
            "ok": False,
            "category": "workflow_plan_required",
            "message": (
                "Before drafting or compiling, call declare_protocol_workflow. "
                "The first two functional groups must be `Variables` and `Labware Placement`."
            ),
        }
    if not getattr(registry, "staged_drafting", False):
        # Declared but simple (skills): allow the full-source draft in one pass.
        return None
    if tool_name == "compile_and_simulate" and not _workflow_complete(registry, current_group_index):
        return {
            "ok": False,
            "category": "workflow_stage_incomplete",
            "current_group_index": current_group_index,
            "groups": groups,
            "message": (
                "Do not call compile_and_simulate yet. Continue staged drafting with "
                f"`{groups[current_group_index] if current_group_index < len(groups) else groups[-1]}` "
                "and call simulate_python_draft first."
            ),
        }
    if tool_name != "simulate_python_draft":
        return None
    source = arguments.get("source")
    if not isinstance(source, str) or not source.strip():
        return {
            "ok": False,
            "category": "workflow_stage_contract",
            "message": "simulate_python_draft requires the full changed Python source for the current group.",
        }
    error = _check_workflow_source_stage(source, groups, current_group_index)
    if error is not None:
        return {
            "ok": False,
            "category": "workflow_stage_contract",
            "current_group_index": current_group_index,
            "groups": groups,
            "message": error,
        }
    return None


def _approval_stage_block(
    *,
    registry: AuthoringToolRegistry,
    tool_name: str,
) -> dict[str, Any] | None:
    if _gates_off(registry):
        return None
    if tool_name == "ask_user":
        return None
    if tool_name in {
        "lookup_workspace",
        "list_valid_positions",
        "search_labware",
        "get_labware",
        "lookup_liquid_class",
        "lookup_compatibility",
        "lookup_rules",
        "lookup_api",
        "plan_protocol_resources",
        "suggest_deck_layout",
        "ground_in_parallel",
        "declare_intent",
        "present_source_protocol_plan",
    }:
        return None
    if registry.requires_source_protocol_plan() and not registry.source_protocol_plan_approved:
        return {
            "ok": False,
            "category": "source_protocol_approval_required",
            "message": (
                "Before object planning or Python drafting from attached file context, "
                "call present_source_protocol_plan and wait for user approval."
            ),
        }
    if tool_name == "present_object_draft":
        return None
    if not registry.object_draft_approved:
        return {
            "ok": False,
            "category": "object_approval_required",
            "message": (
                "Before functional-group planning or Python drafting, present the "
                "worktable object draft with present_object_draft and wait for user approval."
            ),
        }
    if tool_name == "present_functional_group_plan":
        return None
    if not registry.functional_group_plan_approved:
        return {
            "ok": False,
            "category": "functional_group_approval_required",
            "message": (
                "Before drafting Python, present the ordered functional groups with "
                "present_functional_group_plan and wait for user approval."
            ),
        }
    return None


def _check_workflow_source_stage(source: str, groups: list[str], current_group_index: int) -> str | None:
    first_group_match = re.search(r"wt\.group\(\s*['\"]([^'\"]+)['\"]\s*\)", source)
    first_group_start = first_group_match.start() if first_group_match else -1
    first_place = source.find("wt.place(")
    first_declare = source.find("wt.declare_variable(")
    first_sim_value = source.find("wt.set_sim_value(")
    if first_declare < 0:
        return "The Variables phase must declare at least one variable with wt.declare_variable before labware placement."
    if first_sim_value < 0:
        return "Each staged draft must seed simulator variable values with wt.set_sim_value before labware placement."
    if first_group_start < 0:
        return "The first executable group must be wt.group('Labware Placement')."
    if first_declare > first_group_start or first_sim_value > first_group_start:
        return "Variables must be declared and seeded before any wt.group call."
    if first_group_match.group(1) != "Labware Placement":
        return "The first executable group after variables must be wt.group('Labware Placement')."
    if first_place < 0 or first_place < first_group_start:
        return "Labware placement must happen inside the Labware Placement group."

    expected_count = max(0, max(2, current_group_index + 1) - 1)
    group_calls = re.findall(r"wt\.group\(\s*['\"]([^'\"]+)['\"]\s*\)", source)
    if len(group_calls) < expected_count:
        return (
            f"Current staged draft has {len(group_calls)} wt.group(...) call(s); "
            f"expected at least {expected_count} after the Variables phase. "
            "Extend the draft with the next functional group and call simulate_python_draft."
        )
    return None


def _tool_call_name(call: Any) -> str:
    if isinstance(call, dict):
        return call.get("name", "")
    return getattr(call, "name", "")


def _tool_call_args(call: Any) -> dict[str, Any]:
    if isinstance(call, dict):
        args = call.get("args") or call.get("arguments") or {}
    else:
        args = getattr(call, "args", None) or getattr(call, "arguments", None) or {}
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return dict(args)


def _tool_call_id(call: Any) -> str | None:
    if isinstance(call, dict):
        return call.get("id")
    return getattr(call, "id", None)


def _missing_method_recipe_guidance(
    *,
    result: dict[str, Any],
    arguments: dict[str, Any],
    prompt: str,
) -> dict[str, Any] | None:
    failure = _failure_payload(result)
    category = _failure_category_from_result(result, failure)
    if category != "missing_method":
        return None
    object_name = _failure_value(failure, "object") or _failure_value(failure, "object_key")
    method = _failure_value(failure, "method") or _failure_value(failure, "missing_method")
    source = arguments.get("source")
    snippet = _source_snippet(source, method)
    from ..catalog import get_database, retrieve_dsl_recipes

    normalized_object = _normalize_failure_object(object_name)
    recipes = retrieve_dsl_recipes(
        get_database(),
        " ".join(
            part
            for part in (normalized_object, method or "", category, snippet or "", prompt)
            if part
        ),
        object_key=normalized_object,
        failure_category=category,
        bad_method=method,
        context_text=snippet or prompt,
        limit=4,
    )
    if not recipes:
        return None
    lines = [
        "You must repair this exact API misuse.",
        f"Failure: `{object_name or 'unknown object'}` has no method `{method or 'unknown'}`.",
    ]
    if snippet:
        lines.append(f"Source draft snippet: `{snippet}`")
    lines.append("Use these wrong/right DSL recipes:")
    for recipe in recipes:
        if recipe.get("bad_pattern"):
            lines.append(f"- Wrong: `{recipe['bad_pattern']}`")
        for good in recipe.get("good_patterns", [])[:4]:
            lines.append(f"- Right: `{good}`")
    lines.append(
        "Submit a changed draft and call simulate_python_draft. "
        "Do not repeat the same source unchanged."
    )
    return {"message": "\n".join(lines), "recipes": recipes}


def _failure_payload(result: dict[str, Any]) -> dict[str, Any]:
    failure = result.get("failure")
    if isinstance(failure, dict):
        return failure
    details = result.get("simulation_failure_details")
    if isinstance(details, dict):
        return details
    return result


def _failure_category_from_result(result: dict[str, Any], failure: dict[str, Any]) -> str | None:
    category = (
        failure.get("category")
        or result.get("simulation_failure_category")
        or result.get("category")
    )
    return str(category) if category else None


def _failure_value(failure: dict[str, Any], key: str) -> str | None:
    value = failure.get(key)
    details = failure.get("details")
    if value is None and isinstance(details, dict):
        value = details.get(key)
    return str(value) if value is not None else None


def _normalize_failure_object(object_name: str | None) -> str | None:
    if object_name is None:
        return None
    lowered = object_name.lower()
    aliases = {
        "gripper": "wt.gripper",
        "liha": "wt.liha",
        "mca96head": "wt.mca96",
        "mca96": "wt.mca96",
        "plate96": "labware",
        "trough100ml": "labware",
        "trough25ml": "labware",
        "labware": "labware",
    }
    return aliases.get(lowered, lowered)


def _source_snippet(source: Any, method: str | None) -> str | None:
    if not isinstance(source, str) or not method:
        return None
    pattern = re.compile(rf"^.*\.{re.escape(method)}\s*\(.*$", re.MULTILINE)
    match = pattern.search(source)
    if match:
        return match.group(0).strip()[:300]
    return None


# ── Model-trace helpers ──────────────────────────────────────────────

def _client_attr(client: Any, name: str) -> Any:
    value = getattr(client, name, None)
    if value is not None:
        return str(value)
    inner = getattr(client, "_legacy", None)
    if inner is not None:
        value = getattr(inner, name, None)
        if value is not None:
            return str(value)
    return None


def _message_to_trace_dict(message: BaseMessage) -> dict[str, Any]:
    if isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, ToolMessage):
        role = "tool"
    elif isinstance(message, AIMessage):
        role = "assistant"
    else:
        role = str(getattr(message, "type", type(message).__name__))
    out: dict[str, Any] = {
        "role": role,
        "content": getattr(message, "content", None),
    }
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        out["tool_calls"] = list(tool_calls)
    if isinstance(message, ToolMessage):
        out["tool_call_id"] = message.tool_call_id
        out["name"] = message.name
    response_metadata = getattr(message, "response_metadata", None) or {}
    if response_metadata:
        out["response_metadata"] = response_metadata
    additional_kwargs = getattr(message, "additional_kwargs", None) or {}
    reasoning = {
        key: value
        for key, value in additional_kwargs.items()
        if "reasoning" in key.lower() and value not in (None, "")
    }
    if reasoning:
        out["reasoning_fields"] = reasoning
    return out


# ── Legacy-client adapter ────────────────────────────────────────────

class LegacyClientAdapter:
    """Adapt an old-style `LMStudioChatClient` (`.complete(messages, tools)`)
    so it presents the LangChain `bind_tools` / `invoke` shape the graph needs.

    Existing tests (`tests/test_authoring_session.py::FakeClient`) pre-date the
    LangChain migration and supply raw OpenAI-style dict responses. Wrapping
    those clients here keeps the test contract unchanged.

    `bind_tools(tools)` returns a sibling adapter whose `invoke` filters the
    legacy `tool_definitions()` payload down to the bound names — this lets
    category-agent subagents send a restricted toolset to LM Studio.
    """

    def __init__(self, legacy_client: Any, *, bound_names: "frozenset[str] | None" = None) -> None:
        self._legacy = legacy_client
        self._bound_names = bound_names

    def bind_tools(self, tools: Any) -> "LegacyClientAdapter":
        names: set[str] = set()
        for t in tools or ():
            n = getattr(t, "name", None)
            if isinstance(n, str):
                names.add(n)
        if not names:
            # Empty/unknown shape — preserve the original "send everything"
            # contract for the graph's own invocations.
            return LegacyClientAdapter(self._legacy)
        return LegacyClientAdapter(self._legacy, bound_names=frozenset(names))

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        from .tools import tool_definitions
        defs = tool_definitions()
        if self._bound_names is not None:
            defs = [
                d for d in defs
                if d.get("function", {}).get("name") in self._bound_names
            ]
        raw_messages = [_lc_to_legacy_dict(m) for m in messages]
        response = self._legacy.complete(messages=raw_messages, tools=defs)
        return _legacy_dict_to_aimessage(response)


def _lc_to_legacy_dict(message: BaseMessage) -> dict[str, Any]:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "content": message.content,
        }
    if isinstance(message, AIMessage):
        out: dict[str, Any] = {"role": "assistant", "content": message.content}
        tool_calls = list(message.tool_calls or [])
        if tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(tc.get("args") or {}),
                    },
                }
                for tc in tool_calls
            ]
        return out
    return {"role": "user", "content": str(getattr(message, "content", message))}


def _legacy_dict_to_aimessage(d: dict[str, Any]) -> AIMessage:
    raw_tool_calls = d.get("tool_calls") or []
    lc_tool_calls: list[dict[str, Any]] = []
    for tc in raw_tool_calls:
        fn = tc.get("function") or {}
        args_str = fn.get("arguments") or "{}"
        if isinstance(args_str, str):
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
        else:
            args = dict(args_str)
        lc_tool_calls.append({
            "name": fn.get("name", ""),
            "args": args,
            "id": tc.get("id") or "",
        })
    return AIMessage(content=d.get("content") or "", tool_calls=lc_tool_calls)


def adapt_client(client: Any) -> Any:
    """Return a bind_tools/invoke-compatible client.

    Detects the legacy `.complete(...)`-only shape and wraps it; passes
    LangChain `BaseChatModel` instances through unchanged.
    """
    if hasattr(client, "invoke") and hasattr(client, "bind_tools"):
        return client
    if hasattr(client, "complete"):
        return LegacyClientAdapter(client)
    return client


# ── Result-builder helpers ───────────────────────────────────────────

def _build_failure(
    *,
    registry: AuthoringToolRegistry,
    state: GraphState,
    category: FailureCategory,
    message: str,
    best_code: str | None = None,
    last_validation: Any = None,
) -> AuthoringResult:
    last_validation = last_validation if last_validation is not None else state.get("last_validation")
    return AuthoringResult(
        status=AuthoringStatus.FAILURE,
        prompt=state.get("prompt", ""),
        spec=None,
        generated_code=None,
        validation=last_validation,
        compiled_xscr=last_validation.xscr_path if last_validation is not None else None,
        failure_category=category,
        failure_message=message,
        best_draft_code=best_code if best_code is not None else state.get("best_code"),
        attempts=state.get("iterations", 0),
        tool_calls=tuple(registry.calls),
    )


def _build_clarification(
    *,
    registry: AuthoringToolRegistry,
    state: GraphState,
    key: str,
    question: str,
    best_code: str | None = None,
    last_validation: Any = None,
) -> AuthoringResult:
    last_validation = last_validation if last_validation is not None else state.get("last_validation")
    return AuthoringResult(
        status=AuthoringStatus.CLARIFICATION_REQUIRED,
        prompt=state.get("prompt", ""),
        spec=None,
        generated_code=None,
        validation=last_validation,
        compiled_xscr=last_validation.xscr_path if last_validation is not None else None,
        clarification_questions=(
            ClarificationQuestion(
                key=key,
                question=question,
                why_it_matters="The model needs this information before it can safely author the protocol.",
            ),
        ),
        best_draft_code=best_code if best_code is not None else state.get("best_code"),
        attempts=state.get("iterations", 0),
        tool_calls=tuple(registry.calls),
    )


def _build_approval(
    *,
    registry: AuthoringToolRegistry,
    state: GraphState,
    approval: dict[str, Any],
    best_code: str | None = None,
    last_validation: Any = None,
) -> AuthoringResult:
    last_validation = last_validation if last_validation is not None else state.get("last_validation")
    request = ApprovalRequest(
        kind=str(approval.get("kind") or "approval"),
        title=str(approval.get("title") or "Approval Required"),
        summary=str(approval.get("summary") or ""),
        payload=dict(approval.get("payload") or {}),
        question=str(approval.get("question") or "Approve this plan, or reply with changes."),
    )
    return AuthoringResult(
        status=AuthoringStatus.APPROVAL_REQUIRED,
        prompt=state.get("prompt", ""),
        spec=None,
        generated_code=None,
        validation=last_validation,
        compiled_xscr=last_validation.xscr_path if last_validation is not None else None,
        approval_request=request,
        best_draft_code=best_code if best_code is not None else state.get("best_code"),
        attempts=state.get("iterations", 0),
        tool_calls=tuple(registry.calls),
    )


def _build_success(
    *,
    registry: AuthoringToolRegistry,
    state: GraphState,
    code: str | None,
    tool_result: dict[str, Any],
    last_validation: Any,
    coverage_gaps: list[dict[str, Any]] | None = None,
) -> AuthoringResult:
    return AuthoringResult(
        status=AuthoringStatus.SUCCESS,
        prompt=state.get("prompt", ""),
        spec=None,
        generated_code=code,
        validation=last_validation,
        compiled_xscr=Path(tool_result["xscr_path"]) if tool_result.get("xscr_path") else None,
        attempts=state.get("iterations", 0),
        tool_calls=tuple(registry.calls),
        coverage_gaps=tuple(coverage_gaps or ()),
    )
