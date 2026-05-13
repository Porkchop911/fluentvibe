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

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from .lc_tools import make_lc_tools
from .models import ApprovalRequest, AuthoringResult, AuthoringStatus, ClarificationQuestion, FailureCategory
from .repair_lock import RepairLockState
from .tools import AuthoringToolRegistry
from .validator import AuthoringValidator


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
    lc_tools = make_lc_tools(registry)
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
) -> AuthoringResult:
    """Compile and run the graph; return the terminal AuthoringResult."""
    graph = build_authoring_graph(
        registry=registry,
        client=client,
        output_dir=output_dir,
        retry_budget=retry_budget,
    )
    if initial_state is None:
        registry.current_prompt = prompt
        messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        if initial_messages:
            messages.extend(initial_messages)
        messages.append(HumanMessage(content=prompt))

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
        )

    final_state: GraphState = graph.invoke(initial_state)
    result = final_state.get("result")
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

    # Intent-axis nudge — runs once at the start of each `invoke`. Kept as a
    # node (not in run_graph) so multi-turn callers re-evaluate per send().
    def intent_nudge(self, state: GraphState) -> dict[str, Any]:
        # Already prepended by run_graph for the first turn; this node exists
        # so the graph has a canonical entry seam. No-op when invoked.
        return {}

    def model_call(self, state: GraphState) -> dict[str, Any]:
        iterations = state.get("iterations", 0) + 1
        if iterations > self.max_iterations:
            return {
                "iterations": iterations,
                "result": _build_failure(
                    registry=self.registry,
                    state=state,
                    category=FailureCategory.RETRY_BUDGET_EXHAUSTED,
                    message="Model authoring loop exhausted its iteration budget.",
                ),
            }
        try:
            response = self.client_with_tools.invoke(state["messages"])
        except Exception as exc:
            return {
                "iterations": iterations,
                "result": _build_failure(
                    registry=self.registry,
                    state=state,
                    category=FailureCategory.MODEL_AUTHORING_FAILURE,
                    message=str(exc),
                ),
            }
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
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        appended: list[BaseMessage] = []
        tool_call_count = state.get("tool_call_count", 0)
        best_code = state.get("best_code")
        last_validation = state.get("last_validation")
        current_group_index = state.get("current_group_index", 0)
        last_accepted_source_hash = state.get("last_accepted_source_hash")

        for call in tool_calls:
            tool_call_count += 1
            if tool_call_count > self.max_tool_calls:
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

            result = self.registry.dispatch(name, arguments)
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
            if name == "declare_protocol_workflow" and result.get("ok") is True:
                appended.append(ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=tool_call_id,
                    name=name,
                ))
                appended.append(_workflow_next_group_message(self.registry, current_group_index))
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
                    return Command(
                        update={
                            "tool_call_count": tool_call_count,
                            "messages": appended,
                            "best_code": best_code,
                            "last_validation": last_validation,
                            "current_group_index": current_group_index,
                            "last_accepted_source_hash": last_accepted_source_hash,
                            "result": _build_success(
                                registry=self.registry,
                                state=state,
                                code=best_code,
                                tool_result=result,
                                last_validation=last_validation,
                            ),
                        },
                        goto=END,
                    )

            if name == "simulate_python_draft" and result.get("ok") is True:
                source = self.registry.calls[-1]["arguments"].get("source")
                if isinstance(source, str):
                    best_code = source
                    last_accepted_source_hash = _source_hash(source)
                    current_group_index = _advance_workflow_index(self.registry, current_group_index)
                    if not _workflow_complete(self.registry, current_group_index):
                        appended.append(_workflow_next_group_message(self.registry, current_group_index))
                        continue
                    compile_result = self.registry.dispatch(
                        "compile_and_simulate", {"source": source}
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
                        return Command(
                            update={
                                "tool_call_count": tool_call_count,
                                "messages": appended,
                                "best_code": best_code,
                                "last_validation": last_validation,
                                "current_group_index": current_group_index,
                                "last_accepted_source_hash": last_accepted_source_hash,
                                "result": _build_success(
                                    registry=self.registry,
                                    state=state,
                                    code=best_code,
                                    tool_result=compile_result,
                                    last_validation=last_validation,
                                ),
                            },
                            goto=END,
                        )

        # End of tool-call loop — apply draft-pressure nudge if applicable
        # and fall back to model_call.
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

        if self.registry.workflow_plan is None:
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
            return Command(
                update={
                    "last_validation": validation,
                    "result": AuthoringResult(
                        status=AuthoringStatus.SUCCESS,
                        prompt=state.get("prompt", ""),
                        spec=None,
                        generated_code=code,
                        validation=validation,
                        compiled_xscr=validation.xscr_path,
                        attempts=iteration,
                        tool_calls=tuple(self.registry.calls),
                    ),
                },
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


# ── Helpers ──────────────────────────────────────────────────────────

def _missing_grounding(registry: AuthoringToolRegistry) -> list[str]:
    from .service import missing_authoring_grounding
    return missing_authoring_grounding(tuple(registry.calls))


def _grounding_nudge_message(missing: list[str]) -> HumanMessage:
    from .service import _missing_grounding_message
    return _to_lc_message(_missing_grounding_message(missing))


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


def _workflow_groups(registry: AuthoringToolRegistry) -> list[str]:
    plan = getattr(registry, "workflow_plan", None)
    if plan is None:
        return []
    return [group.name for group in plan.groups]


def _workflow_complete(registry: AuthoringToolRegistry, current_group_index: int) -> bool:
    groups = _workflow_groups(registry)
    return bool(groups) and current_group_index >= len(groups)


def _advance_workflow_index(registry: AuthoringToolRegistry, current_group_index: int) -> int:
    groups = _workflow_groups(registry)
    if not groups:
        return current_group_index
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
    if tool_name == "ask_user":
        return None
    if tool_name in {
        "lookup_workspace",
        "list_valid_positions",
        "search_labware",
        "get_labware",
        "lookup_liquid_class",
        "lookup_rules",
        "lookup_api",
        "plan_protocol_resources",
        "suggest_deck_layout",
        "declare_intent",
        "present_object_draft",
    }:
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

    expected = groups[: max(2, current_group_index + 1)]
    present = set(re.findall(r"wt\.group\(\s*['\"]([^'\"]+)['\"]\s*\)", source))
    missing = [name for name in expected[1:] if name not in present]
    if missing:
        return "Current staged draft is missing required group(s): " + ", ".join(missing)
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


# ── Legacy-client adapter ────────────────────────────────────────────

class LegacyClientAdapter:
    """Adapt an old-style `LMStudioChatClient` (`.complete(messages, tools)`)
    so it presents the LangChain `bind_tools` / `invoke` shape the graph needs.

    Existing tests (`tests/test_authoring_session.py::FakeClient`) pre-date the
    LangChain migration and supply raw OpenAI-style dict responses. Wrapping
    those clients here keeps the test contract unchanged.
    """

    def __init__(self, legacy_client: Any) -> None:
        self._legacy = legacy_client

    def bind_tools(self, tools: Any) -> "LegacyClientAdapter":
        # The legacy client always sends `tool_definitions()` directly; the
        # StructuredTool list is irrelevant to it.
        return self

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        from .tools import tool_definitions
        raw_messages = [_lc_to_legacy_dict(m) for m in messages]
        response = self._legacy.complete(messages=raw_messages, tools=tool_definitions())
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
    )
