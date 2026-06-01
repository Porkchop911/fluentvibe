"""Idempotent upsert of authoring rules the model needs to follow SPRI/AMPure SOPs.

Run once: PYTHONPATH=. python scripts/seed_authoring_rules.py

Rules go through `fluentvibe.catalog.database.CatalogDatabase.upsert_rule`. The
catalog DB is committed in `fluentvibe/catalog/tecan.db`, so a single run
persists across sessions. Re-running the script is a no-op.
"""

from __future__ import annotations

import json
import sys

from fluentvibe.catalog import get_database


RULES: list[dict] = [
    {
        "name": "ampure_keep_plate_on_magnet_during_washes",
        "rule_type": "workflow",
        "category": "workflow",
        "protocol_type": "bead_cleanup",
        "description": (
            "During AMPure / SPRI bead cleanup, the sample plate stays on the magnet "
            "rack for EVERY aspirate that follows the first magnet capture: supernatant "
            "removal, both ethanol aspirates after each wash, AND the final eluate "
            "transfer. The plate is moved OFF the magnet exactly once -- immediately "
            "before adding elution buffer -- and moved back ON the magnet for the final "
            "1-min capture before transferring eluate. Do not insert wt.gripper.move(plate, "
            "to=...) calls before any other aspirate; doing so resuspends the beads."
        ),
        "requirements": json.dumps({
            "magnet_engaged_during_supernatant_aspirate": True,
            "magnet_engaged_during_ethanol_aspirate": True,
            "magnet_engaged_during_eluate_transfer": True,
            "off_magnet_only_for": ["add_elution_buffer", "elution_mix_and_incubate"],
        }),
        "severity": "hard",
        "confidence": 0.95,
    },
    {
        "name": "ampure_mix_after_bead_addition_uses_mca",
        "rule_type": "workflow",
        "category": "workflow",
        "protocol_type": "bead_cleanup",
        "description": (
            "After dispensing AMPure XP beads into the sample plate, pipette-mix with "
            "wt.mca96 (NOT wt.liha) for 10 cycles so all 96 wells mix in parallel. "
            "Pattern: mca.mount_adapter() -> mca.pick_up(mca_tips) -> "
            "mca.mix(source_plate, PCR_SAMPLE_UL + BEAD_VOLUME_UL, cycles=10, "
            "liquid_class=LIQUID_CLASS_BEADS) -> mca.return_tips(mca_tips). The same "
            "pattern applies after elution buffer addition (use LIQUID_CLASS_ELUTION_BUFFER)."
        ),
        "requirements": json.dumps({
            "mix_head": "mca96",
            "mix_cycles": 10,
            "mix_volume_expression": "PCR_SAMPLE_UL + BEAD_VOLUME_UL",
            "applies_to": ["after_bead_addition", "after_elution_buffer_addition"],
        }),
        "severity": "hard",
        "confidence": 0.9,
    },
    {
        "name": "ampure_separate_tip_box_for_eluate",
        "rule_type": "best_practice",
        "category": "tips",
        "protocol_type": "bead_cleanup",
        "description": (
            "Place TWO MCA200Box labware objects: one for waste and wash operations, "
            "one used exclusively for the final eluate transfer. Re-using the same tip "
            "box across waste and eluate causes bead carryover. Name the second box "
            "something like 'MCATipsEluate' and pick up only from it inside the "
            "Transfer Eluate group."
        ),
        "requirements": json.dumps({
            "mca_tip_boxes_required": 2,
            "eluate_tip_box_used_only_in": "transfer_eluate_group",
        }),
        "severity": "hard",
        "confidence": 0.85,
    },
    {
        "name": "ampure_derived_volumes_in_python",
        "rule_type": "best_practice",
        "category": "general",
        "protocol_type": "bead_cleanup",
        "description": (
            "Dependent volumes must be derived from primary variables in python "
            "expressions BEFORE the wt.declare_variable call -- never hardcoded. "
            "Example: PCR_SAMPLE_UL = 20.0; BEAD_VOLUME_UL = 36.0; RESIDUAL_SUPERNATANT_UL "
            "= 5.0; SUPERNATANT_ASPIRATE_UL = PCR_SAMPLE_UL + BEAD_VOLUME_UL - "
            "RESIDUAL_SUPERNATANT_UL. Then call wt.declare_variable for each name with "
            "the computed numeric value. This keeps the protocol parameterizable: "
            "changing PCR_SAMPLE_UL alone re-derives every dependent volume."
        ),
        "requirements": json.dumps({
            "primary_variables": [
                "PCR_SAMPLE_UL",
                "BEAD_VOLUME_UL",
                "RESIDUAL_SUPERNATANT_UL",
                "ETHANOL_WASH_UL",
                "ELUTION_BUFFER_UL",
            ],
            "derived_examples": {
                "SUPERNATANT_ASPIRATE_UL": "PCR_SAMPLE_UL + BEAD_VOLUME_UL - RESIDUAL_SUPERNATANT_UL",
                "WASH_ASPIRATE_UL": "ETHANOL_WASH_UL - 5.0",
            },
        }),
        "severity": "hard",
        "confidence": 0.85,
    },
    {
        "name": "ampure_per_role_liquid_class_variables",
        "rule_type": "best_practice",
        "category": "liquid_class",
        "protocol_type": "bead_cleanup",
        "description": (
            "Declare one string variable per reagent role -- LIQUID_CLASS_BEADS, "
            "LIQUID_CLASS_ETHANOL, LIQUID_CLASS_SUPERNATANT, LIQUID_CLASS_ELUATE, "
            "LIQUID_CLASS_ELUTION_BUFFER -- even when all initial values are the same. "
            "Each pipetting call passes the role-specific variable. This lets the lab "
            "tech tune one role's class on the fly without rewriting the protocol."
        ),
        "requirements": json.dumps({
            "liquid_class_variables": [
                "LIQUID_CLASS_BEADS",
                "LIQUID_CLASS_ETHANOL",
                "LIQUID_CLASS_SUPERNATANT",
                "LIQUID_CLASS_ELUATE",
                "LIQUID_CLASS_ELUTION_BUFFER",
            ],
            "do_not_collapse_to_single_variable": True,
        }),
        "severity": "hard",
        "confidence": 0.9,
    },
]


def main() -> int:
    db = get_database()
    upserted = []
    for rule in RULES:
        ok = db.upsert_rule(rule)
        upserted.append((rule["name"], ok))
    for name, ok in upserted:
        marker = "OK" if ok else "FAIL"
        print(f"  [{marker}] {name}")
    print(f"\nupserted {sum(1 for _, ok in upserted if ok)}/{len(upserted)} rules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
