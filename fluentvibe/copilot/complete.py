"""Deterministic completions for protocol authoring.

Two high-signal contexts, no LLM:
- inside ``catalog="..."`` → real FluentControl catalog names from the index
  (the obscure strings nobody remembers), filtered by what's typed;
- after a known receiver dot (``head.``, ``wt.``, ``wt.mca96.``, ``gripper.``,
  ``<liha>.``) → that class's public API, by introspection.

Pure and editor-agnostic: positions are 0-based (LSP-native); the LSP layer maps
``Completion`` to ``CompletionItem`` and the CLI prints it. See
``docs/copilot-design.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

# An open catalog string at the cursor: `catalog="<partial>` with no closing quote.
_CATALOG_OPEN_RE = re.compile(r"""catalog\s*=\s*["']([^"']*)$""")
# A receiver chain followed by a dot and a partial member name at the cursor.
_DOT_RE = re.compile(r"([A-Za-z_][\w.]*)\.(\w*)$")

_MAX_CATALOG = 50


@dataclass
class Completion:
    label: str
    kind: str  # "catalog" | "method"
    insert_text: str
    replace_start: int  # 0-based column where the replacement begins
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "insert_text": self.insert_text,
            "replace_start": self.replace_start,
            "detail": self.detail,
        }


def complete_at(source: str, line: int, character: int) -> list[Completion]:
    """Return completions for the cursor at 0-based ``line``/``character``."""
    lines = source.splitlines()
    if not (0 <= line < len(lines)):
        return []
    prefix = lines[line][:character]

    m = _CATALOG_OPEN_RE.search(prefix)
    if m is not None:
        return _catalog_completions(m.group(1), m.start(1))

    m = _DOT_RE.search(prefix)
    if m is not None:
        return _method_completions(m.group(1), m.group(2), m.start(2))

    return []


def _catalog_completions(partial: str, replace_start: int) -> list[Completion]:
    try:
        from ..catalog.catalog import find_components, index_exists

        if not index_exists():
            return []
        entries = find_components(partial)
    except Exception:
        return []
    out: list[Completion] = []
    for entry in entries[:_MAX_CATALOG]:
        out.append(
            Completion(
                label=entry.name,
                kind="catalog",
                insert_text=entry.name,
                replace_start=replace_start,
                detail=entry.category or "",
            )
        )
    return out


def _class_for_receiver(receiver: str) -> Optional[type]:
    r = receiver.lower()
    last = r.rsplit(".", 1)[-1]
    if "gripper" in r:
        from ..gripper import Gripper

        return Gripper
    if "liha" in r:
        from ..heads.liha import LiHa

        return LiHa
    if "mca" in r or "head" in r:
        from ..heads.mca96 import MCA96Head

        return MCA96Head
    if last == "wt" or "worktable" in r:
        from ..worktable import Worktable

        return Worktable
    return None


def _method_completions(receiver: str, partial: str, replace_start: int) -> list[Completion]:
    cls = _class_for_receiver(receiver)
    if cls is None:
        return []
    out: list[Completion] = []
    for name in sorted({n for n in dir(cls) if not n.startswith("_")}):
        if partial and not name.startswith(partial):
            continue
        out.append(
            Completion(
                label=name,
                kind="method",
                insert_text=name,
                replace_start=replace_start,
                detail=cls.__name__,
            )
        )
    return out
