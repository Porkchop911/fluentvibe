"""Operator hand-offs for steps that happen away from the deck."""

from __future__ import annotations

from .common import BlockError


def offdeck_step(
    wt,
    instruction: str,
    *,
    labware=None,
    handoff: tuple[str, int] | None = None,
    return_to: tuple[str, int] | None = None,
    name: str | None = None,
) -> None:
    """Pause the run so the operator can do a step away from the deck.

    Use for a thermal cycler that is not on the deck, a centrifuge spin, a
    Qubit reading, keeping something on ice. A ``wt.wait()`` would leave the
    plate on the deck and not model the step at all.

    If ``labware`` and ``handoff`` are given, the gripper first moves the
    labware to the hand-off position (a slot the operator can reach), the run
    pauses with ``instruction``, and afterwards the labware goes back to
    ``return_to`` (default: its home slot). Without them the run just pauses.

    When the deck has an integrated device for the step (e.g. an Inheco ODTC
    thermal cycler) drive the device with ``wt.odtc_*`` instead.
    """
    if not str(instruction).strip():
        raise BlockError("offdeck_step: instruction must say what the operator does.")
    if (labware is None) != (handoff is None):
        raise BlockError("offdeck_step: give both labware and handoff, or neither.")
    if name:
        wt.group(name)
    home = None
    if labware is not None:
        home = return_to or labware.slot
        if home is None:
            raise BlockError("offdeck_step: place the labware with wt.place(...) first.")
        wt.gripper.move(labware, to=handoff)
    wt.user_prompt(str(instruction))
    if labware is not None:
        wt.gripper.move(labware, to=home)
