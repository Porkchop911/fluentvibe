"""LLM-backed 'explain this error' for analyzer diagnostics.

Turns a structured diagnostic (the deterministic analyzer already did the hard
work — category, message, repair hint, exact line) into a plain-language
explanation via an OpenAI-compatible chat model. The model is grounded by the
structured failure, so this stays cheap and on-topic.

The chat client is injectable so this is unit-testable offline; the default uses
the env-configured endpoint (FLUENTVIBE_LM_ENDPOINT). See docs/copilot-design.md.
"""

from __future__ import annotations

from typing import Any, Optional

_SYSTEM_PROMPT = (
    "You are a Tecan FluentControl lab-automation assistant helping a scientist "
    "author a liquid-handling protocol in the fluentvibe Python API. You are given "
    "a diagnostic from the protocol analyzer (a real build error or a physical "
    "simulator failure) and the code around it. Explain, in 2-4 plain sentences, "
    "what went wrong and how to fix it. Be concrete and reference the actual "
    "labware/variables in the snippet. Do not invent API that isn't shown. Do not "
    "repeat the raw error verbatim."
)

# Lines of context to show on each side of the failing line.
_CONTEXT = 4


def _snippet(source: str, line: int) -> str:
    lines = source.splitlines()
    if not (1 <= line <= len(lines)):
        return source.strip()
    start = max(line - _CONTEXT, 1)
    end = min(line + _CONTEXT, len(lines))
    out = []
    for i in range(start, end + 1):
        marker = ">>" if i == line else "  "
        out.append(f"{marker} {i:4d} | {lines[i - 1]}")
    return "\n".join(out)


def build_messages(diagnostic: dict[str, Any], source: str) -> list[dict[str, str]]:
    """Build the chat messages for explaining ``diagnostic`` in ``source``."""
    parts = [
        f"Diagnostic category: {diagnostic.get('code')}",
        f"Severity: {diagnostic.get('severity')}",
        f"Message: {diagnostic.get('message')}",
    ]
    hint = diagnostic.get("hint")
    if hint:
        parts.append(f"Analyzer repair hint: {hint}")
    line = int(diagnostic.get("line") or 1)
    parts.append(f"\nCode around line {line} (>> marks the failing line):\n")
    parts.append(_snippet(source, line))
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(parts)},
    ]


def explain_diagnostic(
    diagnostic: dict[str, Any],
    source: str,
    *,
    client: Optional[Any] = None,
) -> str:
    """Return a plain-language explanation of ``diagnostic``.

    ``client`` is anything with ``complete(*, messages, tools) -> {"content": str}``
    (the authoring ``LMStudioChatClient`` contract). Defaults to a client on the
    env-configured endpoint.
    """
    if client is None:
        client = explain_client()
    message = client.complete(messages=build_messages(diagnostic, source), tools=[])
    return (message.get("content") or "").strip()


EXPLAIN_EFFORT_ENV = "FLUENTVIBE_EXPLAIN_EFFORT"


def explain_client() -> Any:
    """The client for editor explanations: low reasoning effort by default (the
    user's choice for explain; authoring keeps its own setting)."""
    import os

    from ..authoring.lm_client import LMStudioChatClient

    return LMStudioChatClient(reasoning_effort=os.environ.get(EXPLAIN_EFFORT_ENV, "").strip() or "low")


_REGION_PROMPT = (
    "You explain a part of a Tecan FluentControl liquid-handling protocol written in the fluentvibe Python "
    "API to the scientist who is writing it. Say what the selected lines do on the robot, step by step, in "
    "plain lab language: which head (FCA = 8-channel arm, MCA = 96-channel head, RGA = plate gripper) moves "
    "what, from where to where, how much, with which tips, and what is in the wells afterwards. Use the "
    "simulated deck state you are given for the numbers; do not invent any. Tips: MCA return_tips puts the "
    "tips back into their box, and the same box serves the same wells again (the blocks keep one box per "
    "plate, channel i only ever touches well i): say 'the same tips', not 'fresh tips', unless tips come from "
    "a different box; FCA drop_tips discards FCA tips. The MCA may keep its adapter and tips on between "
    "steps. If the lines have a problem "
    "(listed), say what it is. At most 8 short sentences or bullets. No code unless a one-line fix is needed."
)


def deck_state_after(source: str, path: str, line: int) -> str:
    """A compact, simulated deck state after the last step written at or before ``line``."""
    try:
        from ..authoring.eval_rubric import build_worktable_from_source

        wt = build_worktable_from_source(source, str(path))
        try:
            wt.simulate(strict=False)
        except Exception:  # noqa: BLE001 - the snapshots up to the failure still help
            pass
    except Exception as exc:  # noqa: BLE001
        return f"(the protocol does not build: {type(exc).__name__}: {exc})"
    snaps = [s for s in getattr(wt, "snapshots", []) or []
             if (getattr(s.step, "source_pos", None) and getattr(s.step.source_pos, "line", 10**9) <= line)]
    if not snaps:
        return "(no steps simulated up to these lines)"
    snap = snaps[-1]
    out = []
    for (site, idx), stack in sorted(snap.slot_map.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        for lw in stack:
            wells = [w for w in getattr(lw, "wells", {}).values() if w.volume_ul > 0.01]
            if not wells:
                continue
            vols = [w.volume_ul for w in wells]
            reagents: dict[str, float] = {}
            for w in wells:
                for layer in w.layers:
                    reagents[layer.reagent.name] = reagents.get(layer.reagent.name, 0) + layer.volume_ul
            mix = ", ".join(f"{n} {v:.0f} µl" for n, v in sorted(reagents.items(), key=lambda kv: -kv[1])[:4])
            vol = f"{min(vols):.1f}" if max(vols) - min(vols) < 0.05 else f"{min(vols):.1f}–{max(vols):.1f}"
            beads = sum(1 for w in wells if getattr(getattr(w, "bead_phase", None), "present", False))
            out.append(f"- {lw.label} at {site} {idx}: {len(wells)} well(s) with liquid, {vol} µl each"
                       + (f", beads in {beads}" if beads else "") + f" (total: {mix})")
    tips = []
    if snap.mca_adapter_label:
        tips.append(f"MCA adapter mounted{', tips from ' + snap.mca_tip_box_label if snap.mca_tips else ''}")
    if any(snap.liha_tips or []):
        tips.append(f"FCA holds {sum(1 for t in snap.liha_tips if t)} tip(s)")
    return "\n".join(out + tips) or "(no liquid on the deck yet)"


def explain_region(source: str, start_line: int, end_line: int, *, path: str = "<protocol>",
                   diagnostics: Optional[list[dict[str, Any]]] = None, client: Optional[Any] = None) -> str:
    """Explain lines ``start_line``..``end_line`` (1-based) of ``source`` with the simulated state."""
    lines = source.splitlines()
    start_line = max(1, start_line)
    end_line = min(len(lines), max(start_line, end_line))
    selection = "\n".join(f"{i:4d} | {lines[i - 1]}" for i in range(start_line, end_line + 1))
    problems = [d for d in diagnostics or [] if start_line <= int(d.get("line") or 0) <= end_line]
    user = (f"Selected lines:\n{selection}\n\n"
            f"Simulated deck state after these lines:\n{deck_state_after(source, path, end_line)}\n\n"
            + ("Problems on these lines:\n" + "\n".join(f"- line {d.get('line')}: {d.get('message')}" for d in problems)
               if problems else "No problems reported on these lines.")
            + f"\n\nWhole file for context:\n```python\n{source}\n```")
    message = (client or explain_client()).complete(
        messages=[{"role": "system", "content": _REGION_PROMPT}, {"role": "user", "content": user}], tools=[])
    return (message.get("content") or "").strip()
