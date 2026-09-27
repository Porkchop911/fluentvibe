"""Physical primitives: remove liquid, mix, magnet on / off.

Together with :func:`~fluentvibe.blocks.distribute_reagent` /
:func:`~fluentvibe.blocks.add_reagent` (add), :func:`~fluentvibe.blocks.stamp` /
:func:`~fluentvibe.blocks.pool_columns` (transfer), ``wt.wait`` (incubate) and
:func:`~fluentvibe.blocks.offdeck_step` (operator) these describe any
plate-based protocol as a sequence of physical actions. They carry no chemistry:
a SPRI clean-up, a streptavidin bead wash and a supernatant discard are all
different orders of the same primitives. :func:`~fluentvibe.blocks.spri_cleanup`
is one such order packaged as a macro.
"""

from __future__ import annotations

import math

from typing import Iterable

from ..variables import num, per_trip, vround
from .common import (
    DEFAULT_EMPTY_TIP_LIQUID_CLASS,
    DEFAULT_MIX_LIQUID_CLASS,
    BlockError,
    BlockVariables,
    mca_columns,
    require_positive,
    variable_prefix,
)


def remove_liquid(
    wt,
    *,
    plate,
    waste,
    volume_ul: float,
    tips,
    liquid_class: str,
    empty_liquid_class: str = DEFAULT_EMPTY_TIP_LIQUID_CLASS,
    name: str | None = None,
    variables: bool = True,
    columns: Iterable[int] | None = None,
) -> None:
    """Take ``volume_ul`` out of every well of ``plate`` into ``waste`` (MCA96).

    Supernatant removal, wash discard, buffer exchange. On a magnet the beads
    (and what is bound to them) stay in the well; off the magnet a draw takes
    suspended beads along. ``tips`` touch the samples: use the plate's own
    sample tip box (on the MCA96 channel *i* only ever meets well *i*). Volumes
    above what the tips hold go in equal trips. ``columns`` restricts it to
    those plate columns (a partial plate).
    """
    require_positive("remove_liquid", volume_ul=volume_ul)
    cols = mca_columns(columns)
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 200.0)
    trips = math.ceil(float(volume_ul) / capacity)
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="remove_liquid")
    volume = v.ref("VOLUME_UL" if trips == 1 else "TRIP_VOLUME_UL", per_trip(volume_ul, trips))
    lc = v.ref("LIQUID_CLASS", liquid_class)
    empty_lc = v.ref("EMPTY_LIQUID_CLASS", empty_liquid_class)
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips, columns=cols)
    for _ in range(trips):
        head.aspirate(plate, volume, liquid_class=lc, columns=cols)
        head.empty_tips(waste, volume, liquid_class=empty_lc)
    head.return_tips(tips, columns=cols)
    head.drop_adapter()


def mix_wells(
    wt,
    *,
    plate,
    tips,
    volume_ul: float,
    cycles: int = 10,
    liquid_class: str = DEFAULT_MIX_LIQUID_CLASS,
    name: str | None = None,
    variables: bool = True,
    columns: Iterable[int] | None = None,
) -> None:
    """Mix every well of ``plate`` in place (MCA96): resuspend beads, mix a reaction.

    ``tips`` touch the samples (use the plate's sample tip box). ``volume_ul``
    is capped at 90% of what the tips hold. ``liquid_class`` needs a Mix
    section (default ``"Water Mix"``).
    """
    require_positive("mix_wells", volume_ul=volume_ul)
    cols = mca_columns(columns)
    if int(cycles) < 1:
        raise BlockError(f"mix_wells: cycles must be 1 or more, got {cycles!r}.")
    capacity = float(getattr(tips, "capacity_ul", 0.0) or 200.0)
    mix_ul = vround(min(num(volume_ul), 0.9 * capacity))
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="mix_wells")
    volume = v.ref("MIX_UL", mix_ul)
    lc = v.ref("MIX_LIQUID_CLASS", liquid_class)
    count = v.ref("MIX_CYCLES", int(cycles))
    if name:
        wt.group(name)
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips, columns=cols)
    head.mix(plate, volume, cycles=count, liquid_class=lc, columns=cols)
    head.return_tips(tips, columns=cols)
    head.drop_adapter()


def separate(wt, *, plate, magnet, settle_seconds: int = 120, name: str | None = None,
             variables: bool = True) -> None:
    """Magnet on: the gripper puts ``plate`` onto ``magnet`` and the beads settle.

    While the plate sits on the magnet, :func:`remove_liquid` leaves the beads
    (and what is bound to them) behind. Take it off with :func:`release`.
    """
    if magnet is None or getattr(magnet, "slot", None) is None:
        raise BlockError("separate: place the magnet with wt.place(...) first.")
    v = BlockVariables(wt, variable_prefix(name) if variables else None, block="separate")
    settle = v.ref("SETTLE_SECONDS", int(settle_seconds))
    if name:
        wt.group(name)
    wt.gripper.move(plate, onto=magnet)
    wt.wait(duration_seconds=settle)


def release(wt, *, plate, to: tuple[str, int], name: str | None = None) -> None:
    """Magnet off: the gripper moves ``plate`` from the magnet back to ``to``
    (its home position), so the beads can be resuspended."""
    if not to:
        raise BlockError("release: give the position to move the plate back to (its home slot).")
    if name:
        wt.group(name)
    wt.gripper.move(plate, to=tuple(to))
