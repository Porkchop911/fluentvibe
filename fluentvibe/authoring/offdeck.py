"""Off-deck steps must pause the run, not be modelled as waits.

A protocol often needs the operator mid-run: a thermal-cycler incubation, a
centrifuge spin, a Qubit reading. Models tend to write these as a comment plus
``wt.wait(...)``, which leaves the plate on the deck while the run carries on.
This check finds functional groups whose comments (or group name) describe an
off-deck action and that neither pause for the operator (``wt.user_prompt``)
nor drive an on-deck device that does the job (``wt.odtc_*`` for the Inheco
thermal cycler, ``wt.inheco_*``), as long as the
protocol handles liquid both before and after it. Preparation before the first
liquid handling and manual steps after the last one (e.g. loading a flow cell
once the run is over) are not flagged.

It is a source-level heuristic until the Bench Spec gives every step an
explicit ``location`` (see docs/authoring-strategy.md, W2).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

OFF_DECK_PATTERN = re.compile(
    r"thermal[\s-]?cycler|thermocycler|pcr machine|centrifug|spin[\s-]?down"
    r"|\bon ice\b|fridge|freezer|qubit|nanodrop|vortex|off[\s-]?deck",
    re.IGNORECASE,
)

_LIQUID_METHODS = frozenset({
    "aspirate", "dispense", "mix", "pick_up", "get_tips", "empty_tips",
    # fluentvibe.blocks stages that handle liquid
    "spri_cleanup", "stamp", "add_reagent", "pool_columns",
})
# Calls that pause for the operator or hand the step to an on-deck device.
_HANDLED_CALLS = frozenset({"user_prompt", "offdeck_step", "thermal_step"})


@dataclass(frozen=True)
class OffDeckFinding:
    group: str
    line: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return {"group": self.group, "line": self.line, "text": self.text}


def _string_arg(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    for kw in call.keywords:
        if kw.arg in {"text", "comment", "name", "prompt"} and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            if isinstance(value, str):
                return value
    return None


def offdeck_findings(source: str) -> list[OffDeckFinding]:
    """Groups that describe an off-deck action mid-protocol without a user prompt."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    calls = sorted(
        (
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name))
        ),
        key=lambda n: (n.lineno, n.col_offset),
    )
    group = ""
    # group name → (first mention line, text); groups with a prompt
    mentions: dict[str, tuple[int, str]] = {}
    prompted: set[str] = set()
    first_liquid_line = 0
    last_liquid_line = 0
    for call in calls:
        method = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
        if method == "group":
            group = _string_arg(call) or ""
            if OFF_DECK_PATTERN.search(group) and group not in mentions:
                mentions[group] = (call.lineno, group)
        elif method in {"add_comment", "comment"}:
            text = _string_arg(call) or ""
            if OFF_DECK_PATTERN.search(text) and group not in mentions:
                mentions[group] = (call.lineno, text)
        elif method in _HANDLED_CALLS or method.startswith(("odtc_", "inheco_")):
            # Paused for the operator, or handled by an integrated device.
            prompted.add(group)
        elif method in _LIQUID_METHODS:
            first_liquid_line = first_liquid_line or call.lineno
            last_liquid_line = max(last_liquid_line, call.lineno)
    return [
        OffDeckFinding(group=name, line=line, text=" ".join(text.split())[:160])
        for name, (line, text) in mentions.items()
        if name not in prompted and first_liquid_line < line < last_liquid_line
    ]
