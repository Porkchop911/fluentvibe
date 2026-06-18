"""Multi-turn prompt-authoring session, backed by a LangGraph state machine.

The previous hand-rolled while-loop has been replaced with `build_authoring_graph`
in `fluentvibe.authoring.graph`. This file is now a thin coordinator: it owns the
accumulated message history and per-session state, then delegates each `send()`
call to the graph.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from .graph import (
    AuthoringConcurrencyConfig,
    GraphState,
    _prefer_fallback,
    _to_lc_message,
    adapt_client,
    build_authoring_graph,
)
from .grounding import CURRENT_WORKTABLE_ENV
from .lab_scope import load_lab_scope
from .lm_client import (
    DEFAULT_LM_STUDIO_ENDPOINT,
    DEFAULT_LM_STUDIO_MODEL,
    LMStudioChatClient,
)
from .models import (
    ApprovalRequest,
    AuthoringResult,
    AuthoringStatus,
    ClarificationQuestion,
    FailureCategory,
)
from .service import (
    SYSTEM_PROMPT,
    PromptAuthoringService,
    _intent_axis_message,
    _missing_intent_axes,
)
from .tools import AuthoringToolRegistry
from .trace import ModelTraceConfig, ModelTraceRecorder
from .validator import AuthoringValidator


class PromptAuthoringSession:
    """Stateful LM authoring loop for terminal chat use."""

    def __init__(
        self,
        *,
        output_dir: Path,
        retry_budget: int = 8,
        workspace_name: str | None = None,
        workspace_guid: str | None = None,
        endpoint: str = DEFAULT_LM_STUDIO_ENDPOINT,
        model: str = DEFAULT_LM_STUDIO_MODEL,
        client: Any | None = None,
        concurrency: AuthoringConcurrencyConfig | None = None,
        trace_config: ModelTraceConfig | None = None,
        lab_scope: str | None = None,
        profile_dir: Path | str | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.retry_budget = retry_budget
        # A workspace-app profile (explicit, or FLUENTVIBE_PROFILE_DIR by
        # default) drives the deck skill + whitelist and the grounding snapshot.
        _profile = None
        if profile_dir is not None:
            from .profile import resolve_profile

            _profile = resolve_profile(profile_dir)
            os.environ.setdefault(
                CURRENT_WORKTABLE_ENV, str(_profile.current_worktable)
            )
            workspace_name = workspace_name or _profile.workspace_name
            workspace_guid = workspace_guid or _profile.workspace_guid
        self._lab_scope = (
            load_lab_scope(lab_scope, profile=_profile)
            if _profile is not None
            else load_lab_scope(lab_scope)
        )
        self._trace = ModelTraceRecorder(
            trace_config
            if trace_config is not None
            else ModelTraceConfig.from_env(output_dir=output_dir)
        )
        raw_client = (
            client
            if client is not None
            else LMStudioChatClient(endpoint=endpoint, model=model, trace_recorder=self._trace)
        )
        if hasattr(raw_client, "trace_recorder"):
            raw_client.trace_recorder = self._trace
        self._client = adapt_client(raw_client)
        self._registry = AuthoringToolRegistry(
            output_dir=output_dir,
            workspace_name=workspace_name,
            workspace_guid=workspace_guid,
        )
        # Narrowed-scope experiment: inert unless --lab-scope/env is set.
        # Stored on the registry so the Lever-B tool filter can consult it.
        self._registry.lab_scope = self._lab_scope
        self._validator = AuthoringValidator()
        self._helpers = PromptAuthoringService.__new__(PromptAuthoringService)
        self._messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
        # skills mode selects its context from the prompt, which isn't known
        # until the first send(); defer injection (see _inject_skill_context).
        # off/cheatsheet/enforce have static context, so inject it now.
        self._skill_msg_pending = self._lab_scope.mode == "skills"
        if not self._skill_msg_pending:
            _scope_text = self._lab_scope.as_context_message()
            if _scope_text is not None:
                self._messages.append(SystemMessage(content=_scope_text))
        self._user_turns: list[str] = []
        self._original_prompt: str = ""
        self._best_code: str | None = None
        self._last_validation: Any | None = None
        self._iterations: int = 0
        self._pending_approval_kind: str | None = None
        self._concurrency = concurrency or AuthoringConcurrencyConfig()
        self._prefetcher = None
        self._prefetch_done = False
        # Track whether the intent-axis nudge has been added to the
        # message history yet. Re-adding it on every clarification turn
        # made the LM re-ask the same questions even after the user had
        # already answered them in the original prompt.
        self._intent_nudge_added = False
        # Wire the LM client into the registry so the new
        # `ground_in_parallel` tool can fan out subagents on demand.
        # The main agent calls it AFTER clarifications, not before.
        self._registry.configure_subagent_client(
            self._client,
            pool_size=self._concurrency.worker_pool_size,
            timeout_s=240.0,
        )

    @property
    def tool_calls(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._registry.calls)

    # Compat alias for any code that still pokes at the registry directly.
    @property
    def _tools(self) -> AuthoringToolRegistry:
        return self._registry

    def _inject_skill_context(self, prompt: str) -> None:
        """Select + inject the skills-mode context on the first turn.

        No-op unless skills mode is pending. Runs the LM pre-pass against the
        first prompt and inserts the assembled context right after the system
        prompt, mirroring where static cheatsheets land for the other modes.
        """
        if not self._skill_msg_pending:
            return
        self._skill_msg_pending = False
        from .lab_skills import build_initial_scope_message

        scope_text = build_initial_scope_message(self._lab_scope, prompt, self._client)
        if scope_text is not None:
            self._messages.insert(1, SystemMessage(content=scope_text))

    def send(self, user_text: str) -> AuthoringResult:
        text = user_text.strip()
        if not text:
            return self._clarification(
                "empty_prompt", "What protocol would you like me to author?"
            )

        if not self._user_turns:
            self._original_prompt = text
            self._inject_skill_context(text)
        self._user_turns.append(text)
        self._trace.start_turn(len(self._user_turns))
        history_text = "\n".join(self._user_turns)
        self._registry.set_authoring_context(
            original_prompt=self._original_prompt,
            latest_user_text=text,
            user_history_text=history_text,
        )
        if self._pending_approval_kind is not None:
            if _looks_like_approval(text):
                self._registry.approve_pending(self._pending_approval_kind)
                self._messages.append(HumanMessage(content=text))
                self._messages.append(HumanMessage(content=(
                    f"The user approved the `{self._pending_approval_kind}` checkpoint. "
                    "Continue to the next cooperative authoring phase."
                )))
                self._pending_approval_kind = None
            else:
                self._registry.reopen_pending(self._pending_approval_kind)
                self._messages.append(HumanMessage(content=text))
                self._messages.append(HumanMessage(content=(
                    f"The user requested changes to the `{self._pending_approval_kind}` checkpoint. "
                    "Revise that checkpoint and present it again for approval before moving forward."
                )))
        else:
            self._messages.append(HumanMessage(content=text))
        # Inject the intent-axis nudge AT MOST ONCE per session. The check
        # runs against the union of every prior user message so a volume
        # mentioned in turn 1 still counts when turn 3 only says "yes".
        if not self._intent_nudge_added and not self._registry.current_intent.is_specified():
            missing_axes = _missing_intent_axes(history_text)
            if missing_axes:
                self._messages.append(_to_lc_message(_intent_axis_message(missing_axes)))
            self._intent_nudge_added = True

        self._start_prefetch(history_text)

        graph = build_authoring_graph(
            registry=self._registry,
            client=self._client,
            output_dir=self.output_dir,
            retry_budget=self.retry_budget,
            validator=self._validator,
            helpers=self._helpers,
            concurrency=self._concurrency,
            trace_recorder=self._trace,
        )

        initial_state: GraphState = {
            "messages": list(self._messages),
            "iterations": 0,
            "tool_call_count": 0,
            "best_code": self._best_code,
            "last_validation": self._last_validation,
            "current_group_index": 0,
            "last_accepted_source_hash": None,
            "result": None,
            "prompt": self._registry.current_prompt or history_text,
            "adherence_nudges": 0,
            "fallback_result": None,
        }

        final_state = graph.invoke(initial_state)
        self._messages = list(final_state.get("messages") or self._messages)
        self._best_code = final_state.get("best_code", self._best_code)
        self._last_validation = final_state.get("last_validation", self._last_validation)
        self._iterations += int(final_state.get("iterations", 0))

        # Accept-with-gaps: if the run failed after an adherence nudge dropped a
        # cleanly-compiling draft, return that draft (with its gaps) instead of
        # failing. run_graph (CLI path) applies the same rule; the session path
        # must too, or the fallback never reaches the webapp.
        result = _prefer_fallback(
            final_state.get("result"), final_state.get("fallback_result")
        )
        if result is None:
            return self._failure(
                FailureCategory.MODEL_AUTHORING_FAILURE,
                "Graph terminated without a result.",
            )
        result = self._normalize_pending_approval_result(result)
        if result.status is AuthoringStatus.APPROVAL_REQUIRED and result.approval_request is not None:
            self._pending_approval_kind = result.approval_request.kind
        return result

    def validate_fluentcontrol_shell(self, xscr_path: str) -> dict[str, Any]:
        return self._registry.validate_fluentcontrol_shell(xscr_path=xscr_path)

    def _start_prefetch(self, prompt: str) -> None:
        # Run the deterministic (SQLite-only) prefetch ONCE per session,
        # on the first send. Clarification turns are short answers like
        # "20ul" — re-running prefetch on those is wasted work, and the
        # main authoring agent now controls LM-driven grounding via the
        # `ground_in_parallel` tool.
        if self._prefetch_done:
            return
        cfg = self._concurrency
        if not cfg.prefetch_deterministic:
            self._prefetch_done = True
            return
        from .prefetch import GroundingPrefetcher, PrefetchConfig

        if self._prefetcher is None:
            self._prefetcher = GroundingPrefetcher(
                registry=self._registry,
                config=PrefetchConfig(
                    deterministic=True,
                    subagent=False,
                    pool_size=max(4, cfg.worker_pool_size),
                ),
            )
        self._prefetcher.start(prompt)
        self._prefetch_done = True

    def close(self) -> None:
        if self._prefetcher is not None:
            self._prefetcher.shutdown()
            self._prefetcher = None

    # ── Internal terminal-result builders (for the empty-prompt path) ────

    def _latest_user_prompt(self) -> str:
        if self._user_turns:
            return self._user_turns[-1]
        for message in reversed(self._messages):
            if isinstance(message, HumanMessage):
                return str(message.content or "")
        return ""

    def _clarification(self, key: str, question: str) -> AuthoringResult:
        return AuthoringResult(
            status=AuthoringStatus.CLARIFICATION_REQUIRED,
            prompt=self._latest_user_prompt(),
            spec=None,
            generated_code=None,
            validation=self._last_validation,
            compiled_xscr=(
                self._last_validation.xscr_path
                if self._last_validation is not None
                else None
            ),
            clarification_questions=(
                ClarificationQuestion(
                    key=key,
                    question=question,
                    why_it_matters=(
                        "The model needs this information before it can safely "
                        "author the protocol."
                    ),
                ),
            ),
            best_draft_code=self._best_code,
            attempts=self._iterations,
            tool_calls=self.tool_calls,
        )

    def _failure(self, category: FailureCategory, message: str) -> AuthoringResult:
        return AuthoringResult(
            status=AuthoringStatus.FAILURE,
            prompt=self._latest_user_prompt(),
            spec=None,
            generated_code=None,
            validation=self._last_validation,
            compiled_xscr=(
                self._last_validation.xscr_path
                if self._last_validation is not None
                else None
            ),
            failure_category=category,
            failure_message=message,
            best_draft_code=self._best_code,
            attempts=self._iterations,
            tool_calls=self.tool_calls,
        )

    def _normalize_pending_approval_result(self, result: AuthoringResult) -> AuthoringResult:
        if result.status is AuthoringStatus.APPROVAL_REQUIRED:
            return result
        pending = self._registry.pending_approval_kind
        if pending == "source_protocol" and self._registry.source_protocol_plan is not None:
            plan = self._registry.source_protocol_plan
            return AuthoringResult(
                status=AuthoringStatus.APPROVAL_REQUIRED,
                prompt=result.prompt,
                spec=result.spec,
                generated_code=result.generated_code,
                validation=result.validation,
                compiled_xscr=result.compiled_xscr,
                clarification_questions=result.clarification_questions,
                approval_request=ApprovalRequest(
                    kind="source_protocol",
                    title="Approve Source Protocol Plan",
                    summary=str(plan.get("summary") or ""),
                    payload=plan,
                    question=(
                        "Approve this extraction of the source document, or reply "
                        "with missing/incorrect steps."
                    ),
                ),
                best_draft_code=result.best_draft_code,
                attempts=result.attempts,
                tool_calls=result.tool_calls,
            )
        if pending == "objects" and self._registry.object_draft is not None:
            draft = self._registry.object_draft
            return AuthoringResult(
                status=AuthoringStatus.APPROVAL_REQUIRED,
                prompt=result.prompt,
                spec=result.spec,
                generated_code=result.generated_code,
                validation=result.validation,
                compiled_xscr=result.compiled_xscr,
                clarification_questions=result.clarification_questions,
                approval_request=ApprovalRequest(
                    kind="objects",
                    title="Approve Worktable Objects",
                    summary=str(draft.get("summary") or ""),
                    payload=draft,
                    question="Approve these worktable objects, or reply with changes.",
                ),
                best_draft_code=result.best_draft_code,
                attempts=result.attempts,
                tool_calls=result.tool_calls,
            )
        if pending == "functional_groups" and self._registry.workflow_plan is not None:
            plan = self._registry.workflow_plan
            return AuthoringResult(
                status=AuthoringStatus.APPROVAL_REQUIRED,
                prompt=result.prompt,
                spec=result.spec,
                generated_code=result.generated_code,
                validation=result.validation,
                compiled_xscr=result.compiled_xscr,
                clarification_questions=result.clarification_questions,
                approval_request=ApprovalRequest(
                    kind="functional_groups",
                    title="Approve Functional Groups",
                    summary=plan.summary,
                    payload=plan.to_dict(),
                    question="Approve this functional group plan, or reply with changes.",
                ),
                best_draft_code=result.best_draft_code,
                attempts=result.attempts,
                tool_calls=result.tool_calls,
            )
        return result


def _looks_like_approval(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    approval_phrases = (
        "yes",
        "approve",
        "approved",
        "looks good",
        "go ahead",
        "continue",
        "confirmed",
        "confirm",
        "ok",
        "okay",
    )
    change_phrases = ("change", "instead", "but", "except", "revise", "modify", "use ")
    return any(phrase in lowered for phrase in approval_phrases) and not any(
        phrase in lowered for phrase in change_phrases
    )
