"""Editor-facing Python source positions for IR steps.

The IR's ``line_number`` is the *FluentControl* tree line, assigned at render
time — it has nothing to do with the ``.py`` a user is editing. ``SourcePos``
records where in the *author's Python source* a step was emitted, so simulator
failures (which know the failing step) can be surfaced as editor diagnostics on
the correct line.

This is purely an in-memory authoring aid. ``SourcePos`` is excluded from IR
serialization and never affects rendering.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

# The fluentvibe package root: fluentvibe/ir/source_pos.py -> parents[1] == fluentvibe/.
# Frames whose file lives under this directory are framework-internal and skipped
# when attributing a step to the author's source.
_PKG_ROOT = Path(__file__).resolve().parents[1]


class SourcePos(BaseModel):
    """A position in the author's Python source that emitted an IR step."""

    file: str
    line: int
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }


def _is_internal(filename: str) -> bool:
    """True for frames we never want to attribute a step to.

    Skips fluentvibe's own modules, the stdlib ``contextlib`` (so steps emitted
    when a ``with wt.group()/loop()`` block closes attribute to the user's
    ``with`` line, not contextlib internals), and synthetic ``<...>`` frames.
    """
    if not filename or filename.startswith("<"):
        return True
    try:
        resolved = Path(filename).resolve()
    except (OSError, ValueError):
        return True
    if resolved == _PKG_ROOT or _PKG_ROOT in resolved.parents:
        return True
    return resolved.name == "contextlib.py"


def capture_source_pos() -> Optional[SourcePos]:
    """Return the first non-internal caller frame as a ``SourcePos``, or None.

    Walks outward from the caller, skipping framework and stdlib frames, and
    returns the innermost frame that lives in the author's own source file.
    """
    frame = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if not _is_internal(filename):
            return SourcePos(file=filename, line=frame.f_lineno)
        frame = frame.f_back
    return None
