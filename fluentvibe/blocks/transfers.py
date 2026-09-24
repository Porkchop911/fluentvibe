"""Plate transfers: MCA96 stamps, reagent additions, LiHa column pooling."""

from __future__ import annotations

from typing import Iterable

from .common import (
    DEFAULT_MIX_LIQUID_CLASS,
    BlockError,
    columns_or_all,
    require_distinct,
    require_positive,
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
) -> None:
    """Copy every well of ``source`` into the same well of ``dest`` (MCA96).

    Use for plate-to-plate transfers where well A1 goes to A1, B1 to B1, …
    (sample plate → reaction plate, barcode plate → sample plate). Optionally
    mixes ``dest`` afterwards with the same tips (each tip only ever meets its
    own sample, so this is contamination-free).

    ``tips`` is an MCA96 tip box used for this stamp only; reuse the same box
    later only for the same samples. The block mounts and drops the adapter.
    """
    require_positive("stamp", volume_ul=volume_ul)
    if mix_cycles and mix_volume_ul is None:
        mix_volume_ul = 0.8 * float(volume_ul)
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(source, volume_ul, liquid_class=liquid_class)
    head.dispense(dest, volume_ul, liquid_class=liquid_class)
    if mix_cycles:
        head.mix(dest, mix_volume_ul, cycles=mix_cycles, liquid_class=mix_liquid_class)
    head.return_tips(tips)
    head.drop_adapter()


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
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(reagent_tips)
    head.aspirate(reagent_source, volume_ul, liquid_class=liquid_class)
    head.dispense(plate, volume_ul, liquid_class=liquid_class)
    head.return_tips(reagent_tips)
    if mix_cycles:
        head.pick_up(mix_tips)
        head.mix(
            plate,
            mix_volume_ul if mix_volume_ul is not None else 0.8 * float(volume_ul),
            cycles=mix_cycles,
            liquid_class=mix_liquid_class,
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
    if name:
        wt.group(name)
    head = wt.liha
    for column in cols:
        head.get_tips(tips)
        head.aspirate(source, volume_ul, liquid_class=liquid_class, well_offset=(column - 1) * 8)
        head.dispense(
            dest, volume_ul, liquid_class=liquid_class, well_offset=(int(dest_column) - 1) * 8
        )
        head.drop_tips()
