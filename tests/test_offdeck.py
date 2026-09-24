"""Off-deck steps mid-protocol must pause for the operator."""

from __future__ import annotations

from fluentvibe.authoring.eval_rubric import score_source
from fluentvibe.authoring.offdeck import offdeck_findings

_TEMPLATE = '''
def build_worktable():
    wt.group("Prep")
    wt.add_comment("Before starting: keep reagents on ice.")
    head.aspirate(src, 10.0)
    head.dispense(dst, 10.0)
    wt.group("Tagmentation")
    wt.add_comment("Incubate on a thermal cycler at 30 C for 2 min, then 80 C for 2 min.")
    {pause}
    wt.group("Cleanup")
    head.aspirate(dst, 10.0)
    head.dispense(out, 10.0)
    wt.group("Loading (manual)")
    wt.add_comment("Load the library onto the flow cell off-deck.")
'''


def _source(pause: str) -> str:
    return _TEMPLATE.format(pause=pause)


def test_thermal_cycler_modelled_as_wait_is_flagged():
    findings = offdeck_findings(_source("wt.wait(duration_seconds=240)"))
    assert [(f.group, f.line) for f in findings] == [("Tagmentation", 8)]
    inv = next(i for i in score_source(_source("wt.wait(duration_seconds=240)"))
               if i.key == "offdeck_steps_prompted")
    assert inv.status == "fail"
    assert "Tagmentation" in inv.evidence


def test_user_prompt_in_the_group_passes():
    src = _source('wt.user_prompt("Move the plate to the thermal cycler, run TAG, return it.")')
    assert offdeck_findings(src) == []


def test_pre_run_prep_and_post_run_manual_steps_are_not_flagged():
    # "on ice" before the first liquid handling and the flow-cell loading after
    # the last one are outside the run; only the mid-run thermal step counts.
    groups = {f.group for f in offdeck_findings(_source("wt.wait(duration_seconds=240)"))}
    assert "Prep" not in groups
    assert "Loading (manual)" not in groups


def test_on_deck_incubation_is_not_off_deck():
    src = _source("wt.wait(duration_seconds=240)").replace(
        "Incubate on a thermal cycler at 30 C for 2 min, then 80 C for 2 min.",
        "Incubate 10 min at room temperature.",
    )
    assert offdeck_findings(src) == []


def test_integrated_thermal_cycler_counts_as_handled():
    src = _source(
        'wt.odtc_open_door()\n'
        '    wt.gripper.move(dst, to=("ODTC", 1))\n'
        '    wt.odtc_close_door()\n'
        '    wt.odtc_execute_method("TAG_30C_80C")'
    )
    assert offdeck_findings(src) == []


def test_offdeck_step_block_counts_as_handled_and_blocks_count_as_liquid():
    src = _source('offdeck_step(wt, "Run TAG on the thermal cycler.", labware=dst, handoff=("Nest61mm_Pos", 10))')
    assert offdeck_findings(src) == []
    # A block call is liquid handling: an unprompted off-deck step between two
    # block calls is still flagged.
    blocks_only = '''
def build_worktable():
    wt.group("Barcode")
    stamp(wt, source=bc, dest=samples, volume_ul=1.0, tips=t, liquid_class="x")
    wt.group("Tagmentation")
    wt.add_comment("Incubate on a thermal cycler.")
    wt.wait(duration_seconds=240)
    wt.group("Cleanup")
    spri_cleanup(wt, sample_plate=samples)
'''
    assert [f.group for f in offdeck_findings(blocks_only)] == ["Tagmentation"]
