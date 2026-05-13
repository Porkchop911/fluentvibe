"""Multi-turn prompt-authoring session, backed by a LangGraph state machine.

The previous hand-rolled while-loop has been replaced with `build_authoring_graph`
in `fluentvibe.authoring.graph`. This file is now a thin coordinator: it owns the
accumulated message history and per-session state, then delegates each `send()`
call to the graph.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from .graph import GraphState, _to_lc_message, adapt_client, build_authoring_graph
from .lm_client import (
    DEFAULT_LM_STUDIO_ENDPOINT,
    DEFAULT_LM_STUDIO_MODEL,
    LMStudioChatClient,
    make_chat_client,
)
from .models import ApprovalRequest, AuthoringResult, AuthoringStatus, ClarificationQuestion, FailureCategory
from .service import (
    SYSTEM_PROMPT,
    PromptAuthoringService,
    _intent_axis_message,
    _missing_intent_axes,
)
from .tools import AuthoringToolRegistry
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
    ) -> None:
        self.output_dir = output_dir
        self.retry_budget = retry_budget
        self._client = adapt_client(
            client
            if client is not None
            else LMStudioChatClient(endpoint=endpoint, model=model)
        )
        self._registry = AuthoringToolRegistry(
            output_dir=output_dir,
            workspace_name=workspace_name,
            workspace_guid=workspace_guid,
        )
        self._validator = AuthoringValidator()
        self._helpers = PromptAuthoringService.__new__(PromptAuthoringService)
        self._messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
        self._best_code: str | None = None
        self._last_validation: Any | None = None
        self._iterations: int = 0
        self._pending_approval_kind: str | None = None

    @property
    def tool_calls(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._registry.calls)

    # Compat alias for any code that still pokes at the registry directly.
    @property
    def _tools(self) -> AuthoringToolRegistry:
        return self._registry

    def send(self, user_text: str) -> AuthoringResult:
        text = user_text.strip()
        if not text:
            return self._clarification(
                "empty_prompt", "What protocol would you like me to author?"
            )

        self._registry.current_prompt = text
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
        missing_axes = _missing_intent_axes(text)
        if missing_axes and not self._registry.current_intent.is_specified():
            self._messages.append(_to_lc_message(_intent_axis_message(missing_axes)))

        graph = build_authoring_graph(
            registry=self._registry,
            client=self._client,
            output_dir=self.output_dir,
            retry_budget=self.retry_budget,
            validator=self._validator,
            helpers=self._helpers,
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
            "prompt": text,
        }

        final_state = graph.invoke(initial_state)
        self._messages = list(final_state.get("messages") or self._messages)
        self._best_code = final_state.get("best_code", self._best_code)
        self._last_validation = final_state.get("last_validation", self._last_validation)
        self._iterations += int(final_state.get("iterations", 0))

        result = final_state.get("result")
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

    # ── Internal terminal-result builders (for the empty-prompt path) ────

    def _latest_user_prompt(self) -> str:
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
