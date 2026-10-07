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


def test_faithful_conversion_keeps_every_well_and_volume(monkeypatch, tmp_path):
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
    # The authoring gate the conversion really goes through (it wants a
    # declared variable before placement; the plain simulation does not).
    from fluentvibe.authoring.lab_scope import load_lab_scope
    from fluentvibe.authoring.tools import AuthoringToolRegistry

    registry = AuthoringToolRegistry(output_dir=tmp_path)
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(conv.source)
    assert gate.get("success"), gate.get("failure_message")
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


def test_a_parked_tip_with_liquid_is_named_not_hidden(monkeypatch):
    if not (PROFILE / "workspace_profile.json").exists():
        pytest.skip("no 1080_Dev profile on this machine")
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.authoring.skeleton import load_deck

    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE))
    trace = _trace()
    # Opentrons "aspirate_and_park_tip": the tip goes back into the rack holding 60 ul.
    trace["events"][2:7] = [
        {"kind": "pick_up_tip", "text": "Picking up tip"},
        {"kind": "aspirate", "labware": "3:falcon", "wells": ["A1"], "volume": 60.0, "channels": 1, "text": ""},
        {"kind": "return_tip", "text": "Returning tip to A1 of tiprack"},
    ]
    conv = convert_trace(trace, load_deck(PROFILE))
    parked = [u for u in conv.report["unconverted"] if u.startswith("parked tip")]
    assert parked and "60 ul" in parked[0]
    assert "operator prompt: Vortex the tubes." in conv.report["kept_pauses_and_waits"]


def test_inventory_links_parts_variants_and_readme_links(tmp_path):
    import csv

    def lib(slug, title, readme_extra=""):
        folder = tmp_path / "lib" / slug
        folder.mkdir(parents=True)
        (folder / "metadata.json").write_text(json.dumps({"name": title, "saved_protocol_file": "p.py"}), "utf-8")
        (folder / "README.md").write_text(f"# {title}\n\nDoes things. {readme_extra}\n", encoding="utf-8")
        (folder / "p.py").write_text("def run(ctx): pass\n", encoding="utf-8")

    lib("0000025894", "Cell Lysis - Part 1", "[Part 2](https://library.opentrons.com/p/0000025894-2)")
    lib("0000025894-2", "Cell Lysis - Part 2")
    lib("bca", "BCA", "See https://library.opentrons.com/p/normalization too.")
    lib("normalization", "Normalization")
    lib("lonely", "Lonely")
    entries = pi.build_index([tmp_path / "lib"])
    related = pi.related_protocols(entries)
    assert related["0000025894"] == ["0000025894-2"] and related["0000025894-2"] == ["0000025894"]
    assert related["bca"] == ["normalization"] and related["lonely"] == []
    out = tmp_path / "inventory.csv"
    assert pi.export_csv(entries, out) == 5
    rows = {r["id"]: r for r in csv.DictReader(out.open(encoding="utf-8-sig"))}
    assert rows["bca"]["related_protocols"] == "normalization" and rows["bca"]["path"].endswith("p.py")
    assert rows["lonely"]["description"].startswith("Does things")


def test_air_gap_is_not_liquid_and_the_waste_chute_is_the_waste(monkeypatch):
    if not (PROFILE / "workspace_profile.json").exists():
        pytest.skip("no 1080_Dev profile on this machine")
    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.authoring.skeleton import load_deck

    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE))
    trace = _trace()
    trace["labware"]["waste:trash"] = {"load_name": "waste", "display": "Waste", "slot": "", "wells": ["A1"],
                                       "rows": 1, "columns": 1, "capacity_ul": 1e9, "is_tiprack": False}
    # "Remove & discard supernate": aspirate 25, air gap 5, dispense 30 into the waste chute.
    trace["events"][10:12] = [
        {"kind": "aspirate", "labware": "1:plate", "wells": ["A1"], "volume": 25.0, "channels": 1, "text": ""},
        {"kind": "air_gap", "volume": 5.0, "text": "Air gap of 5 uL"},
        {"kind": "dispense", "labware": "waste:trash", "wells": ["A1"], "volume": 30.0, "channels": 1, "text": ""},
    ]
    trace["labware"]["1:plate"]["capacity_ul"] = 200.0
    conv = convert_trace(trace, load_deck(PROFILE))
    assert "fca.dispense(waste, 25," in conv.source.lower().replace("waste_2", "waste")
    wt = build_worktable_from_source(conv.source, "tiny.py")
    wt.simulate(strict=True)  # was: "tip holds 25 ul but 30 ul requested"
    fidelity = volume_fidelity(trace, conv, wt.simulation_report.final_labware)
    assert fidelity["wells_matching"] == fidelity["wells_checked"], fidelity


def test_multi_dispense_runs_use_the_multi_liquid_class():
    # FluentControl: "Missing in Tip ... Aspiration volume must be at least dispense
    # volume" when one aspirate fed several dispenses with "Water Free Single".
    from fluentvibe.authoring.opentrons_faithful import _multi_dispense_events

    events = [{"kind": "pick_up_tip"}, {"kind": "aspirate"}, {"kind": "dispense"}, {"kind": "dispense"},
              {"kind": "dispense"}, {"kind": "aspirate"}, {"kind": "dispense"}, {"kind": "drop_tip"},
              {"kind": "aspirate"}, {"kind": "aspirate"}, {"kind": "dispense"}]
    multi = _multi_dispense_events(events)
    assert [i for i, e in enumerate(events) if id(e) in multi] == [1, 2, 3, 4]


def test_96_channel_moves_go_to_the_mca(monkeypatch, tmp_path):
    # pcr_amp_uminn, evotips, 00d445: every move was a 96-channel one and stayed a
    # TODO comment, so 0 wells matched; Strata called them "not faithful".
    if not (PROFILE / "workspace_profile.json").exists():
        pytest.skip("no 1080_Dev profile on this machine")
    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.authoring.skeleton import load_deck

    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE))
    plate_wells = [f"{r}{c}" for c in range(1, 13) for r in "ABCDEFGH"]
    plate = {"load_name": "nest_96_wellplate", "display": "PCR plate", "slot": "1", "wells": plate_wells,
             "rows": 8, "columns": 12, "capacity_ul": 200.0, "is_tiprack": False}
    reservoir = {"load_name": "nest_1_reservoir_195ml", "display": "Master mix reservoir", "slot": "2",
                 "wells": ["A1"], "rows": 1, "columns": 1, "capacity_ul": 195000.0, "is_tiprack": False}
    trace = {"protocol": "x.py", "name": "96ch", "labware": {"1:plate": plate, "2:res": reservoir}, "events": [
        {"kind": "pick_up_tip", "channels": 96, "text": "Picking up tip"},
        {"kind": "aspirate", "labware": "2:res", "wells": ["A1"] * 96, "volume": 20.0, "channels": 96, "text": ""},
        {"kind": "dispense", "labware": "1:plate", "wells": plate_wells, "volume": 20.0, "channels": 96, "text": ""},
        {"kind": "drop_tip", "channels": 96, "text": "Dropping tip"},
    ]}
    conv = convert_trace(trace, load_deck(PROFILE))
    assert "mca.mount_adapter()" in conv.source and "mca.dispense(" in conv.source
    assert "TODO 96-channel" not in conv.source
    sbs = [c for c in conv.report["containers"] if c["kind"] == "sbs"]
    assert sbs and "SBS" in sbs[0]["catalog"]  # the MCA cannot draw from a slim trough
    assert not any(c["kind"] == "tips" for c in conv.report["containers"])  # no FCA box: the FCA never pipettes
    wt = build_worktable_from_source(conv.source, "mca.py")
    wt.simulate(strict=True)
    fidelity = volume_fidelity(trace, conv, wt.simulation_report.final_labware)
    assert fidelity["wells_checked"] == 97 and fidelity["wells_matching"] == 97, fidelity


def test_transfers_near_the_tip_volume_are_split():
    # FluentControl: "Too much in Tip: 0.25 ul ... use larger tip" for 900 ul in a 1000 ul tip.
    from fluentvibe.authoring.opentrons_faithful import _split_large_transfers

    events = [{"kind": "aspirate", "volume": 900.0, "channels": 1}, {"kind": "dispense", "volume": 900.0, "channels": 1},
              {"kind": "aspirate", "volume": 100.0, "channels": 1}, {"kind": "dispense", "volume": 100.0, "channels": 1}]
    split = _split_large_transfers(events, 850.0)
    assert [(e["kind"], e["volume"]) for e in split] == [
        ("aspirate", 450.0), ("dispense", 450.0), ("aspirate", 450.0), ("dispense", 450.0),
        ("aspirate", 100.0), ("dispense", 100.0)]


def test_large_multi_dispense_runs_are_cut_up():
    # FluentControl: "Too much in Plunger: 61.49 ul ... use multiple pipetting steps"
    # for aspirate 540 + 20 ul, then 3 x 180 ul with the multi-dispense class.
    from fluentvibe.authoring.opentrons_faithful import _split_multi_runs

    src = {"labware": "r", "wells": ["A1"] * 8, "channels": 8}
    events = [{"kind": "aspirate", "volume": 540.0, **src}, {"kind": "delay", "seconds": 2},
              {"kind": "aspirate", "volume": 20.0, **src}]
    for col in (4, 5, 6):
        events += [{"kind": "dispense", "volume": 180.0, "labware": "p", "wells": [f"A{col}"], "channels": 8},
                   {"kind": "delay", "seconds": 2}]
    split = _split_multi_runs(events, 500.0)
    loads = [e["volume"] for e in split if e["kind"] == "aspirate"]
    assert loads == [360.0, 200.0]  # the surplus (20 ul) goes with the last part
    assert [e["wells"] for e in split if e["kind"] == "dispense"] == [["A4"], ["A5"], ["A6"]]


def test_the_air_gap_aspirate_after_an_air_gap_is_air():
    # Newer Opentrons API levels log "Air gap of 10 uL" and then "Aspirating 10 uL
    # from A10 of the plate" as two steps; taken as liquid it overfilled a tip.
    from fluentvibe.authoring.opentrons_faithful import _drop_air_gap_aspirates

    events = [{"kind": "aspirate", "volume": 20.0}, {"kind": "dispense", "volume": 20.0},
              {"kind": "air_gap", "volume": 10.0}, {"kind": "aspirate", "volume": 10.0},
              {"kind": "aspirate", "volume": 10.0}]
    kept = _drop_air_gap_aspirates(events)
    assert [e["kind"] for e in kept] == ["aspirate", "dispense", "air_gap", "aspirate"]
