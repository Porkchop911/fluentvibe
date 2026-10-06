"""The document check verifies a pool as a pool: many sample wells into fewer,
not any plate-to-plate move (Codex review)."""

from __future__ import annotations

import re

from fluentvibe.authoring.requirements import (
    _operations,
    _protocol_tokens,
    requirements_from_spec,
    skeleton_notes,
    verify_all,
)
from fluentvibe.authoring.skeleton import build_skeleton, load_deck
from tests.test_skeleton import _spec, profile  # noqa: F401  (pytest fixture)

POOL_SPEC = {
    "title": "Rapid barcoding", "sample_count": 48, "sample_volume_ul": 9,
    "reagents": [{"id": "dna", "name": "Amplicon DNA", "role": "sample"},
                 {"id": "rb", "name": "Rapid Barcodes", "role": "per_sample"},
                 {"id": "axp", "name": "AMPure XP", "role": "bead_carrier", "supply_ul": 2400},
                 {"id": "ra", "name": "Rapid Adapter", "role": "reagent", "supply_ul": 15}],
    "steps": [
        {"id": "bc", "op": "add", "text": "Add 1 µl Rapid Barcode", "location": "deck", "reagent": "rb",
         "volume_ul": 1},
        {"id": "pool", "op": "pool", "text": "Pool all barcoded samples", "location": "deck", "volume_ul": 10},
        {"id": "axp", "op": "add", "text": "Add an equal volume of AXP to the pool", "location": "deck",
         "reagent": "axp", "volume_ul": 480},
        {"id": "mag", "op": "separate", "text": "Pellet on a magnet", "location": "deck", "minutes": [2]},
    ],
}


def _run(source, path):
    namespace: dict = {}
    exec(compile(source, str(path), "exec"), namespace)
    wt = namespace["build_worktable"]()
    wt.simulate()
    return wt


def _tokens(wt, reqs):
    return [t[0] for t in _protocol_tokens(wt, _operations(wt), reqs[0].params["reagents"])]


def test_pool_is_checked_as_many_wells_into_fewer(profile, tmp_path):  # noqa: F811
    source = build_skeleton(_spec(POOL_SPEC), load_deck(profile))
    reqs = requirements_from_spec(_spec(POOL_SPEC), skeleton_notes(source))
    assert "pool" in [e["token"] for e in reqs[0].params["expected"]]
    wt = _run(source, tmp_path / "pool.py")
    assert "pool" in _tokens(wt, reqs)
    (verdict,) = verify_all(wt, reqs)
    assert verdict.status == "pass", verdict

    # The same plates, stamped 1:1 into the pool plate: a transfer, not a pool.
    stamped = re.sub(
        r"pool_columns\(wt, (source=\w+, dest=\w+, volume_ul=[\d.]+), tips=\w+,\s*"
        r"(liquid_class=\"[^\"]*\"), dest_column=1, (name=\"[^\"]*\")[^)]*\)",
        r"stamp(wt, \1, tips=bc_tips, \2, \3, columns=[1, 2, 3, 4, 5, 6])", source)
    assert stamped != source and "pool_columns(wt" not in stamped
    wt = _run(stamped, tmp_path / "stamped.py")
    tokens = _tokens(wt, reqs)
    assert "pool" not in tokens and "transfer" in tokens
    (verdict,) = verify_all(wt, reqs)
    assert verdict.status == "fail" and "pool" in verdict.evidence, verdict


def test_few_samples_pooled_into_one_well_are_a_pool(profile, tmp_path):  # noqa: F811
    raw = {**POOL_SPEC, "sample_count": 8}
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "pool_wells(" in source
    reqs = requirements_from_spec(_spec(raw), skeleton_notes(source))
    (verdict,) = verify_all(_run(source, tmp_path / "p8.py"), reqs)
    assert verdict.status == "pass", verdict


def test_one_to_one_transfer_still_passes_as_transfer(profile, tmp_path):  # noqa: F811
    raw = {
        "title": "Reagent add and transfer", "sample_count": 96, "sample_volume_ul": 20,
        "reagents": [{"id": "S", "name": "Sample", "role": "sample"},
                     {"id": "MM", "name": "Master mix", "role": "reagent"}],
        "steps": [
            {"id": "s1", "op": "add", "text": "Add 10 ul master mix", "location": "deck",
             "reagent": "MM", "volume_ul": 10},
            {"id": "s3", "op": "transfer", "text": "Transfer 25 ul to a new plate", "location": "deck",
             "volume_ul": 25},
        ],
    }
    source = build_skeleton(_spec(raw), load_deck(profile))
    assert "stamp(" in source
    reqs = requirements_from_spec(_spec(raw), skeleton_notes(source))
    wt = _run(source, tmp_path / "t.py")
    tokens = _tokens(wt, reqs)
    assert "transfer" in tokens and "pool" not in tokens
    (verdict,) = verify_all(wt, reqs)
    assert verdict.status == "pass", verdict
