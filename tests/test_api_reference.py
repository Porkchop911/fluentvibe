"""The API reference agents read is generated from the code (it once listed
12 of 41 Worktable methods, and a model concluded wt.volume did not exist)."""

import inspect

from fluentvibe.api_reference import format_text, reference
from fluentvibe.worktable import Worktable


def test_every_public_worktable_method_is_listed():
    listed = {m["name"] for m in reference("Worktable")["members"]}
    public = {n for n, v in inspect.getmembers(Worktable) if not n.startswith("_") and callable(v)}
    assert public <= listed and {"volume", "add", "remove", "labware_by_label"} <= listed


def test_heads_blocks_and_unknown_objects():
    assert any(m["name"] == "aspirate" for m in reference("wt.liha")["members"])
    assert any(m["name"] == "distribute_reagent" for m in reference("blocks")["members"])
    assert reference("liha")["object"] == "wt.liha"
    bad = reference("NoSuchThing")
    assert "error" in bad and "Worktable" in format_text(bad)
