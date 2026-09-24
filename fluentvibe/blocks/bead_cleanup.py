"""SPRI / AMPure-style magnetic bead cleanup on the MCA96."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .common import (
    DEFAULT_EMPTY_TIP_LIQUID_CLASS,
    DEFAULT_MIX_LIQUID_CLASS,
    BlockError,
    BlockVariables,
    ensure_analyte_marker,
    require_distinct,
    require_positive,
    require_role,
    variable_prefix,
)


@dataclass(frozen=True)
class CleanupVolumes:
    """Volumes a cleanup derived from its inputs (µL, per well)."""

    bead_ul: float
    supernatant_ul: float
    wash_ul: float
    elution_ul: float
    eluate_transfer_ul: float


def spri_cleanup(
    wt,
    *,
    sample_plate,
    magnet,
    bead_source,
    wash_source,
    elution_source,
    waste,
    eluate_plate,
    reagent_tips,
    sample_tips,
    eluate_tips,
    sample_volume_ul: float,
    elution_volume_ul: float,
    liquid_class: str,
    bead_ratio: float | None = None,
    bead_volume_ul: float | None = None,
    wash_volume_ul: float = 150.0,
    wash_count: int = 2,
    retain_volume_ul: float = 2.0,
    bind_mix_cycles: int = 10,
    elution_mix_cycles: int = 10,
    bind_seconds: int = 300,
    settle_seconds: int = 120,
    dry_seconds: int = 120,
    elute_seconds: int = 120,
    wash_liquid_class: str | None = None,
    mix_liquid_class: str = DEFAULT_MIX_LIQUID_CLASS,
    empty_liquid_class: str = DEFAULT_EMPTY_TIP_LIQUID_CLASS,
    name: str = "Bead cleanup",
    variables: bool = True,
) -> CleanupVolumes:
    """Full-plate magnetic bead cleanup: bind, wash, elute off-magnet, recover.

    Stages (each its own ``wt.group``): add beads and mix to bind → incubate →
    plate onto ``magnet`` → remove supernatant to ``waste`` → ``wash_count``
    washes (add ``wash_source``, remove) → air-dry → plate off the magnet → add
    ``elution_source`` and mix → incubate → plate back onto the magnet → move
    the cleared eluate to ``eluate_plate`` → plate back to its home slot.

    Give either ``bead_ratio`` (e.g. 1.8 for 1.8×) or ``bead_volume_ul``.
    Mixing uses ``mix_liquid_class`` (a class with a Mix section, default
    ``"Water Mix"``); FluentControl rejects mixing with a transfer-only class.
    Emptying tips into ``waste`` uses ``empty_liquid_class`` (``"Empty Tip"``).
    Derived per well: supernatant = sample + beads − retain; eluate transfer =
    elution − retain (``retain_volume_ul`` stays behind so the pellet is not
    disturbed).

    Preconditions (a :class:`BlockError` says what is missing):

    * ``sample_plate`` wells contain a ``Reagent(..., role="analyte")``;
    * ``bead_source`` holds a ``role="bead_carrier"`` reagent, ``elution_source``
      a ``role="eluent"`` reagent, ``wash_source`` e.g. 80% ethanol;
    * ``reagent_tips``, ``sample_tips`` and ``eluate_tips`` are three different
      MCA96 tip boxes; the MCA adapter is **not** mounted (the block mounts and
      drops it).

    If the sample wells are mostly analyte, the block re-expresses the excess
    as plain sample liquid (see :func:`~fluentvibe.blocks.common.ensure_analyte_marker`);
    total volumes are unchanged.

    Volumes, times and liquid classes are declared as FluentControl variables
    named after ``name`` (``"PCR clean-up"`` → ``PCR_CLEAN_UP_BEAD_VOLUME_UL`` …),
    so they stay editable in FluentControl; pass ``variables=False`` for plain
    literals. Two cleanups need different ``name`` values.

    Returns the derived :class:`CleanupVolumes`.
    """
    block = "spri_cleanup"
    if (bead_ratio is None) == (bead_volume_ul is None):
        raise BlockError(f"{block}: give exactly one of bead_ratio or bead_volume_ul.")
    bead_ul = float(bead_volume_ul if bead_volume_ul is not None else sample_volume_ul * bead_ratio)
    require_positive(
        block,
        sample_volume_ul=sample_volume_ul,
        bead_volume=bead_ul,
        elution_volume_ul=elution_volume_ul,
        wash_volume_ul=wash_volume_ul,
    )
    if wash_count < 0:
        raise BlockError(f"{block}: wash_count must be 0 or more, got {wash_count!r}.")
    if not 0 <= retain_volume_ul < elution_volume_ul:
        raise BlockError(
            f"{block}: retain_volume_ul ({retain_volume_ul}) must be ≥ 0 and smaller than "
            f"elution_volume_ul ({elution_volume_ul})."
        )
    require_distinct(block, reagent_tips=reagent_tips, sample_tips=sample_tips, eluate_tips=eluate_tips)
    require_role(sample_plate, "analyte", param="sample_plate", block=block, wt=wt)
    require_role(bead_source, "bead_carrier", param="bead_source", block=block)
    require_role(elution_source, "eluent", param="elution_source", block=block)
    ensure_analyte_marker(sample_plate)
    home = sample_plate.slot
    if home is None:
        raise BlockError(f"{block}: place sample_plate with wt.place(...) before the cleanup.")

    volumes = CleanupVolumes(
        bead_ul=bead_ul,
        supernatant_ul=float(sample_volume_ul) + bead_ul - float(retain_volume_ul),
        wash_ul=float(wash_volume_ul),
        elution_ul=float(elution_volume_ul),
        eluate_transfer_ul=float(elution_volume_ul) - float(retain_volume_ul),
    )
    # Mix 80% of the well volume, capped by what the sample tips can hold.
    tip_capacity = float(getattr(sample_tips, "capacity_ul", 0.0) or 200.0)
    bind_mix_ul = 0.8 * min(float(sample_volume_ul) + bead_ul, tip_capacity)
    elution_mix_ul = 0.8 * min(float(elution_volume_ul), tip_capacity)
    head = wt.mca96

    # Every volume, time and liquid class becomes a FluentControl variable
    # (``<NAME>_…``) unless variables=False, so the run stays editable in FC.
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block=block)
    # Volumes above what the tips hold go in equal trips with the same tips.
    reagent_capacity = float(getattr(reagent_tips, "capacity_ul", 0.0) or 200.0)
    bead_trips = math.ceil(volumes.bead_ul / reagent_capacity)
    supernatant_trips = math.ceil(volumes.supernatant_ul / tip_capacity)
    bead = v.ref("BEAD_VOLUME_UL" if bead_trips == 1 else "BEAD_TRIP_UL", round(volumes.bead_ul / bead_trips, 2))
    supernatant = v.ref("SUPERNATANT_UL" if supernatant_trips == 1 else "SUPERNATANT_TRIP_UL",
                        round(volumes.supernatant_ul / supernatant_trips, 2))
    wash = v.ref("WASH_VOLUME_UL", volumes.wash_ul)
    elution = v.ref("ELUTION_VOLUME_UL", volumes.elution_ul)
    eluate = v.ref("ELUATE_TRANSFER_UL", volumes.eluate_transfer_ul)
    bind_mix = v.ref("BIND_MIX_UL", bind_mix_ul)
    elution_mix = v.ref("ELUTION_MIX_UL", elution_mix_ul)
    lc = v.ref("LIQUID_CLASS", liquid_class)
    wash_lc = v.ref("WASH_LIQUID_CLASS", wash_liquid_class or liquid_class)
    mix_lc = v.ref("MIX_LIQUID_CLASS", mix_liquid_class)
    empty_lc = v.ref("EMPTY_LIQUID_CLASS", empty_liquid_class)
    bind_time = v.ref("BIND_SECONDS", bind_seconds)
    settle_time = v.ref("SETTLE_SECONDS", settle_seconds)
    dry_time = v.ref("DRY_SECONDS", dry_seconds)
    elute_time = v.ref("ELUTE_SECONDS", elute_seconds)

    wt.group(f"{name} - bind")
    head.mount_adapter()
    head.pick_up(reagent_tips)
    for _ in range(bead_trips):
        head.aspirate(bead_source, bead, liquid_class=lc)
        head.dispense(sample_plate, bead, liquid_class=lc)
    head.return_tips(reagent_tips)
    head.pick_up(sample_tips)
    head.mix(sample_plate, bind_mix, cycles=bind_mix_cycles, liquid_class=mix_lc)
    head.return_tips(sample_tips)
    wt.wait(duration_seconds=bind_time)

    wt.group(f"{name} - separate and remove supernatant")
    wt.gripper.move(sample_plate, onto=magnet)
    wt.wait(duration_seconds=settle_time)
    head.pick_up(sample_tips)
    for _ in range(supernatant_trips):
        head.aspirate(sample_plate, supernatant, liquid_class=lc)
        head.empty_tips(waste, supernatant, liquid_class=empty_lc)
    head.return_tips(sample_tips)

    for index in range(1, wash_count + 1):
        wt.group(f"{name} - wash {index}")
        head.pick_up(reagent_tips)
        head.aspirate(wash_source, wash, liquid_class=wash_lc)
        head.dispense(sample_plate, wash, liquid_class=wash_lc)
        head.return_tips(reagent_tips)
        wt.wait(duration_seconds=30)
        head.pick_up(sample_tips)
        head.aspirate(sample_plate, wash, liquid_class=wash_lc)
        head.empty_tips(waste, wash, liquid_class=empty_lc)
        head.return_tips(sample_tips)
    if wash_count:
        wt.wait(duration_seconds=dry_time)

    wt.group(f"{name} - elute off magnet")
    wt.gripper.move(sample_plate, to=home)
    head.pick_up(reagent_tips)
    head.aspirate(elution_source, elution, liquid_class=lc)
    head.dispense(sample_plate, elution, liquid_class=lc)
    head.return_tips(reagent_tips)
    head.pick_up(sample_tips)
    head.mix(sample_plate, elution_mix, cycles=elution_mix_cycles, liquid_class=mix_lc)
    head.return_tips(sample_tips)
    wt.wait(duration_seconds=elute_time)

    wt.group(f"{name} - recover eluate")
    wt.gripper.move(sample_plate, onto=magnet)
    wt.wait(duration_seconds=settle_time)
    head.pick_up(eluate_tips)
    head.aspirate(sample_plate, eluate, liquid_class=lc)
    head.dispense(eluate_plate, eluate, liquid_class=lc)
    head.return_tips(eluate_tips)
    head.drop_adapter()
    wt.gripper.move(sample_plate, to=home)
    return volumes
