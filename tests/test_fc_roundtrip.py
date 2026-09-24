"""FluentControl edits carried back to Python lines (fc_roundtrip)."""

from __future__ import annotations

import re

from fluentvibe.authoring import fc_roundtrip
from fluentvibe.authoring.fc_roundtrip import diff_scripts, locate_variables, roundtrip_message
from fluentvibe.blocks import offdeck_step
from fluentvibe.ir.schema import Group, Protocol, UserPromptStep, WaitStep
from tests.test_blocks import Deck


def _compiled_deck(tmp_path):
    deck = Deck()
    deck.cleanup(name="PCR clean-up")
    offdeck_step(deck.wt, "Seal the plate and spin it down.", name="Spin")
    base = tmp_path / "base.xscr"
    deck.wt.compile(base)
    return deck, base


def test_unchanged_script_has_no_changes(tmp_path):
    deck, base = _compiled_deck(tmp_path)
    assert diff_scripts(base, base, wt=deck.wt) == []


def test_edits_in_the_script_point_at_python(tmp_path):
    deck, base = _compiled_deck(tmp_path)
    text = base.read_text(encoding="utf-8-sig")
    text, n1 = re.subn(r"(<d3p1:Name>PCR_CLEAN_UP_BEAD_VOLUME_UL</d3p1:Name>.*?<d2p1:string>)36\.0(</d2p1:string>)",
                       r"\g<1>40.0\g<2>", text, count=1, flags=re.S)
    text, n2 = re.subn("Seal the plate and spin it down.", "Seal the plate firmly and spin it down.", text, count=1)
    assert (n1, n2) == (1, 1)
    edited = tmp_path / "edited.xscr"
    edited.write_text(text, encoding="utf-8")

    source = 'deck.cleanup(\n    name="PCR clean-up",\n)\n'
    changes = locate_variables(diff_scripts(base, edited, wt=deck.wt), source)
    kinds = {c.kind: c for c in changes}
    assert set(kinds) == {"changed", "variable"}
    prompt = kinds["changed"]
    assert prompt.command == "user_prompt" and prompt.python_line is not None
    assert prompt.fields["prompt"][1] == "Seal the plate firmly and spin it down."
    variable = kinds["variable"]
    assert variable.fields["default"] == (36.0, 40.0) and variable.python_line == 1
    assert "PCR_CLEAN_UP_BEAD_VOLUME_UL" in roundtrip_message(changes)


def test_deleted_and_added_commands_are_aligned(monkeypatch):
    def protocol(*steps):
        return Protocol(name="p", groups=[Group(name="G", steps=list(steps))], worktable_guid="g", worktable_name="w")

    base = protocol(UserPromptStep(prompt="a"), WaitStep(duration_seconds=5), UserPromptStep(prompt="b"))
    scripts = {
        "base": base,
        "deleted": protocol(UserPromptStep(prompt="a"), UserPromptStep(prompt="b")),
        "inserted": protocol(UserPromptStep(prompt="a"), WaitStep(duration_seconds=5),
                             UserPromptStep(prompt="new"), UserPromptStep(prompt="b")),
    }
    monkeypatch.setattr("fluentvibe.decompiler.xscr_parser.parse_xscr", lambda path: scripts[str(path)])
    assert [(c.kind, c.command) for c in fc_roundtrip.diff_scripts("base", "deleted")] == [("removed", "wait")]
    added = fc_roundtrip.diff_scripts("base", "inserted")
    assert [(c.kind, c.subject) for c in added] == [("added", "new")]
