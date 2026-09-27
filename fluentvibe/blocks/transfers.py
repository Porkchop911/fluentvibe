"""Plate transfers: MCA96 stamps, reagent additions, LiHa column pooling."""

from __future__ import annotations

import math
from typing import Iterable

from ..variables import num, per_trip
from .common import (
    DEFAULT_MIX_LIQUID_CLASS,
    BlockError,
    BlockVariables,
    columns_or_all,
    mca_columns,
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
    columns: Iterable[int] | None = None,
) -> None:
    """Copy every well of ``source`` into the same well of ``dest`` (MCA96).

    Use for plate-to-plate transfers where well A1 goes to A1, B1 to B1, …
    (sample plate → reaction plate, barcode plate → sample plate). Optionally
    mixes ``dest`` afterwards with the same tips (each tip only ever meets its
    own sample, so this is contamination-free).

    ``tips`` is an MCA96 tip box used for this stamp only; reuse the same box
    later only for the same samples. The block mounts and drops the adapter.
    With a ``name``, volumes and liquid classes become FluentControl variables
    (``<NAME>_VOLUME_UL`` …) unless ``variables=False``. ``columns`` (1-based)
    restricts it to those plate columns (a partial plate); the same box columns
    of ``tips`` are used.
    """
    require_positive("stamp", volume_ul=volume_ul)
    cols = mca_columns(columns)
    # More than the tips hold: equal trips with the same tips.
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 0.0)
    trips = math.ceil(float(volume_ul) / capacity) if capacity else 1
    if mix_cycles and mix_volume_ul is None:
        mix_volume_ul = 0.8 * num(volume_ul)
    if mix_cycles and capacity:
        mix_volume_ul = min(num(mix_volume_ul), 0.9 * capacity)
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="stamp")
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", per_trip(volume_ul, trips))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips, columns=cols)
    for _ in range(trips):
        head.aspirate(source, volume, liquid_class=lc, columns=cols)
        head.dispense(dest, volume, liquid_class=lc, columns=cols)
    if mix_cycles:
        head.mix(dest, v.ref("MIX_UL", num(mix_volume_ul)), cycles=mix_cycles,
                 liquid_class=v.ref("MIX_LIQUID_CLASS", mix_liquid_class), columns=cols)
    head.return_tips(tips, columns=cols)
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
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", per_trip(volume_ul, trips))
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
    columns: Iterable[int] | None = None,
) -> None:
    """Add a reagent from a trough to every well of ``plate`` (MCA96).

    The ``reagent_tips`` only aspirate from ``reagent_source`` and dispense into
    the wells from above, so they stay clean and may be reused for the same
    reagent. To mix after adding, pass separate ``mix_tips`` (they touch the
    samples); mixing with the reagent tips would carry sample back into the
    reagent trough the next time they are used.
    """
    require_positive("add_reagent", volume_ul=volume_ul)
    cols = mca_columns(columns)
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
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", per_trip(volume_ul, trips))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(reagent_tips, columns=cols)
    for _ in range(trips):
        head.aspirate(reagent_source, volume, liquid_class=lc)
        head.dispense(plate, volume, liquid_class=lc, columns=cols)
    head.return_tips(reagent_tips, columns=cols)
    if mix_cycles:
        head.pick_up(mix_tips, columns=cols)
        head.mix(
            plate,
            v.ref("MIX_UL", num(mix_volume_ul if mix_volume_ul is not None else 0.8 * num(volume_ul))),
            cycles=mix_cycles,
            liquid_class=v.ref("MIX_LIQUID_CLASS", mix_liquid_class),
            columns=cols,
        )
        head.return_tips(mix_tips, columns=cols)
    head.drop_adapter()


def _by_column(volumes: dict[str, float]) -> list[list[tuple[str, float]]]:
    columns: dict[int, list[tuple[str, float]]] = {}
    for well, volume in volumes.items():
        address = str(well).strip().upper()
        if float(volume) <= 0:
            continue
        columns.setdefault(int(address[1:]), []).append((address, num(volume)))
    return [sorted(columns[c], key=lambda item: item[0][0]) for c in sorted(columns)]


def distribute_volumes(
    wt,
    *,
    source,
    plate,
    volumes: dict[str, float],
    tips,
    liquid_class: str,
    name: str | None = None,
) -> None:
    """Add a different volume of a reagent to each well (normalisation, dilution
    series) with the FCA (LiHa): ``volumes`` maps well -> ul.

    Column by column, each well is served by the channel of its row, with its
    own volume (FluentControl stores one volume per channel). One tip set for
    all columns: the tips only take reagent and dispense from above. Volumes
    above the tip capacity go in equal trips. ``tips`` is an FCA tip box.
    """
    batches = _by_column(volumes)
    if not batches:
        raise BlockError("distribute_volumes: give at least one well with a volume above 0.")
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 200.0)
    if name:
        wt.group(name)
    head = wt.liha
    head.get_tips(tips)
    for batch in batches:
        wells = [w for w, _ in batch]
        channels = [ord(w[0]) - ord("A") for w in wells]
        trips = max(1, math.ceil(max(v for _, v in batch) / capacity))
        per_trip_ul = [per_trip(v, trips) for _, v in batch]
        for _ in range(trips):
            head.aspirate(source, per_trip_ul[0], liquid_class=liquid_class, wells=["A1"] * len(wells),
                          volumes=per_trip_ul, channels=channels)
            head.dispense(plate, per_trip_ul[0], liquid_class=liquid_class, wells=wells,
                          volumes=per_trip_ul, channels=channels)
    head.drop_tips()


def transfer_volumes(
    wt,
    *,
    source,
    dest,
    volumes: dict[str, float],
    tips,
    liquid_class: str,
    name: str | None = None,
) -> None:
    """Move a different volume from each well of ``source`` to the same well of
    ``dest`` (e.g. normalising samples) with the FCA (LiHa): ``volumes`` maps
    well -> ul. Column by column, fresh tips per column (they touch samples),
    each well on the channel of its row. ``tips`` is an FCA tip box.
    """
    batches = _by_column(volumes)
    if not batches:
        raise BlockError("transfer_volumes: give at least one well with a volume above 0.")
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 200.0)
    if name:
        wt.group(name)
    head = wt.liha
    for batch in batches:
        wells = [w for w, _ in batch]
        channels = [ord(w[0]) - ord("A") for w in wells]
        trips = max(1, math.ceil(max(v for _, v in batch) / capacity))
        per_trip_ul = [per_trip(v, trips) for _, v in batch]
        head.get_tips(tips)
        for _ in range(trips):
            head.aspirate(source, per_trip_ul[0], liquid_class=liquid_class, wells=wells, volumes=per_trip_ul,
                          channels=channels)
            head.dispense(dest, per_trip_ul[0], liquid_class=liquid_class, wells=wells, volumes=per_trip_ul,
                          channels=channels)
        head.drop_tips()


def pool_wells(
    wt,
    *,
    source,
    dest,
    volume_ul: float,
    tips,
    liquid_class: str,
    source_wells: Iterable[str],
    dest_well: str = "A1",
    name: str | None = None,
    variables: bool = True,
) -> None:
    """Pool ``volume_ul`` from each of ``source_wells`` into one well of ``dest`` (LiHa).

    The wells of each source column go in one pass (one channel per well, up
    to 8), all dispensed into ``dest_well``; every pass uses fresh FCA tips, so
    no tip touches two samples before the pool. Use it for a handful of samples
    (a partial plate) or any explicit set of wells; :func:`pool_columns` pools
    whole plate columns into 8 row pools instead.

    ``tips`` is an FCA tip box.
    """
    require_positive("pool_wells", volume_ul=volume_ul)
    wells = [str(w).strip().upper() for w in source_wells]
    if not wells:
        raise BlockError("pool_wells: give the source_wells to pool.")
    by_column: dict[str, list[str]] = {}
    for well in wells:
        by_column.setdefault(well[1:], []).append(well)
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="pool_wells")
    volume = v.ref("VOLUME_UL", num(volume_ul))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.liha
    target = str(dest_well).strip().upper()
    for column_wells in by_column.values():
        for start in range(0, len(column_wells), 8):
            batch = column_wells[start:start + 8]
            head.get_tips(tips)
            head.aspirate(source, volume, liquid_class=lc, wells=batch)
            head.dispense(dest, volume, liquid_class=lc, wells=[target] * len(batch))
            head.drop_tips()


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
    volume = v.ref("VOLUME_UL", num(volume_ul))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    if name:
        wt.group(name)
    head = wt.liha
    for column in cols:
        head.get_tips(tips)
        head.aspirate(source, volume, liquid_class=lc, well_offset=(column - 1) * 8)
        head.dispense(dest, volume, liquid_class=lc, well_offset=(int(dest_column) - 1) * 8)
        head.drop_tips()
