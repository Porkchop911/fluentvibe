"""Copilot Phase 5: deterministic completions (API methods + catalog names)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.catalog.catalog import index_exists  # noqa: E402
from fluentvibe.copilot.complete import complete_at  # noqa: E402


def _labels(source: str, line0: int, col0: int) -> list[str]:
    return [c.label for c in complete_at(source, line0, col0)]


def test_method_completion_for_head() -> None:
    line = "    head."
    labels = _labels(line, 0, len(line))
    assert "aspirate" in labels
    assert "pick_up" in labels
    assert "mount_adapter" in labels


def test_method_completion_for_worktable_and_gripper() -> None:
    assert "place" in _labels("wt.", 0, 3)
    assert "group" in _labels("wt.", 0, 3)
    assert "move" in _labels("    gripper.", 0, len("    gripper."))


def test_method_completion_chained_receiver() -> None:
    line = "    wt.mca96."
    assert "aspirate" in _labels(line, 0, len(line))


def test_method_completion_filtered_by_partial() -> None:
    line = "    head.pi"
    labels = _labels(line, 0, len(line))
    assert "pick_up" in labels
    assert "aspirate" not in labels


def test_replace_start_is_after_the_dot() -> None:
    line = "    head.as"
    comps = complete_at(line, 0, len(line))
    assert comps
    # "    head." is 9 chars, so the member partial starts at column 9.
    assert all(c.replace_start == 9 for c in comps)


def test_no_completion_in_plain_code() -> None:
    assert _labels("    x = 1", 0, 9) == []
    assert _labels("import os", 0, 9) == []


def test_method_completion_carries_signature_and_snippet() -> None:
    line = "    head.aspir"
    comps = [c for c in complete_at(line, 0, len(line)) if c.label == "aspirate"]
    assert comps, "expected an aspirate completion"
    c = comps[0]
    # The detail is the real signature, the insert is a snippet placing the cursor
    # inside the parens, and it is flagged as a snippet for the editor.
    assert c.detail.startswith("aspirate(")
    assert c.insert_text == "aspirate($0)"
    assert c.insert_format == "snippet"
    assert c.documentation  # one-line docstring summary


def test_method_completion_skips_non_callable_attributes() -> None:
    # Every offered member must be callable on the class (no data/property noise).
    from fluentvibe.copilot.complete import _class_for_receiver

    cls = _class_for_receiver("head")
    assert cls is not None
    for c in complete_at("    head.", 0, len("    head.")):
        assert callable(getattr(cls, c.label, None))


@pytest.mark.skipif(not index_exists(), reason="needs a built catalog index")
def test_catalog_name_completion() -> None:
    line = '    tips = wt.place(Plate96("S", catalog="96 W'
    comps = complete_at(line, 0, len(line))
    assert comps, "expected catalog suggestions"
    assert all(c.kind == "catalog" for c in comps)
    assert all(c.insert_format == "plain" for c in comps)
    assert any("96 Well" in c.label for c in comps)
    # The replacement starts just inside the opening quote.
    open_quote = line.rindex('"')
    assert all(c.replace_start == open_quote + 1 for c in comps)


@pytest.mark.skipif(not index_exists(), reason="needs a built catalog index")
def test_catalog_completion_ranks_expected_category_first() -> None:
    # Inside a tip-box constructor, tip_box-category names should be ranked ahead
    # of anything else that matches the substring.
    line = '    tips = wt.place(MCA100Box("T", catalog="MCA'
    comps = complete_at(line, 0, len(line))
    if not comps:
        pytest.skip("no MCA catalog entries indexed")
    kinds = [c.detail for c in comps]
    # The first suggestion's category is tip_box when any tip_box entry matches.
    if "tip_box" in kinds:
        assert kinds[0] == "tip_box"
