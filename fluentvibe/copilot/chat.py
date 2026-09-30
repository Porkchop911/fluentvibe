"""Context for the VS Code chat panel.

The panel streams the model's answer itself (words appear as they come); this
module gives it what the answer must be grounded in: the open protocol with
line numbers, the selection, the current problems, the simulated deck state at
the selection (or cursor), and a compact reference of the blocks. Nothing here
calls the model.
"""

from __future__ import annotations

import inspect
from typing import Any

from .analyzer import analyze_source
from .explain import deck_state_after

SYSTEM_PROMPT = (
    "You are the fluentvibe assistant in VS Code. The scientist is writing a Tecan FluentControl "
    "liquid-handling protocol by hand in the fluentvibe Python API and asks you questions about it.\n"
    "Answer briefly and concretely, in plain lab language. Ground every number in the file or in the "
    "simulated deck state you are given; if something is not there, say you do not know.\n"
    "Heads: FCA = wt.liha (8 channels, FCA tip boxes), MCA = wt.mca96 (96 channels, MCA tip boxes), RGA = "
    "wt.gripper (moves plates, e.g. onto and off the magnet). Prefer the blocks from fluentvibe.blocks "
    "(reference below) over hand-written head calls. Reagents and master mixes go by the FCA from slim "
    "troughs; the MCA only takes cheap bulk liquids from SBS reservoirs and never pipettes in a slim trough. "
    "Liquid classes: 'Water Free Single' for transfers, 'Water Mix' for mixing, 'Empty Tip' for emptying. "
    "MCA return_tips puts tips back into their box for the same wells; say 'the same tips', not 'fresh tips', "
    "unless they come from another box. A tip that has touched sample wells never goes back into a shared "
    "source (trough, reservoir): a block that draws from a reservoir (add_reagent, distribute_reagent) needs "
    "its own tips, not the plate's box. Reusing tips within a plate's own wells is fine.\n"
    "Only when asked to change or write code: give ONE ```python block that replaces the selected lines "
    "exactly (same indentation), say that it replaces the selection, and add no steps, reagents or "
    "chemistry the scientist did not ask for. Never use wt.add(...)."
)


def blocks_reference(source: str = "") -> str:
    """Signatures (and summaries) of the blocks ``source`` uses; the others by name.

    Every 1,000 tokens of context cost about 2 s before the first word on the
    local 27B (hybrid model, little prefix reuse), so the reference stays short."""
    try:
        from .. import blocks
    except Exception:  # noqa: BLE001
        return ""
    lines, others = [], []
    for name in sorted(getattr(blocks, "__all__", ())):
        fn = getattr(blocks, name, None)
        if not callable(fn):
            continue
        if source and name not in source:
            others.append(name)
            continue
        try:
            sig = str(inspect.signature(fn)).replace("wt, *, ", "wt, ")
        except (TypeError, ValueError):
            sig = "(...)"
        doc = (inspect.getdoc(fn) or "").strip().splitlines()
        lines.append(f"- {name}{sig}: {doc[0] if doc else ''}")
    if others:
        lines.append("Also available: " + ", ".join(others))
    return "\n".join(lines)


def chat_context(source: str, path: str, *, start_line: int, end_line: int, has_selection: bool) -> dict[str, Any]:
    """System prompt + a context block for the chat's next message.

    ``start_line``/``end_line`` are 1-based; without a selection they are the
    cursor line (the deck state is then taken there)."""
    lines = source.splitlines()
    numbered = "\n".join(f"{i:4d} | {text}" for i, text in enumerate(lines, 1))
    try:
        problems = [d.to_dict() for d in analyze_source(source, path)]
    except Exception as exc:  # noqa: BLE001
        problems = [{"line": 1, "message": f"(analysis failed: {type(exc).__name__}: {exc})"}]
    parts = [f"Open protocol: {path}\n```python\n{numbered}\n```"]
    if has_selection:
        sel = "\n".join(lines[start_line - 1:end_line])
        parts.append(f"Selected lines {start_line}-{end_line}:\n```python\n{sel}\n```")
    else:
        parts.append(f"Cursor on line {start_line}.")
    where = f"after line {end_line}"
    parts.append(f"Simulated deck state {where}:\n{deck_state_after(source, path, end_line)}")
    parts.append("Problems in the file:\n" + ("\n".join(f"- line {d.get('line')}: {d.get('message')}"
                                                       for d in problems) or "none"))
    return {
        "system": SYSTEM_PROMPT + "\n\nBlocks (fluentvibe.blocks):\n" + blocks_reference(source),
        "context": "\n\n".join(parts),
        "problems": len(problems),
    }
