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


def thermal_step(
    wt,
    plate,
    program: str,
    *,
    odtc_position: tuple[str, int] | None = None,
    method_file: str | None = None,
    method_name: str | None = None,
    handoff: tuple[str, int] | None = None,
    name: str | None = None,
) -> None:
    """Run a thermal program on ``plate``: on the deck's ODTC, or via the operator.

    With ``odtc_position`` (the deck position the Inheco ODTC occupies) and a
    ``method_name`` (a program authored in the Inheco Script Editor; optional
    ``method_file`` to load first), the block opens the door, moves the plate
    in with the gripper, closes the door, runs the method, and moves the plate
    back to its home slot. Without an ODTC it pauses the run so the operator
    runs ``program`` on an external thermal cycler (see :func:`offdeck_step`;
    pass ``handoff`` to move the plate to a reachable slot first).

    Never model a thermal program as ``wt.wait`` — the plate would stay on the
    deck at room temperature.
    """
    if not str(program).strip():
        raise BlockError("thermal_step: program must describe the temperatures and times.")
    if odtc_position is None:
        offdeck_step(
            wt,
            f"Run on the thermal cycler: {program}. Return the plate afterwards.",
            labware=plate if handoff is not None else None,
            handoff=handoff,
            name=name,
        )
        return
    if not method_name:
        raise BlockError(
            "thermal_step: with an ODTC, give method_name (the program stored on the ODTC)."
        )
    home = plate.slot
    if home is None:
        raise BlockError("thermal_step: place the plate with wt.place(...) first.")
    if name:
        wt.group(name)
    wt.odtc_open_door()
    wt.gripper.move(plate, to=tuple(odtc_position))
    wt.odtc_close_door()
    if method_file:
        wt.odtc_set_parameters(method_file)
    wt.odtc_execute_method(method_name)
    wt.odtc_open_door()
    wt.gripper.move(plate, to=home)
    wt.odtc_close_door()
