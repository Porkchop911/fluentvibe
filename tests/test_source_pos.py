"""Phase 0: Python source-position capture for IR steps.

Proves the one new copilot primitive — that an authoring call's editor line is
attached to the emitted step, and that a simulator failure can be mapped back to
that line. See docs/copilot-design.md.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import (  # noqa: E402
    InsufficientVolumeError,
    MCA100Box,
    Plate96,
    Reagent,
    Worktable,
)
from fluentvibe.ir.schema import AspirateStep  # noqa: E402
from fluentvibe.ir.source_pos import capture_source_pos  # noqa: E402

THIS_FILE = Path(__file__).resolve()


def _here() -> int:
    """Return the line number of the caller (the line invoking _here)."""
    return inspect.currentframe().f_back.f_lineno


def _build_worktable() -> tuple[Worktable, object, object, object]:
    wt = Worktable(name="SourcePos test")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    return wt, src, dst, tips


def _iter_steps(protocol):
    for group in protocol.groups:
        yield from group.steps


def test_capture_source_pos_points_at_caller() -> None:
    expected = _here() + 1
    pos = capture_source_pos()
    assert pos is not None
    assert Path(pos.file).resolve() == THIS_FILE
    assert pos.line == expected


def test_capture_skips_framework_frames() -> None:
    """A step emitted from inside an authoring method attributes to user code,
    not to the fluentvibe internals that actually called _emit."""
    wt, src, dst, tips = _build_worktable()
    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    aspirate_line = _here() + 1
    wt.mca96.aspirate(src, 10.0, liquid_class="Water Free Single")

    aspirates = [s for s in _iter_steps(wt.to_protocol()) if isinstance(s, AspirateStep)]
    assert len(aspirates) == 1
    pos = aspirates[0].source_pos
    assert pos is not None
    assert Path(pos.file).resolve() == THIS_FILE
    assert pos.line == aspirate_line


def test_source_pos_is_excluded_from_serialization() -> None:
    """source_pos is an in-memory editor aid: it must never leak into the IR
    dump (and therefore can't affect rendering or round-trip parity)."""
    wt, src, dst, tips = _build_worktable()
    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    wt.mca96.aspirate(src, 10.0, liquid_class="Water Free Single")

    aspirate = next(s for s in _iter_steps(wt.to_protocol()) if isinstance(s, AspirateStep))
    assert aspirate.source_pos is not None  # present in memory
    assert "source_pos" not in aspirate.model_dump()  # absent from serialization


def test_simulation_failure_carries_source_pos() -> None:
    """End-to-end: a simulator failure maps back to the exact authoring line."""
    wt, src, dst, tips = _build_worktable()
    src.fill_all(Reagent("Buffer"), 5.0)  # only 5 µL — aspirate of 20 will fail
    wt.group("Pipette")
    wt.mca96.mount_adapter()
    wt.mca96.pick_up(tips)
    aspirate_line = _here() + 1
    wt.mca96.aspirate(src, 20.0, liquid_class="Water Free Single")

    with pytest.raises(InsufficientVolumeError):
        wt.simulate()

    failure = wt.simulation_report.failure
    assert failure is not None
    assert failure.source_pos is not None
    assert Path(failure.source_pos["file"]).resolve() == THIS_FILE
    assert failure.source_pos["line"] == aspirate_line
    # The serialized failure (what an LSP server would emit) carries it too.
    assert failure.to_dict()["source_pos"]["line"] == aspirate_line
