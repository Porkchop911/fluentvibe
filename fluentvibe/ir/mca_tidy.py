"""Keep the MCA96 adapter and tips on the head between consecutive MCA stages.

Every MCA block (stamp, add_reagent, remove_liquid, mix_wells ...) is a complete,
safe stage on its own: mount the adapter, pick up tips, work, return the tips,
drop the adapter. Chained, that is an adapter drop and remount (and a tip return
and re-pickup) between every two stages, three or four times per pass of a wash
loop. This pass removes the round trips that change nothing:

* ``return tips X`` ... ``pick up tips X`` (same box, same columns): the tips stay on;
* ``drop adapter`` ... ``mount adapter`` (same adapter): the adapter stays on;
* a loop body that mounts the adapter first and drops it last (and, inside that,
  picks up and returns one tip set first and last) does so once around the loop.

Only steps that do not use the MCA may lie in between (gripper moves, waits, FCA
work, comments, variables). An operator prompt, a device macro, a worklist or a
subroutine, or anything that moves or removes the tip box, keeps the round trip:
the head is left clean for them. It runs on a copy in ``Worktable.to_protocol``,
so the simulator and the compiled script see the same steps.
"""

from __future__ import annotations

from typing import Any

from .schema import (
    AddLabwareStep,
    AspirateStep,
    ConditionalStep,
    DispenseStep,
    DropHeadAdapterStep,
    ExecuteApplicationStep,
    ExecuteWorklistStep,
    GetHeadAdapterStep,
    Group,
    LegacyDriverMacroStep,
    LoadWorklistStep,
    LoopStep,
    Mca384DropTipsStep,
    Mca384EmptyTipsStep,
    Mca384GetTipsStep,
    Mca384MixStep,
    Mca384MoveArmStep,
    PickUpTipsStep,
    RemoveLabwareStep,
    RgaTransferLabwareStep,
    ScriptGroupStep,
    SetTipsBackStep,
    SubRoutineStep,
    UserPromptStep,
    WorklistImportStep,
)

_MCA = (GetHeadAdapterStep, DropHeadAdapterStep, PickUpTipsStep, SetTipsBackStep, AspirateStep, DispenseStep,
        Mca384MixStep, Mca384EmptyTipsStep, Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep)
_BARRIER = (UserPromptStep, LegacyDriverMacroStep, SubRoutineStep, ExecuteApplicationStep, WorklistImportStep,
            LoadWorklistStep, ExecuteWorklistStep, AddLabwareStep, RemoveLabwareStep)


def _children(step) -> list[list]:
    if isinstance(step, (LoopStep, ScriptGroupStep)):
        return [step.steps]
    if isinstance(step, ConditionalStep):
        return [step.then_steps, step.else_steps]
    return []


def _leaves(seq: list) -> list[tuple[list, Any]]:
    """The steps of ``seq`` in run order as (containing list, step), looking
    through nested script groups (a block's ``wt.group`` inside a loop) but not
    into loops or conditionals, which run a different number of times."""
    out: list[tuple[list, Any]] = []
    for step in seq:
        if isinstance(step, ScriptGroupStep):
            out += _leaves(step.steps)
        else:
            out.append((seq, step))
    return out


def _remove(pairs: list[tuple[list, Any]]) -> None:
    for container, step in pairs:
        for i, s in enumerate(container):
            if s is step:
                del container[i]
                break


def _touches_head(step) -> bool:
    """Uses the MCA, or is something the head should be clean for."""
    if isinstance(step, _MCA + _BARRIER):
        return True
    if isinstance(step, RgaTransferLabwareStep) and "tip" in (step.labware_name or "").lower():
        return True
    if type(step).__name__ == "GenericStep":
        return True
    return any(_touches_head(s) for body in _children(step) for s in body)


def _cols(step) -> tuple:
    return (tuple(step.columns or ()), step.partial_columns, step.partial_rows)


def _cancel(seq: list) -> None:
    """Remove the return/pickup and drop/mount pairs that change nothing."""
    leaves = _leaves(seq)
    removed: list[tuple[list, Any]] = []
    kept: list[tuple[list, Any]] = []   # head-touching steps still in effect, in order
    held: list[str] = []                # the box whose tips are on the head
    for leaf in leaves:
        step = leaf[1]
        if not _touches_head(step):
            continue
        prev = kept[-1][1] if kept else None
        if isinstance(step, GetHeadAdapterStep) and isinstance(prev, DropHeadAdapterStep)                 and prev.labware_name in (None, step.labware_name) and prev.device_alias == step.device_alias:
            removed += [kept.pop(), leaf]
            continue
        if isinstance(step, PickUpTipsStep) and isinstance(prev, SetTipsBackStep)                 and prev.back_position == "BackToPosition" and prev.device_alias == step.device_alias                 and (prev.labware_name or (held[-1] if held else None)) == step.labware_name                 and _cols(prev) == _cols(step):
            removed += [kept.pop(), leaf]
            held[:] = [step.labware_name]
            continue
        if isinstance(step, PickUpTipsStep):
            held[:] = [step.labware_name]
        kept.append(leaf)
    _remove(removed)


def _first_last(body: list):
    idx = [leaf for leaf in _leaves(body) if _touches_head(leaf[1])]
    return (idx[0], idx[-1]) if len(idx) > 1 else (None, None)


def _count(body: list, kinds) -> int:
    return sum(isinstance(s, kinds) + _count([c for ch in _children(s) for c in ch], kinds) for s in body)


def _hoist(loop: LoopStep) -> tuple[list, list]:
    """Move a mount-first/drop-last adapter (and a pickup-first/return-last tip
    set inside it) out of ``loop``; returns the steps to put before and after."""
    before: list = []
    after: list = []
    body = loop.steps
    first, last = _first_last(body)
    if first is None:
        return before, after
    head, tail = first[1], last[1]
    if not (isinstance(head, GetHeadAdapterStep) and isinstance(tail, DropHeadAdapterStep)
            and tail.labware_name in (None, head.labware_name)
            and _count(body, (GetHeadAdapterStep, DropHeadAdapterStep)) == 2):
        return before, after
    _remove([first, last])
    before.append(head)
    after.append(tail)
    first, last = _first_last(body)
    if first is not None:
        head, tail = first[1], last[1]
        if isinstance(head, PickUpTipsStep) and isinstance(tail, SetTipsBackStep)                 and tail.back_position == "BackToPosition"                 and tail.labware_name in (None, head.labware_name) and _cols(tail) == _cols(head)                 and _count(body, (PickUpTipsStep, SetTipsBackStep)) == 2:
            _remove([first, last])
            before.append(head)
            after.insert(0, tail)
    return before, after


def _tidy_bodies(step) -> None:
    """Tidy the loop and conditional bodies nested anywhere under ``step``."""
    if isinstance(step, ScriptGroupStep):
        for s in step.steps:
            _tidy_bodies(s)
    elif isinstance(step, (LoopStep, ConditionalStep)):
        for body in _children(step):
            for s in body:
                _tidy_bodies(s)
            _hoist_all(body)
            _cancel(body)


def _hoist_all(seq: list) -> None:
    """Hoist out of every loop in ``seq`` (through script groups), in place."""
    for container, step in _leaves(seq):
        if isinstance(step, LoopStep):
            before, after = _hoist(step)
            if before or after:
                i = next(k for k, s in enumerate(container) if s is step)
                container[i:i + 1] = before + [step] + after


def tidy_mca_groups(groups: list[Group]) -> list[Group]:
    """The same groups with needless MCA adapter/tip round trips removed.

    The top-level groups are one run of steps (a round trip often spans two
    groups: one block ends a group, the next block starts one)."""
    copies = [g.model_copy(deep=True) for g in groups]
    run = [ScriptGroupStep(name=g.name, steps=g.steps) for g in copies]
    for s in run:
        _tidy_bodies(s)
    _hoist_all(run)
    _cancel(run)
    for g, sg in zip(copies, run):
        g.steps = sg.steps
    return copies
