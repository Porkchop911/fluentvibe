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
# A constructor / factory call name, e.g. `Plate96` in `Plate96("S", catalog="...`.
_CTOR_RE = re.compile(r"([A-Za-z_]\w*)\s*\(")

_MAX_CATALOG = 50


@dataclass
class Completion:
    label: str
    kind: str  # "catalog" | "method"
    insert_text: str
    replace_start: int  # 0-based column where the replacement begins
    detail: str = ""
    documentation: str = ""
    insert_format: str = "plain"  # "plain" | "snippet"

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "insert_text": self.insert_text,
            "replace_start": self.replace_start,
            "detail": self.detail,
            "documentation": self.documentation,
            "insert_format": self.insert_format,
        }


def complete_at(source: str, line: int, character: int) -> list[Completion]:
    """Return completions for the cursor at 0-based ``line``/``character``."""
    lines = source.splitlines()
    if not (0 <= line < len(lines)):
        return []
    prefix = lines[line][:character]

    m = _CATALOG_OPEN_RE.search(prefix)
    if m is not None:
        return _catalog_completions(m.group(1), m.start(1), _expected_category(prefix))

    m = _DOT_RE.search(prefix)
    if m is not None:
        return _method_completions(m.group(1), m.group(2), m.start(2))

    return []


# Constructor-name token -> the catalog category its `catalog=` argument expects.
_CTOR_CATEGORY = (
    (("box", "diti", "tip"), "tip_box"),
    (("plate",), "plate"),
    (("trough", "reservoir"), "trough"),
    (("tube",), "tube_rack"),
)


def _expected_category(prefix: str) -> Optional[str]:
    """Infer the expected catalog category from the enclosing constructor call.

    e.g. inside ``MCA100Box(..., catalog="...`` we expect a ``tip_box`` name; inside
    ``Plate96(..., catalog="...`` a ``plate``. Returns None when nothing matches, so
    completion falls back to the unfiltered list. Best-effort ranking only.
    """
    ctors = list(_CTOR_RE.finditer(prefix))
    if not ctors:
        return None
    name = ctors[-1].group(1).lower()
    for tokens, category in _CTOR_CATEGORY:
        if any(tok in name for tok in tokens):
            return category
    return None


def _catalog_completions(
    partial: str, replace_start: int, expected: Optional[str] = None
) -> list[Completion]:
    try:
        from ..catalog.catalog import find_components, index_exists

        if not index_exists():
            return []
        entries = find_components(partial)
    except Exception:
        return []
    if expected:
        # Rank entries of the expected category first, then top up with the rest so
        # a wrong guess never hides a valid name.
        preferred = [e for e in entries if e.category == expected]
        rest = [e for e in entries if e.category != expected]
        entries = preferred + rest
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


def _first_doc_line(doc: str) -> str:
    """First non-empty line of a docstring (the one-line summary)."""
    for line in doc.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _method_completions(receiver: str, partial: str, replace_start: int) -> list[Completion]:
    cls = _class_for_receiver(receiver)
    if cls is None:
        return []
    # Lazy import to avoid a cycle (api_info imports _class_for_receiver from here).
    from .api_info import _signature_for

    out: list[Completion] = []
    for name in sorted({n for n in dir(cls) if not n.startswith("_")}):
        if partial and not name.startswith(partial):
            continue
        member = getattr(cls, name, None)
        if not callable(member):
            # A data/property attribute, not a method — don't offer it as a call.
            continue
        sig = _signature_for(cls, name)
        if sig is None:
            # Callable but un-introspectable (C-level): offer the bare name.
            out.append(
                Completion(
                    label=name,
                    kind="method",
                    insert_text=name,
                    replace_start=replace_start,
                    detail=cls.__name__,
                )
            )
            continue
        out.append(
            Completion(
                label=name,
                kind="method",
                insert_text=f"{name}($0)",
                replace_start=replace_start,
                detail=sig.label,
                documentation=_first_doc_line(sig.doc),
                insert_format="snippet",
            )
        )
    return out
