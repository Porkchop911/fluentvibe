"""Opentrons protocols: the searchable index and the well-by-well conversion.

No Opentrons installation needed: the index reads small fixture folders, and
the converter gets a hand-written trace in the shape scripts/opentrons_trace.py
writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fluentvibe import protocol_index as pi
from fluentvibe.authoring.opentrons_faithful import convert_trace, volume_fidelity

PROFILE = Path("build") / "workspaces" / "1080_Dev"


def test_index_reads_library_and_git_folders_and_searches_by_what_they_do(tmp_path):
    lib = tmp_path / "Opentron_protocols" / "0000025894"
    lib.mkdir(parents=True)
    (lib / "metadata.json").write_text(json.dumps({"name": "Cell Lysis and BCA", "saved_protocol_file": "p.py"}),
                                       encoding="utf-8")
    (lib / "README.md").write_text("# Cell Lysis and BCA\n\nA lysis protocol.\n\n1. Adding buffer\n2. Adding BCA\n",
                                   encoding="utf-8")
    (lib / "p.py").write_text("metadata = {'protocolName': 'x', 'apiLevel': '2.15'}\ndef run(ctx):\n    pass\n",
                              encoding="utf-8")
    git = tmp_path / "git" / "opentrons_protocols" / "protocols" / "00222e"
    git.mkdir(parents=True)
    (git / "README.md").write_text(
        "# Plasma Spike Serial Dilution\n\n## Categories\n* Sample Prep\n\t* Serial Dilution\n\n"
        "## Description\nDilutes a stock.\n\n## Labware\n* [Tip Rack](http://x)\n", encoding="utf-8")
    (git / "00222e.ot2.apiv2.py").write_text("def run(ctx):\n    pass\n", encoding="utf-8")

    entries = pi.build_index([tmp_path / "Opentron_protocols", tmp_path / "git"])
    assert {e.id for e in entries} == {"0000025894", "git:00222e"}
    bca = pi.find(entries, "0000025894")
    assert bca.title == "Cell Lysis and BCA" and bca.steps == ["Adding buffer", "Adding BCA"]
    assert bca.path.endswith("p.py") and bca.convertible
    assert [e.id for e in pi.search(entries, "serial dilution")] == ["git:00222e"]
    assert pi.find(entries, "00222e").labware == ["Tip Rack"]  # id without the git: prefix
    assert pi.search(entries, "bca nothing-like-this") == []


def _trace() -> dict:
    """Buffer from a 50 ml tube into two 1.5 ml tubes, then 8-channel stamp
    of 20 ul from those... (kept small): 1-channel moves and one 8-channel."""
    tube_rack = {"load_name": "opentrons_10_tuberack_falcon", "display": "Falcon rack", "slot": "3",
                 "wells": ["A1", "A2", "A3"], "rows": 3, "columns": 4, "capacity_ul": 50000.0,
                 "is_tiprack": False}
    eppis = {"load_name": "opentrons_24_tuberack_eppendorf", "display": "Eppendorf rack", "slot": "2",
             "wells": ["A1", "B1", "C1", "D1"], "rows": 4, "columns": 6, "capacity_ul": 1500.0,
             "is_tiprack": False}
    plate = {"load_name": "nest_96_wellplate", "display": "Sample plate", "slot": "1",
             "wells": [f"{r}{c}" for c in range(1, 13) for r in "ABCDEFGH"], "rows": 8, "columns": 12,
             "capacity_ul": 200.0, "is_tiprack": False}
    col1 = [f"{r}1" for r in "ABCDEFGH"]
    col2 = [f"{r}2" for r in "ABCDEFGH"]
    events = [
        {"kind": "comment", "text": "---- Adding buffer ----"},
        {"kind": "pick_up_tip", "text": "Picking up tip"},
        {"kind": "aspirate", "labware": "3:falcon", "wells": ["A1"], "volume": 600.0, "channels": 1, "text": ""},
        {"kind": "dispense", "labware": "2:eppi", "wells": ["A1"], "volume": 600.0, "channels": 1, "text": ""},
        {"kind": "aspirate", "labware": "3:falcon", "wells": ["A1"], "volume": 100.0, "channels": 1, "text": ""},
        {"kind": "dispense", "labware": "2:eppi", "wells": ["B1"], "volume": 100.0, "channels": 1, "text": ""},
        {"kind": "drop_tip", "text": "Dropping tip"},
        {"kind": "pause", "text": "Pausing", "message": "Vortex the tubes."},
        {"kind": "comment", "text": "---- Stamp ----"},
        {"kind": "pick_up_tip", "text": "Picking up tip"},
        {"kind": "aspirate", "labware": "1:plate", "wells": col1, "volume": 20.0, "channels": 8, "text": ""},
        {"kind": "dispense", "labware": "1:plate", "wells": col2, "volume": 20.0, "channels": 8, "text": ""},
        {"kind": "drop_tip", "text": "Dropping tip"},
        {"kind": "delay", "text": "Delaying", "seconds": 30.0},
    ]
    return {"protocol": "x.py", "name": "Tiny", "events": events,
            "labware": {"3:falcon": tube_rack, "2:eppi": eppis, "1:plate": plate}}


def test_faithful_conversion_keeps_every_well_and_volume(monkeypatch):
    if not (PROFILE / "workspace_profile.json").exists():
        pytest.skip("no 1080_Dev profile on this machine")
    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.authoring.skeleton import load_deck

    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE))
    trace = _trace()
    conv = convert_trace(trace, load_deck(PROFILE))
    # The 600 ul tube does not fit a plate well: its own trough; the 100 ul tube
    # and the 96-well plate keep plate wells.
    assert conv.mapping["2:eppi|A1"][1] == "A1" and conv.mapping["2:eppi|A1"][0] != conv.mapping["2:eppi|B1"][0]
    assert conv.mapping["1:plate|C2"] == (conv.mapping["1:plate|A1"][0], "C2")
    assert "wt.user_prompt('Vortex the tubes.')" in conv.source and "wt.wait(duration_seconds=30)" in conv.source
    wt = build_worktable_from_source(conv.source, "tiny.py")
    wt.simulate(strict=True)
    fidelity = volume_fidelity(trace, conv, wt.simulation_report.final_labware)
    assert fidelity["wells_checked"] > 0 and fidelity["wells_matching"] == fidelity["wells_checked"], fidelity


class _Client:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.messages = content, error, None

    def complete(self, *, messages, tools):
        self.messages = messages
        if self.error:
            raise self.error
        return {"content": self.content}


def test_strata_review_sees_the_source_and_the_well_comparison(tmp_path):
    from fluentvibe.authoring.opentrons_review import review_conversion

    protocol = tmp_path / "p.py"
    protocol.write_text("def run(ctx):\n    pass  # cherrypick\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Cherrypicking\nMoves samples.\n", encoding="utf-8")
    summary = {"mode": "well by well", "stage": "gate", "error": "OverdrawError: tip holds 0.0 ul",
               "fidelity": {"wells_checked": 13, "wells_matching": 7}, "unconverted": ["magnet: Engaging"]}
    answer = ('Thinking... {"verdict": "not faithful", "summary": "The source drops a full tip.", '
              '"must_fix": ["fix the dispense after a new tip"], "source_problems": ["dispenses from an empty tip"]}')
    client = _Client(answer)
    review = review_conversion(protocol, summary, client=client)
    assert review["verdict"] == "not faithful" and review["source_problems"] == ["dispenses from an empty tip"]
    user = client.messages[-1]["content"]
    assert "cherrypick" in user and "Moves samples" in user and '"wells_matching": 7' in user
    assert "magnet: Engaging" in user and client.messages[0]["role"] == "system"


def test_strata_review_failures_never_break_the_conversion(tmp_path):
    from fluentvibe.authoring.opentrons_review import review_conversion

    protocol = tmp_path / "p.py"
    protocol.write_text("def run(ctx): pass\n", encoding="utf-8")
    down = review_conversion(protocol, {}, client=_Client(error=ConnectionRefusedError("8080 refused")))
    assert "could not review" in down["error"] and "8080" in down["error"]
    garbled = review_conversion(protocol, {}, client=_Client("looks fine to me"))
    assert garbled["error"].startswith("Strata's review was not readable") and garbled["raw"] == "looks fine to me"
