"""Plate transfers: MCA96 stamps, reagent additions, LiHa column pooling."""

from __future__ import annotations

import math
from typing import Iterable

from .common import (
    DEFAULT_MIX_LIQUID_CLASS,
    BlockError,
    BlockVariables,
    columns_or_all,
    require_distinct,
    require_positive,
    variable_prefix,
)


def stamp(
    wt,
    *,
    source,
    dest,
    volume_ul: float,
    tips,
    liquid_class: str,
    mix_cycles: int = 0,
    mix_volume_ul: float | None = None,
    mix_liquid_class: str = DEFAULT_MIX_LIQUID_CLASS,
    name: str | None = None,
    variables: bool = True,
) -> None:
    """Copy every well of ``source`` into the same well of ``dest`` (MCA96).

    Use for plate-to-plate transfers where well A1 goes to A1, B1 to B1, …
    (sample plate → reaction plate, barcode plate → sample plate). Optionally
    mixes ``dest`` afterwards with the same tips (each tip only ever meets its
    own sample, so this is contamination-free).

    ``tips`` is an MCA96 tip box used for this stamp only; reuse the same box
    later only for the same samples. The block mounts and drops the adapter.
    With a ``name``, volumes and liquid classes become FluentControl variables
    (``<NAME>_VOLUME_UL`` …) unless ``variables=False``.
    """
    require_positive("stamp", volume_ul=volume_ul)
    # More than the tips hold: equal trips with the same tips.
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 0.0)
    trips = math.ceil(float(volume_ul) / capacity) if capacity else 1
    if mix_cycles and mix_volume_ul is None:
        mix_volume_ul = 0.8 * float(volume_ul)
    if mix_cycles and capacity:
        mix_volume_ul = min(float(mix_volume_ul), 0.9 * capacity)
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="stamp")
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", round(float(volume_ul) / trips, 2))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    for _ in range(trips):
        head.aspirate(source, volume, liquid_class=lc)
        head.dispense(dest, volume, liquid_class=lc)
    if mix_cycles:
        head.mix(dest, v.ref("MIX_UL", float(mix_volume_ul)), cycles=mix_cycles,
                 liquid_class=v.ref("MIX_LIQUID_CLASS", mix_liquid_class))
    head.return_tips(tips)
    head.drop_adapter()


def liha_distribute(wt, *, source, plate, volume, volume_ul: float, tips, liquid_class, columns) -> None:
    """FCA (LiHa, 8 channels) dispenses ``volume`` into every well of ``columns``.

    One set of 8 tips serves all columns: the tips only aspirate the reagent
    and dispense from above, so they never touch sample. Volumes above the
    tip capacity go in equal trips (``volume`` is then the per-trip amount).
    """
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 200.0)
    trips = max(1, math.ceil(float(volume_ul) / capacity))
    head = wt.liha
    head.get_tips(tips)
    for column in columns:
        for _ in range(trips):
            head.aspirate(source, volume, liquid_class=liquid_class)
            head.dispense(plate, volume, liquid_class=liquid_class, well_offset=(column - 1) * 8)
    head.drop_tips()


def distribute_reagent(
    wt,
    *,
    source,
    plate,
    volume_ul: float,
    tips,
    liquid_class: str,
    columns: Iterable[int] | None = None,
    name: str | None = None,
    variables: bool = True,
) -> None:
    """Add a reagent to every well of ``plate`` with the FCA (LiHa), column by column.

    The standard way to add kit reagents, master mixes and buffers: the FCA
    pipettes from a slim trough (``25ml_short``, ``100ml`` catalogs) or tubes
    with little dead volume. Keep the MCA96 for cheap bulk liquids (water,
    ethanol, wash buffer) and for plate-to-plate work (:func:`stamp`).

    ``tips`` is an FCA tip box; one set of 8 tips is used for all columns
    (reagent-only contact, dispensed from above) and dropped at the end.
    """
    require_positive("distribute_reagent", volume_ul=volume_ul)
    cols = columns_or_all(columns)
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 200.0)
    trips = max(1, math.ceil(float(volume_ul) / capacity))
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="distribute_reagent")
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", round(float(volume_ul) / trips, 2))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    liha_distribute(wt, source=source, plate=plate, volume=volume, volume_ul=volume_ul, tips=tips,
                    liquid_class=lc, columns=cols)


def add_reagent(
    wt,
    *,
    reagent_source,
    plate,
    volume_ul: float,
    reagent_tips,
    liquid_class: str,
    mix_tips=None,
    mix_cycles: int = 0,
    mix_volume_ul: float | None = None,
    mix_liquid_class: str = DEFAULT_MIX_LIQUID_CLASS,
    name: str | None = None,
    variables: bool = True,
) -> None:
    """Add a reagent from a trough to every well of ``plate`` (MCA96).

    The ``reagent_tips`` only aspirate from ``reagent_source`` and dispense into
    the wells from above, so they stay clean and may be reused for the same
    reagent. To mix after adding, pass separate ``mix_tips`` (they touch the
    samples); mixing with the reagent tips would carry sample back into the
    reagent trough the next time they are used.
    """
    require_positive("add_reagent", volume_ul=volume_ul)
    if mix_cycles:
        if mix_tips is None:
            raise BlockError(
                "add_reagent: mixing touches the samples; pass a separate mix_tips box "
                "(not the reagent_tips)."
            )
        require_distinct("add_reagent", reagent_tips=reagent_tips, mix_tips=mix_tips)
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="add_reagent")
    capacity = float(getattr(reagent_tips, "capacity_ul", 0.0) or 0.0)
    trips = math.ceil(float(volume_ul) / capacity) if capacity else 1
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", round(float(volume_ul) / trips, 2))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(reagent_tips)
    for _ in range(trips):
        head.aspirate(reagent_source, volume, liquid_class=lc)
        head.dispense(plate, volume, liquid_class=lc)
    head.return_tips(reagent_tips)
    if mix_cycles:
        head.pick_up(mix_tips)
        head.mix(
            plate,
            v.ref("MIX_UL", float(mix_volume_ul if mix_volume_ul is not None else 0.8 * float(volume_ul))),
            cycles=mix_cycles,
            liquid_class=v.ref("MIX_LIQUID_CLASS", mix_liquid_class),
        )
        head.return_tips(mix_tips)
    head.drop_adapter()


def pool_columns(
    wt,
    *,
    source,
    dest,
    volume_ul: float,
    tips,
    liquid_class: str,
    dest_column: int = 1,
    columns: Iterable[int] | None = None,
    name: str | None = None,
    variables: bool = True,
) -> None:
    """Pool the columns of ``source`` into one column of ``dest`` (LiHa, 8 channels).

    Every source column is aspirated with fresh tips and dispensed into
    ``dest_column``, so ``dest`` row A ends up holding all row-A samples,
    row B all row-B samples, and so on: 8 row pools of
    ``len(columns) × volume_ul`` each. Combining the 8 row pools into one tube
    needs single-channel pipetting, which the LiHa API does not expose yet;
    follow with :func:`~fluentvibe.blocks.offdeck_step` if the protocol needs a
    single pool.

    ``tips`` is a LiHa (FCA) tip box; one set of 8 tips is used per column.
    """
    require_positive("pool_columns", volume_ul=volume_ul)
    cols = columns_or_all(columns)
    if not 1 <= int(dest_column) <= 12:
        raise BlockError(f"pool_columns: dest_column must be 1..12, got {dest_column!r}.")
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="pool_columns")
    volume = v.ref("VOLUME_UL", float(volume_ul))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.liha
    for column in cols:
        head.get_tips(tips)
        head.aspirate(source, volume, liquid_class=lc, well_offset=(column - 1) * 8)
        head.dispense(dest, volume, liquid_class=lc, well_offset=(int(dest_column) - 1) * 8)
        head.drop_tips()
