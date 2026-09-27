"""Shorten a long protocol document to what the bench procedure needs.

Vendor PDFs carry chapters no liquid handling uses: flow cell loading, data
acquisition and analysis, troubleshooting, ordering, revision history. The
model reasons over every character it is given, and on long documents it has
thought for minutes without producing a spec. Chapters are found from the
document's numbered table of contents ("4. Priming and loading the ...");
those that are clearly not bench procedure are left out, the rest (overview,
equipment and kit contents, the procedure) stays as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CHAPTER = re.compile(r"^\s*(\d{1,2})\.\s+(\S.{2,80}?)\s*$")
_NOT_PROCEDURE = re.compile(
    r"(?i)priming and loading|loading the .*flow cell|flow cell (reuse|priming|check)|data acquisition|"
    r"basecalling|downstream analysis|data analysis|reuse and returns|troubleshoot|issues during|"
    r"sequencing run|ordering information|order(ing)? info|appendix|revision history|change log|"
    r"safety data|warranty|contact us|references|further reading"
)
MIN_CHARS = 15000     # shorter documents are used as they are


@dataclass
class Trimmed:
    text: str
    left_out: list[str] = field(default_factory=list)   # chapter titles not given to the model

    @property
    def note(self) -> str:
        if not self.left_out:
            return ""
        return "left out of the document (not bench procedure): " + "; ".join(self.left_out)


def trim_document(text: str) -> Trimmed:
    if len(text) < MIN_CHARS:
        return Trimmed(text)
    lines = text.splitlines()
    first_seen: dict[str, int] = {}
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        m = _CHAPTER.match(line)
        if not m:
            continue
        key = f"{int(m.group(1))}. {' '.join(m.group(2).split()).lower()}"
        if key in first_seen:
            # The second time a numbered title appears is the chapter itself
            # (the first is the table of contents).
            if not any(title == key for _, title in starts):
                starts.append((index, key))
        else:
            first_seen[key] = index
    if len(starts) < 3:
        return Trimmed(text)
    starts.sort()
    keep: list[str] = lines[: starts[0][0]]
    left_out: list[str] = []
    for n, (start, key) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        title = lines[start].strip()
        if _NOT_PROCEDURE.search(title):
            left_out.append(title)
            continue
        keep.extend(lines[start:end])
    if not left_out:
        return Trimmed(text)
    return Trimmed("\n".join(keep), left_out)
