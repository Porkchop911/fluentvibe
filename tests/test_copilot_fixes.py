"""Copilot Phase 3: deterministic quick-fixes attached to diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.copilot import analyze_file  # noqa: E402
from fluentvibe.copilot.fixes import compute_fixes  # noqa: E402

_NO_ADAPTER = '''\
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable(name="No adapter")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Transfer")
    head = wt.mca96
    head.pick_up(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    return wt
'''


def _line_of(source: str, needle: str) -> int:
    return next(i for i, t in enumerate(source.splitlines(), start=1) if needle in t)


def test_compute_fixes_adapter_state_inserts_mount_adapter() -> None:
    fixes = compute_fixes("adapter_state", 1, "simulate", ["    head.pick_up(tips)"])
    assert len(fixes) == 1
    assert fixes[0].kind == "insert_before"
    assert fixes[0].line == 1
    assert fixes[0].text == "    head.mount_adapter()"
    assert "mount_adapter" in fixes[0].title


def test_compute_fixes_uses_full_receiver_chain() -> None:
    fixes = compute_fixes("adapter_state", 2, "simulate", ["x", "        wt.mca96.aspirate(s, 5)"])
    assert fixes[0].text == "        wt.mca96.mount_adapter()"


def test_compute_fixes_none_for_build_source_or_other_category() -> None:
    assert compute_fixes("adapter_state", 1, "build", ["    head.pick_up(t)"]) == []
    assert compute_fixes("source_volume_short", 1, "simulate", ["    head.aspirate(s, 5)"]) == []


def test_compute_fixes_missing_method_suggests_closest() -> None:
    fixes = compute_fixes("missing_method", 1, "build", ["    head.aspirat(src, 20.0)"])
    assert len(fixes) == 1
    assert fixes[0].kind == "replace_line"
    assert fixes[0].text == "    head.aspirat(src, 20.0)".replace("aspirat(", "aspirate(")
    assert "aspirate" in fixes[0].title


def test_compute_fixes_missing_method_none_when_no_close_match() -> None:
    # A method nothing on the class resembles -> no guess.
    assert compute_fixes("missing_method", 1, "build", ["    head.zzzzzzz(x)"]) == []
    # An unknown receiver -> no class -> no fix.
    assert compute_fixes("missing_method", 1, "build", ["    nope.aspirat(x)"]) == []


def test_compute_fixes_runtime_variable_inserts_set_sim_value() -> None:
    msg = "No sim-time value for runtime variable 'TARGET_VOLUME_UL'. Call `wt.set_sim_value(...)`."
    fixes = compute_fixes(
        "runtime_variable", 2, "simulate", ["x", "    head.aspirate(s, TARGET_VOLUME_UL)"], msg
    )
    assert len(fixes) == 1
    assert fixes[0].kind == "insert_before"
    assert fixes[0].line == 2
    assert fixes[0].text.startswith('    wt.set_sim_value("TARGET_VOLUME_UL", ')
    # Without the variable name in the message, there is nothing to seed.
    assert compute_fixes("runtime_variable", 2, "simulate", ["x", "    y"], "no name here") == []


def test_analyzer_attaches_mount_adapter_fix(tmp_path: Path) -> None:
    path = tmp_path / "no_adapter.py"
    path.write_text(_NO_ADAPTER, encoding="utf-8")
    diags = analyze_file(path)
    assert len(diags) == 1
    d = diags[0]
    assert d.code == "adapter_state"
    assert d.line == _line_of(_NO_ADAPTER, "head.pick_up(")
    assert len(d.fixes) == 1
    fix = d.fixes[0]
    assert fix.text == "    head.mount_adapter()"
    assert fix.line == d.line
    # The fix is serialized for the CLI/LSP boundary.
    assert d.to_dict()["fixes"][0]["text"] == "    head.mount_adapter()"
