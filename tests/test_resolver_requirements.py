"""wt.add resolution and the requirements ledger (ADR: docs/adr-authoring-resolver.md).

The negative tests are the falsification checks: each breaks one explicit
requirement in a way a comment or a name could hide, and verification must
fail (or resolution must refuse).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fluentvibe.authoring.eval_rubric import build_worktable_from_source
from fluentvibe.authoring.requirements import FAIL, PASS, fully_verified, load_requirements, verify
from fluentvibe.resolver import ResolutionConflict

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "ampure_resolver.py"
LEDGER = REPO / "examples" / "ampure_resolver.requirements.json"
PROFILE = REPO / "build" / "workspaces" / "sat_1080_test"

pytestmark = pytest.mark.skipif(not (PROFILE / "workspace_profile.json").exists(),
                                reason="needs the sat_1080_test deck profile")


@pytest.fixture(autouse=True)
def _profile(monkeypatch):
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV

    monkeypatch.setenv(PROFILE_DIR_ENV, str(PROFILE))


def _verdicts(source: str):
    wt = build_worktable_from_source(source, str(EXAMPLE))
    wt.simulate(strict=True)
    return {v.id: v for v in verify(wt, load_requirements(LEDGER))}, wt


def _source() -> str:
    return EXAMPLE.read_text(encoding="utf-8")


def test_example_meets_every_requirement():
    verdicts, wt = _verdicts(_source())
    assert fully_verified(verdicts.values()), {k: (v.status, v.evidence) for k, v in verdicts.items()}
    choices = [d["choice"] for d in wt.resolution_report()]
    assert "head fca" in choices and any(c.startswith("source 70_ethanol") for c in choices)


def test_explicit_head_overrides_the_bulk_liquid_default():
    report = _verdicts(_source())[1].resolution_report()
    ethanol = [d for d in report if d["call"].startswith("Ethanol") and d["choice"].startswith("head")]
    assert ethanol and all(d["choice"] == "head fca" and d["reason"] == "requested" for d in ethanol)


def test_removed_wait_fails_even_with_its_comment_left():
    source = _source().replace("    wt.wait(duration_seconds=300)\n",
                               "    wt.add_comment(\"Incubate 5 min at room temperature\")\n", 1)
    assert "duration_seconds=300" not in source
    verdicts, _ = _verdicts(source)
    assert verdicts["R5"].status == FAIL, verdicts["R5"].evidence


def test_fca_ethanol_switched_to_mca_fails():
    source = _source().replace('head="fca", liquid_class_var="LC_ETHANOL"', 'head="mca", liquid_class_var="LC_ETHANOL"')
    verdicts, _ = _verdicts(source)
    assert verdicts["R3"].status == FAIL and "MCA" not in verdicts["R3"].evidence.upper().split("USE")[0] or \
        verdicts["R3"].status == FAIL


def test_literal_liquid_class_instead_of_variable_fails():
    # (A named block turns a literal into its own string variable, which does
    # meet the requirement; variables=False writes a true literal.)
    old = 'liquid_class="LC_SAMPLE", name="Discard supernatant")'
    assert old in _source()
    source = _source().replace(old, 'liquid_class="Water Free Single", name="Discard supernatant", variables=False)')
    verdicts, _ = _verdicts(source)
    assert verdicts["R4"].status == FAIL and "literal" in verdicts["R4"].evidence


def test_impossible_explicit_combination_is_a_conflict():
    source = _source().replace(
        '    wt.group("Bind")\n',
        '    slim = wt.place(Trough25mL("Slim", catalog="25ml_short"), "WS_100ml_1", 6)\n'
        '    wt.group("Bind")\n'
        '    wt.add(ethanol, to=samples, volume_ul=50, head="mca", source=slim, name="Impossible")\n', 1)
    with pytest.raises(ResolutionConflict, match="cannot pipette from a slim trough"):
        build_worktable_from_source(source, str(EXAMPLE))


def test_missing_evidence_is_unknown_not_pass():
    verdicts, _ = _verdicts(_source().replace('Reagent("70% ethanol")', 'Reagent("80% isopropanol")'))
    assert verdicts["R3"].status == "unknown"
    assert not fully_verified(verdicts.values())


def test_resolved_labware_is_placed_at_the_start():
    wt = build_worktable_from_source(_source(), str(EXAMPLE))
    protocol = wt.to_protocol()
    placement = next(g for g in protocol.groups if g.name == "Labware Placement")
    labels = [s.label for s in placement.steps if type(s).__name__ == "AddLabwareStep"]
    assert "AMPure_XP_beads_FCA_source" in labels and "FcaTips1" in labels
    later = [s for g in protocol.groups if g.name != "Labware Placement" for s in g.steps
             if type(s).__name__ == "AddLabwareStep"]
    assert not later


def test_wt_add_without_profile_says_how_to_fix(monkeypatch):
    from fluentvibe.authoring.profile import PROFILE_DIR_ENV
    from fluentvibe.resolver import ResolutionError

    monkeypatch.delenv(PROFILE_DIR_ENV, raising=False)
    with pytest.raises(ResolutionError, match="FLUENTVIBE_PROFILE_DIR"):
        build_worktable_from_source(_source(), str(EXAMPLE))
    assert PASS == "pass"


def test_new_check_kinds_on_the_example():
    from fluentvibe.authoring.requirements import Requirement, verify_all

    wt = build_worktable_from_source(_source(), str(EXAMPLE))
    wt.simulate()
    verdicts = {v.id: v for v in verify_all(wt, [
        Requirement("vol", "20 ul samples", "sample_volume", {"ul": 20}),
        Requirement("n", "whole plate", "sample_count", {"count": 96}),
        Requirement("plate", "abgene plates", "plate_catalog", {"contains": "ABgene"}),
        Requirement("mca", "otherwise the MCA", "head_for_other_steps",
                    {"head": "mca", "except": ["AMPure", "Elution", "ethanol"]}),
        Requirement("free", "not checkable", "unchecked"),
    ])}
    assert [verdicts[k].status for k in ("vol", "n", "plate", "mca")] == ["pass"] * 4
    assert verdicts["free"].status == "unknown"


def test_mix_default_is_a_clarification_not_a_failure():
    from fluentvibe.authoring.requirements import Requirement, verify

    wt = build_worktable_from_source(_source(), str(EXAMPLE))
    (verdict,) = verify(wt, [Requirement("lc", "lc vars", "liquid_class_variables", {"default": "Water Free Single"})])
    assert verdict.status == "pass" and "Mix section" in verdict.evidence


def test_extraction_parses_and_drops_unchecked_duplicates():
    import json as _json

    from fluentvibe.authoring.requirements import extract_requirements

    class Client:
        def complete(self, *, messages, tools):
            args = {"requirements": [
                {"id": "R1", "text": "use the fca for ethanol", "kind": "head_for_reagent",
                 "params": {"reagent": "ethanol", "head": "fca"}},
                {"id": "R2", "text": "use the fca for ethanol", "kind": "unchecked"},
                {"id": "R3", "text": "label the plates nicely", "kind": "unchecked"},
            ], "dispositions": [{"clause": "thanks!", "disposition": "excluded"}]}
            return {"tool_calls": [{"function": {"name": "submit_requirements", "arguments": _json.dumps(args)}}]}

    reqs, dispositions = extract_requirements(Client(), "use the fca for ethanol; label the plates nicely")
    assert [r.id for r in reqs] == ["R1", "R3"] and dispositions[0]["disposition"] == "excluded"
