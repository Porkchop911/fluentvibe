"""Apply a code block from the chat to the open protocol.

The model answers with one python block. It may change several places at once
("that replaces line 16 and line 60"), so the block is placed like this:

1. Line markers the chat asks for: ``# lines 16-16`` (or ``# line 60``) before
   each changed part; the part replaces exactly those lines.
2. Without markers, the block is split at ``...`` lines and each part is placed
   by its first statement: the file statement with the same assignment target
   (``BEADS_UL =``) or the same call (``beads.fill_all(``) is replaced. A part
   that matches no statement, or more than one, is not placed (reported).
3. A single part with neither, while lines are selected, replaces the selection.

Nothing is written here: the result is the whole proposed file, checked like
Ctrl+I (build + simulate), for the editor's diff preview.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .analyzer import analyze_source
from .edit import _import_insert_line, _import_text, _missing_block_imports

_MARKER = re.compile(r"^\s*#\s*lines?\s+(\d+)\s*(?:[-–—]\s*(\d+))?\s*:?\s*$", re.I)
_ELLIPSIS = re.compile(r"^\s*(?:#\s*)?(?:\.\.\.|…)\s*$")
_ASSIGN = re.compile(r"^\s*([A-Za-z_][\w.\[\]\"']*)\s*(?::[^=]+)?=(?!=)")
_CALL = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*\(")


@dataclass
class Patch:
    proposed_source: str = ""
    placed: list[dict[str, Any]] = field(default_factory=list)     # {"start", "end", "how"}
    unplaced: list[str] = field(default_factory=list)              # first lines of parts not placed
    imports: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposed_source": self.proposed_source,
            "placed": self.placed,
            "unplaced": self.unplaced,
            "imports": self.imports,
            "diagnostics": self.diagnostics,
            "introduces_errors": any(d.get("severity") == "error" for d in self.diagnostics),
        }


def _statement_end(lines: list[str], start: int) -> int:
    """0-based index of the last line of the statement starting at ``start``."""
    depth, quote = 0, None
    for i in range(start, len(lines)):
        for ch in lines[i]:
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif ch == "#":
                break
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        quote = None
        if depth <= 0:
            return i
    return len(lines) - 1


def _key(line: str) -> tuple[str, str] | None:
    m = _ASSIGN.match(line)
    if m:
        return ("assign", m.group(1))
    m = _CALL.match(line)
    if m:
        # Calls are told apart by their first argument as well (several fill_all(...) lines).
        rest = line[m.end():].strip()
        first = re.split(r"[,)]", rest, maxsplit=1)[0].strip()
        return ("call", f"{m.group(1)}({first}")
    return None


def _parts(code: str) -> list[tuple[tuple[int, int] | None, list[str]]]:
    """[(marked line range or None, lines)] in block order."""
    parts: list[tuple[tuple[int, int] | None, list[str]]] = []
    current: list[str] = []
    marked: tuple[int, int] | None = None
    for line in code.splitlines():
        m = _MARKER.match(line)
        if m or _ELLIPSIS.match(line):
            if current and any(ln.strip() for ln in current):
                parts.append((marked, current))
            current, marked = [], None
            if m:
                a = int(m.group(1))
                marked = (a, int(m.group(2) or a))
            continue
        current.append(line)
    if current and any(ln.strip() for ln in current):
        parts.append((marked, current))
    return parts


def _reindent(part: list[str], indent: str) -> list[str]:
    """The part with its own common indentation replaced by ``indent``."""
    body = [ln for ln in part if ln.strip()]
    common = min((len(ln) - len(ln.lstrip()) for ln in body), default=0)
    return [indent + ln[common:] if ln.strip() else "" for ln in part]


def apply_chat_code(source: str, code: str, *, path: str = "<protocol>", start_line: int = 0,
                    end_line: int = 0, has_selection: bool = False, revalidate: bool = True) -> Patch:
    lines = source.splitlines()
    patch = Patch()
    parts = _parts(code)
    replacements: list[tuple[int, int, list[str], str]] = []   # 0-based start, end (inclusive)
    single_unmarked = len(parts) == 1 and parts[0][0] is None
    for marked, part in parts:
        if marked is not None:
            a, b = marked
            if not (1 <= a <= b <= len(lines)):
                patch.unplaced.append(part[0].strip())
                continue
            replacements.append((a - 1, b - 1, part, f"lines {a}-{b} (marked)"))
            continue
        if single_unmarked and has_selection and start_line >= 1:
            replacements.append((start_line - 1, min(end_line, len(lines)) - 1, part, "the selection"))
            continue
        first = next(ln for ln in part if ln.strip())
        key = _key(first)
        hits = [i for i, ln in enumerate(lines) if key is not None and _key(ln) == key] if key else []
        if len(hits) != 1:
            patch.unplaced.append(first.strip())
            continue
        end = _statement_end(lines, hits[0])
        replacements.append((hits[0], end, part, f"matched '{key[1]}'"))
    # Overlapping replacements would corrupt the file: refuse the later ones.
    replacements.sort(key=lambda r: r[0])
    kept: list[tuple[int, int, list[str], str]] = []
    for r in replacements:
        if kept and r[0] <= kept[-1][1]:
            patch.unplaced.append(next(ln for ln in r[2] if ln.strip()).strip())
            continue
        kept.append(r)
    new_lines = list(lines)
    for a, b, part, how in reversed(kept):
        indent = re.match(r"\s*", lines[a]).group(0)
        new_lines[a:b + 1] = _reindent(part, indent)
        patch.placed.insert(0, {"start": a + 1, "end": b + 1, "how": how})
    if not kept:
        return patch
    new_code = "\n".join("\n".join(r[2]) for r in kept)
    patch.imports = _missing_block_imports(source, new_code)
    if patch.imports:
        new_lines.insert(_import_insert_line(new_lines) - 1, _import_text(patch.imports))
    patch.proposed_source = "\n".join(new_lines) + ("\n" if source.endswith("\n") else "")
    if revalidate:
        patch.diagnostics = [d.to_dict() for d in analyze_source(patch.proposed_source, path)]
    return patch
