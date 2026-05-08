"""Repair-lock state for structured authoring failures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


_CATEGORY_ALLOWED_TOOLS: dict[str, set[str]] = {
    "source_volume_short": {"plan_protocol_resources", "simulate_python_draft", "compile_and_simulate"},
    "well_overflow": {"plan_protocol_resources", "simulate_python_draft", "compile_and_simulate"},
    "tip_capacity": {
        "plan_protocol_resources",
        "search_labware",
        "get_labware",
        "simulate_python_draft",
        "compile_and_simulate",
    },
    "missing_method": {"lookup_api", "simulate_python_draft", "compile_and_simulate"},
    "slot_occupied": {"suggest_deck_layout", "simulate_python_draft", "compile_and_simulate"},
}


@dataclass
class RepairLockState:
    category: str | None = None
    last_source_hash: str | None = None
    repeated_no_progress_count: int = 0

    def block_reason(self, tool_name: str) -> str | None:
        if self.category is None:
            return None
        allowed = _CATEGORY_ALLOWED_TOOLS.get(self.category)
        if allowed is None or tool_name in allowed:
            return None
        return (
            f"Repair lock is active for `{self.category}`. Do not call `{tool_name}` now. "
            f"Use one of: {', '.join(sorted(allowed))}."
        )

    def observe_tool_result(self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
        if tool_name not in {"simulate_python_draft", "compile_and_simulate"}:
            return None
        if result.get("ok") is True:
            self.category = None
            self.last_source_hash = None
            self.repeated_no_progress_count = 0
            return None

        category = _failure_category(result)
        if not category:
            return None

        source_hash = _source_hash(arguments.get("source"))
        if category == self.category and source_hash == self.last_source_hash:
            self.repeated_no_progress_count += 1
        else:
            self.category = category
            self.last_source_hash = source_hash
            self.repeated_no_progress_count = 1

        if self.repeated_no_progress_count >= 2:
            failure = result.get("failure") if isinstance(result.get("failure"), dict) else {}
            details = result.get("simulation_failure_details") if isinstance(result.get("simulation_failure_details"), dict) else {}
            payload = failure or details
            return {
                "category": category,
                "message": (
                    f"Repeated `{category}` failure without a meaningful draft change. "
                    "Stopping before exhausting retries."
                ),
                "object": _nested_value(payload, "object"),
                "method": _nested_value(payload, "method"),
                "retrieved_recipes": result.get("retrieved_recipes") or [],
                "last_draft_source_hash": source_hash,
                "repair_options": result.get("repair_options") or [],
                "draft_path": result.get("python_path") or result.get("draft_path"),
                "resource_plan_required": category in {"source_volume_short", "well_overflow", "tip_capacity"},
            }
        return None


def _failure_category(result: dict[str, Any]) -> str | None:
    category = result.get("simulation_failure_category") or result.get("category")
    failure = result.get("failure")
    if isinstance(failure, dict):
        category = failure.get("category") or category
    return str(category) if category else None


def _source_hash(source: Any) -> str | None:
    if not isinstance(source, str):
        return None
    return hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()


def _nested_value(payload: dict[str, Any], key: str) -> Any:
    if not isinstance(payload, dict):
        return None
    if key in payload:
        return payload.get(key)
    details = payload.get("details")
    if isinstance(details, dict):
        return details.get(key)
    return None
