"""Robustness to small-model tool-call slips.

Local models routinely misname tool args (``message`` for ``question``),
omit required ones, or mangle catalog names. These should be recoverable —
a clean error the model can self-correct from — not a hard crash or a
premature give-up.
"""

from __future__ import annotations

import tempfile

import pytest

from fluentvibe.authoring.repair_lock import RepairLockState
from fluentvibe.authoring.tools import AuthoringToolRegistry, reconcile_tool_args
from fluentvibe.catalog.catalog import index_exists, suggest_names


def _registry() -> AuthoringToolRegistry:
    return AuthoringToolRegistry(output_dir=tempfile.mkdtemp())


# ── arg reconciliation ────────────────────────────────────────────────

def test_reconcile_drops_unknown_keys():
    reg = _registry()
    out = reconcile_tool_args(reg.functions()["ask_user"], {"question": "v?", "junk": 1})
    assert out == {"question": "v?"}


def test_reconcile_aliases_common_slips():
    reg = _registry()
    fns = reg.functions()
    assert reconcile_tool_args(fns["ask_user"], {"message": "v?"}) == {"question": "v?"}
    assert set(reconcile_tool_args(fns["simulate_python_draft"], {"code": "x=1"})) == {"source"}


def test_reconcile_does_not_overwrite_supplied_canonical():
    reg = _registry()
    out = reconcile_tool_args(reg.functions()["ask_user"], {"question": "real", "message": "alias"})
    assert out == {"question": "real"}


# ── dispatch recovers instead of crashing ─────────────────────────────

def test_dispatch_recovers_aliased_arg():
    reg = _registry()
    result = reg.dispatch("ask_user", {"message": "What volume?"})
    assert result["ok"] is True
    assert result.get("question") == "What volume?"


def test_dispatch_missing_required_is_clean_and_actionable():
    reg = _registry()
    result = reg.dispatch("simulate_python_draft", {})  # no `source`
    assert result["ok"] is False
    assert result["category"] == "bad_tool_arguments"
    # the error names the accepted args so the model can self-correct
    assert "source" in result["message"]


# ── repair lock: malformed calls don't trigger give-up ────────────────

def test_repeated_bad_tool_arguments_does_not_give_up():
    lock = RepairLockState()
    bad = {"ok": False, "category": "bad_tool_arguments", "message": "missing source"}
    assert lock.observe_tool_result("simulate_python_draft", {}, bad) is None
    assert lock.observe_tool_result("simulate_python_draft", {}, bad) is None


def test_repeated_real_draft_failure_still_gives_up():
    lock = RepairLockState()
    sim = {"ok": False, "category": "strict_simulation", "message": "tip capacity"}
    assert lock.observe_tool_result("simulate_python_draft", {"source": "same"}, sim) is None
    stop = lock.observe_tool_result("simulate_python_draft", {"source": "same"}, sim)
    assert stop is not None and stop["category"] == "strict_simulation"


# ── catalog name suggestion ───────────────────────────────────────────

@pytest.mark.skipif(not index_exists(), reason="requires FC catalog index")
def test_suggest_names_corrects_separator_slip():
    out = suggest_names("FCA_200ul_SBS")
    assert "FCA, 200ul SBS" in out
