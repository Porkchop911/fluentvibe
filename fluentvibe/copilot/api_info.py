"""Deterministic signature help and hover for the fluentvibe API.

Pure introspection of the real classes (no code execution, no LLM): given the
cursor inside ``head.aspirate(`` it returns the parameter list; hovering a method
returns its signature + docstring. Instant and safe, so it can run on every
keystroke. The receiver→class mapping is shared with completion
(``complete._class_for_receiver``). See ``docs/copilot-design.md``.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Optional

from .complete import _class_for_receiver

# An open call at the cursor: `<receiver>.<method>(<args-so-far>` with no close.
_OPEN_CALL_RE = re.compile(r"([A-Za-z_][\w.]*)\.([A-Za-z_]\w*)\s*\(([^()]*)$")
# A `<receiver>.<method>` reference anywhere on a line (for hover).
_MEMBER_RE = re.compile(r"([A-Za-z_][\w.]*)\.([A-Za-z_]\w*)")


@dataclass
class ApiSignature:
    name: str
    label: str  # "aspirate(target, volume_ul, *, liquid_class, columns=None)"
    doc: str
    params: list[str]
    active_param: int = 0
    owner: str = ""  # class name the member belongs to

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "doc": self.doc,
            "params": list(self.params),
            "active_param": self.active_param,
            "owner": self.owner,
        }


def _signature_for(cls: type, method: str) -> Optional[ApiSignature]:
    member = getattr(cls, method, None)
    if member is None or not callable(member):
        return None
    try:
        sig = inspect.signature(member)
    except (TypeError, ValueError):
        return None
    params = [p for name, p in sig.parameters.items() if name != "self"]
    label = f"{method}({', '.join(str(p) for p in params)})"
    return ApiSignature(
        name=method,
        label=label,
        doc=inspect.getdoc(member) or "",
        params=[p.name for p in params if p.name not in {"args", "kwargs"}],
        owner=cls.__name__,
    )


def signature_at(source: str, line: int, character: int) -> Optional[ApiSignature]:
    """Signature help for an open call at the 0-based cursor, or None."""
    lines = source.splitlines()
    if not (0 <= line < len(lines)):
        return None
    prefix = lines[line][:character]
    m = _OPEN_CALL_RE.search(prefix)
    if m is None:
        return None
    cls = _class_for_receiver(m.group(1))
    if cls is None:
        return None
    sig = _signature_for(cls, m.group(2))
    if sig is None:
        return None
    # Active parameter = number of top-level commas typed since the '('.
    sig.active_param = m.group(3).count(",")
    return sig


def hover_at(source: str, line: int, character: int) -> Optional[ApiSignature]:
    """Signature + docstring for the API member under the 0-based cursor, or None."""
    lines = source.splitlines()
    if not (0 <= line < len(lines)):
        return None
    line_text = lines[line]
    for m in _MEMBER_RE.finditer(line_text):
        if m.start(2) <= character <= m.end(2):
            cls = _class_for_receiver(m.group(1))
            if cls is not None:
                return _signature_for(cls, m.group(2))
    return None
