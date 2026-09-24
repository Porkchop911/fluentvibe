"""Bench Spec parsing, deterministic checks, presentation and extraction."""

from __future__ import annotations

import json

from fluentvibe.authoring.bench_spec import (
    EXTRACTION_TOOL_NAME,
    bench_spec_json_schema,
    extract_bench_spec,
    spec_context_block,
    spec_to_markdown,
    validate_bench_spec,
)

DOC = """
7 Incubate the tubes or plate at 30°C for 2 minutes and then at 80°C for 2
minutes. Briefly put the tubes or plate on ice to cool.
9 Pool all the barcoded samples into a clean 1.5 or 2 ml Eppendorf DNA LoBind
tube, noting the total volume. 10 μ l 120 μ l 240 μ l 480 μ l 960 μ l
11 To the entire pooled barcoded sample, add an equal volume of resuspended
AMPure XP Beads (AXP). 15 Keep the tube on the magnet and wash the beads with
1.5 ml of freshly prepared 80% ethanol. 18 resuspend the pellet in 15 µl
Elution Buffer (EB). Add 1 µl of the diluted Rapid Adapter (RA). AXP 1,200.
"""


def _spec(**overrides):
    spec = {
        "title": "ONT RBK114 amplicon barcoding",
        "sample_count": 96,
        "sample_volume_ul": 9,
        "reagents": [
            {"id": "RB", "name": "Rapid Barcodes", "role": "per_sample"},
            {"id": "AXP", "name": "AMPure XP Beads", "role": "bead_carrier"},
            {"id": "EB", "name": "Elution Buffer", "role": "eluent"},
        ],
        "steps": [
            {"id": "s1", "op": "add", "text": "Add 1 µl barcode per sample", "location": "deck",
             "reagent": "RB", "volume_ul": 1},
            {"id": "s2", "op": "incubate", "text": "30 °C 2 min then 80 °C 2 min on a thermal cycler",
             "location": "off_deck", "temp_c": [30, 80], "minutes": [2, 2]},
            {"id": "s3", "op": "pool", "text": "Pool 10 µl of every sample", "location": "deck",
             "volume_ul": 10},
            {"id": "s4", "op": "bead_cleanup", "text": "Equal-volume AXP clean-up", "location": "deck",
             "reagent": "AXP", "ratio": 1, "washes": 2, "elute_ul": 15},
        ],
        "notes": [],
    }
    spec.update(overrides)
    return spec


def test_valid_spec_has_no_problems():
    spec, problems = validate_bench_spec(_spec(), DOC)
    assert spec is not None
    assert problems == []
    assert [s.op for s in spec.steps] == ["add", "incubate", "pool", "bead_cleanup"]
    assert spec.steps[1].temp_c == [30.0, 80.0]


def test_invented_numbers_are_flagged():
    raw = _spec()
    raw["steps"][3]["ratio"] = 1.8  # the document never says 1.8
    _, problems = validate_bench_spec(raw, DOC)
    assert [(p.kind, p.where) for p in problems] == [("number", "steps[3].ratio")]


def test_thousands_separator_and_spaced_units_are_read():
    raw = _spec()
    raw["steps"][2]["volume_ul"] = 960
    raw["notes"] = []
    raw["steps"].append({"id": "s5", "op": "custom", "text": "AXP stock", "location": "manual",
                         "volume_ul": 1200})
    _, problems = validate_bench_spec(raw, DOC)
    assert problems == []


def test_off_deck_device_marked_deck_is_flagged():
    raw = _spec()
    raw["steps"][1]["location"] = "deck"
    _, problems = validate_bench_spec(raw, DOC)
    assert [(p.kind, p.where) for p in problems] == [("location", "steps[1].location")]


def test_schema_problems_are_collected():
    raw = _spec()
    raw["steps"][0]["op"] = "teleport"
    raw["steps"][1]["id"] = "s1"
    raw["steps"][2]["reagent"] = "XYZ"
    _, problems = validate_bench_spec(raw)
    kinds = {(p.kind, p.where) for p in problems}
    assert ("schema", "steps[0].op") in kinds
    assert ("schema", "steps[1].id") in kinds
    assert ("schema", "steps[2].reagent") in kinds
    assert validate_bench_spec({"title": "x", "steps": []})[0] is None


def test_markdown_marks_flagged_rows_and_context_block_is_json():
    raw = _spec()
    raw["steps"][3]["ratio"] = 1.8
    spec, problems = validate_bench_spec(raw, DOC)
    md = spec_to_markdown(spec, problems)
    assert "| s4 |" in md and "1.8 does not appear" in md
    assert "| s1 |" in md and md.count("| ok |") == 3
    block = spec_context_block(spec)
    assert json.loads(block.split("\n", 1)[1])["title"] == spec.title


def test_schema_enumerates_ops_and_locations():
    schema = bench_spec_json_schema()
    step = schema["properties"]["steps"]["items"]["properties"]
    assert "bead_cleanup" in step["op"]["enum"]
    assert step["location"]["enum"] == ["deck", "off_deck", "manual"]


class _FakeClient:
    def __init__(self, arguments):
        self.arguments = arguments
        self.seen = None

    def complete(self, *, messages, tools):
        self.seen = {"messages": messages, "tools": tools}
        if self.arguments is None:
            return {"role": "assistant", "content": "I think...", "tool_calls": []}
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": EXTRACTION_TOOL_NAME, "arguments": self.arguments},
        }]}


def test_extraction_forces_one_tool_and_validates():
    client = _FakeClient(json.dumps(_spec()))
    spec, problems, raw = extract_bench_spec(client, DOC)
    assert spec is not None and problems == [] and raw["title"]
    assert [t["function"]["name"] for t in client.seen["tools"]] == [EXTRACTION_TOOL_NAME]
    assert DOC.strip()[:20] in client.seen["messages"][1]["content"]


def test_extraction_reports_missing_or_bad_tool_call():
    spec, problems, _ = extract_bench_spec(_FakeClient(None), DOC)
    assert spec is None and "did not call" in problems[0].message
    spec, problems, _ = extract_bench_spec(_FakeClient("{not json"), DOC)
    assert spec is None and "not valid JSON" in problems[0].message
