"""Reagent budget: kit reagents may not be loaded beyond the kit's supply."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from fluentvibe import Reagent, Trough25mL
from fluentvibe.authoring.bench_spec import spec_context_block, validate_bench_spec
from fluentvibe.authoring.eval_rubric import score_semantic
from fluentvibe.authoring.reagent_budget import check_reagent_budget, spec_from_prompt
from tests.test_blocks import LC, Deck, _workspace

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = REPO_ROOT / "examples" / "ont_rbk114_spec.json"


def _spec():
    spec, problems = validate_bench_spec(json.loads(SPEC.read_text(encoding="utf-8")))
    assert spec is not None and problems == []
    return spec


def test_gold_protocol_fits_the_kit():
    _workspace()
    path = REPO_ROOT / "examples" / "ont_rbk114_blocks.py"
    loader = importlib.util.spec_from_file_location("ont_rbk114_blocks_budget", path)
    module = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(module)
    wt = module.build_worktable()
    wt.simulate(strict=True)
    assert check_reagent_budget(wt, _spec()) == []
    inv = next(i for i in score_semantic(wt, spec=_spec()) if i.key == "reagent_budget")
    assert inv.status == "pass"


def test_adapter_dilution_in_every_well_exceeds_the_kit():
    # The document-only 27B draft: 1.5 µl RA per well for 96 wells, from a kit
    # that ships 15 µl.
    deck = Deck()
    ra = deck.wt.place(Trough25mL("RA", catalog="25ml_short"), "WS_100ml_1", 5)
    ra.fill_all(Reagent("Rapid Adapter (RA)"), 200.0)
    head = deck.wt.liha
    head.get_tips(deck.fca_tips)
    for col in range(12):
        head.aspirate(ra, 1.5, liquid_class=LC)
        head.dispense(deck.eluate, 1.5, liquid_class=LC, well_offset=col * 8)
    head.drop_tips()
    deck.wt.simulate(strict=True)
    findings = {f.reagent_id: f for f in check_reagent_budget(deck.wt, _spec())}
    ra = findings["RA"]
    assert ra.loaded_ul == pytest.approx(200.0)
    assert ra.consumed_ul == pytest.approx(144.0)
    assert ra.available_ul == pytest.approx(15.0)
    inv = next(i for i in score_semantic(deck.wt, spec=_spec()) if i.key == "reagent_budget")
    assert inv.status == "fail" and "kit provides 15 µl" in inv.evidence


def test_lab_stock_names_are_not_charged_to_the_kit():
    deck = Deck()  # troughs: "AMPure XP" beads, "EB" eluent
    deck.wt.simulate(strict=True)
    ids = {f.reagent_id for f in check_reagent_budget(deck.wt, _spec())}
    # "EB" as a whole word is the kit's Elution Buffer (15000 µl > 500 µl);
    # "AMPure XP" without the AXP id is lab stock and not charged.
    assert ids == {"EB"}


def test_spec_round_trips_through_the_authoring_prompt():
    spec = _spec()
    prompt = "Automate the library prep.\n\n" + spec_context_block(spec) + "\n\nAttached file context: ..."
    parsed = spec_from_prompt(prompt)
    assert parsed is not None
    assert {r.id: r.supply_ul for r in parsed.reagents}["RA"] == 15.0
    assert spec_from_prompt("no spec here") is None
