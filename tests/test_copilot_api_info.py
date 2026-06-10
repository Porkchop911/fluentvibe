"""Signature help + hover: pure introspection of the fluentvibe API."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.copilot.api_info import hover_at, signature_at  # noqa: E402


def test_signature_at_open_call() -> None:
    line = "    head.aspirate("
    sig = signature_at(line, 0, len(line))
    assert sig is not None
    assert sig.name == "aspirate"
    assert sig.owner == "MCA96Head"
    assert sig.label.startswith("aspirate(")
    # The real signature's parameters, self dropped.
    assert "volume_ul" in sig.params
    assert "liquid_class" in sig.params
    assert sig.active_param == 0


def test_signature_active_param_tracks_commas() -> None:
    line = "    head.aspirate(src, "
    sig = signature_at(line, 0, len(line))
    assert sig is not None
    assert sig.active_param == 1


def test_signature_for_worktable_place() -> None:
    line = "    src = wt.place("
    sig = signature_at(line, 0, len(line))
    assert sig is not None
    assert sig.name == "place"
    assert sig.owner == "Worktable"


def test_signature_none_outside_a_call() -> None:
    assert signature_at("    head.aspirate(src)", 0, len("    head.aspirate(src)")) is None
    assert signature_at("    x = 1", 0, 8) is None


def test_hover_on_method_returns_signature_and_doc() -> None:
    line = "    head.pick_up(tips)"
    # cursor on the "pick_up" token
    col = line.index("pick_up") + 2
    info = hover_at(line, 0, col)
    assert info is not None
    assert info.name == "pick_up"
    assert info.owner == "MCA96Head"
    assert info.doc  # pick_up has a docstring
    assert "columns" in info.params


def test_hover_none_on_unknown_receiver() -> None:
    line = "    foo.bar(x)"
    assert hover_at(line, 0, line.index("bar") + 1) is None
