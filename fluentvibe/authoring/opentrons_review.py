"""Strata (the local model) reviews an Opentrons -> fluentvibe conversion.

The conversion itself is deterministic (``opentrons_import``); this is the
second opinion: the model reads the Opentrons source and its description next
to what the conversion did (labware mapping, substitutions, steps kept as
operator prompts, the well-by-well volume comparison) and says whether the
converted protocol does the same biology, and what a person must check or fix.
It also flags problems in the Opentrons protocol itself (the fluentvibe
simulator tracks liquid, the Opentrons simulator does not).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

SYSTEM_PROMPT = (
    "You check the conversion of an Opentrons liquid-handling protocol into a Tecan Fluent protocol "
    "(fluentvibe). You get the Opentrons protocol source, its description, and the conversion report: "
    "how Opentrons labware was placed on the Fluent deck, which wells moved to other containers, which "
    "steps stayed operator prompts (modules, magnet, gripper), and a well-by-well comparison of the "
    "volume each well gained or lost in the Opentrons run versus the Fluent simulation.\n"
    "Judge whether the Fluent protocol does the same biology: same reagents into the same samples, same "
    "volumes, same order, incubations and separations still happening (automated or as an operator "
    "step). A Fluent simulation error can also reveal a bug in the Opentrons protocol (its simulator "
    "does not track liquid): say so when that is the case. Do not invent chemistry; ground every "
    "statement in the material given.\n"
    "Answer with ONE JSON object and nothing else:\n"
    '{"verdict": "faithful" | "partly faithful" | "not faithful", '
    '"summary": "<two sentences in plain lab language>", '
    '"must_fix": ["<what a person must change or check before running>", ...], '
    '"source_problems": ["<problems in the Opentrons protocol itself>", ...]}'
)

_SOURCE_CHARS = 12000
_LIST_ITEMS = 30


def _readme(protocol: Path) -> str:
    folder = protocol if protocol.is_dir() else protocol.parent
    readme = folder / "README.md"
    try:
        return readme.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return ""


def _protocol_source(protocol: Path) -> str:
    path = protocol
    if protocol.is_dir():
        path = next(iter(sorted(protocol.glob("*.py"))), protocol)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > _SOURCE_CHARS:
        text = text[:_SOURCE_CHARS] + f"\n# ... ({len(text) - _SOURCE_CHARS} more characters not shown)"
    return text


def review_material(protocol: Path, summary: dict[str, Any]) -> str:
    """The user message: Opentrons source, description, conversion report."""
    report = {
        "conversion mode": summary.get("mode"),
        "stage reached": summary.get("stage"),
        "error": summary.get("error"),
        "aspirate/dispense events in the Opentrons run": summary.get("liquid_events"),
        "compiled and simulated on the Fluent deck": summary.get("gate"),
        "well-by-well comparison": summary.get("fidelity"),
        "Fluent containers": summary.get("containers"),
        "Opentrons wells placed in other containers": (summary.get("substitutions") or [])[:_LIST_ITEMS],
        "Opentrons pauses and delays that ARE in the Fluent protocol (operator prompts / waits)":
            (summary.get("kept_pauses_and_waits") or [])[:_LIST_ITEMS],
        "not converted (module programs, magnet, gripper, parked tips: operator prompts or comments)":
            (summary.get("unconverted") or [])[:_LIST_ITEMS],
        "FCA tips used": summary.get("fca_tips_used"),
        "FluentControl InfoPad": summary.get("fc_findings") if summary.get("fc_ok") is not None else None,
    }
    report = {k: v for k, v in report.items() if v not in (None, [], {})}
    return (
        f"Opentrons protocol description (README):\n{_readme(protocol) or '(none)'}\n\n"
        f"Opentrons protocol source:\n```python\n{_protocol_source(protocol)}\n```\n\n"
        f"Conversion report:\n```json\n{json.dumps(report, indent=1, ensure_ascii=False)}\n```"
    )


def _parse(content: str) -> Optional[dict[str, Any]]:
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or "verdict" not in data:
        return None
    data["must_fix"] = [str(x) for x in data.get("must_fix") or []]
    data["source_problems"] = [str(x) for x in data.get("source_problems") or []]
    return data


def review_conversion(protocol: Path, summary: dict[str, Any], *, client: Any = None) -> dict[str, Any]:
    """The model's verdict on the conversion: ``verdict``, ``summary``,
    ``must_fix``, ``source_problems``; or ``error`` when the model could not
    be asked or answered unreadably (the conversion itself stands either way)."""
    if client is None:
        from .lm_client import LMStudioChatClient

        client = LMStudioChatClient(request_timeout_s=1800)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": review_material(Path(protocol), summary)}]
    try:
        message = client.complete(messages=messages, tools=[])
    except Exception as exc:  # noqa: BLE001  (no model server: the conversion stands without a review)
        return {"error": f"Strata could not review the conversion: {exc}"[:400]}
    content = message.get("content") or ""
    parsed = _parse(content)
    if parsed is None:
        return {"error": "Strata's review was not readable JSON", "raw": content[:2000]}
    return parsed
