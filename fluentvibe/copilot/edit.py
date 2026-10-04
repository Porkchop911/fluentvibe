"""LLM 'edit this region with an instruction' for protocol authoring.

The Continue-style inline edit: select some lines, say what you want ("add a
return-tips step", "use 200 uL tips"), and the model rewrites just that region.
The rewrite is then **re-validated** through the analyzer, so the caller can warn
before applying a suggestion that doesn't build or simulate.

The chat client is injectable (offline-testable); the default uses the
env-configured endpoint. See docs/copilot-design.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .analyzer import analyze_source

_SYSTEM_PROMPT = (
    "You edit Tecan FluentControl liquid-handling protocols written in the fluentvibe Python API. You are given "
    "the whole file for context, a selected region, and an instruction. Rewrite ONLY the selected region to "
    "satisfy the instruction; change nothing the instruction does not ask for, and add no steps, reagents or "
    "chemistry of your own. Preserve the surrounding indentation.\n"
    "Heads: FCA = wt.liha (8 channels, FCA tip boxes), MCA = wt.mca96 (96 channels, MCA tip boxes), RGA = "
    "wt.gripper. Prefer the blocks from fluentvibe.blocks over hand-written head calls: distribute_reagent "
    "(FCA, reagent into every well), add_reagent (MCA, bulk liquid from an SBS reservoir), stamp (MCA, plate to "
    "plate), transfer_volumes / distribute_volumes (FCA, a volume per well), pool_wells / pool_columns, "
    "mix_wells, remove_liquid, separate / release (magnet on / off), offdeck_step (operator step), "
    "spri_cleanup. Use the labware variables, variables and liquid classes the file already has. A block the "
    "file does not import yet may be used; its import is added for you.\n"
    "Return ONLY the replacement Python for the selected region: no explanations, no markdown code fences."
)

_FENCE_RE = re.compile(r"^```[\w-]*\n(.*)\n```$", re.DOTALL)


@dataclass
class EditResult:
    new_text: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)   # blocks the edit uses that the file does not import
    import_line: int = 0                               # 1-based line to insert the import before
    proposed_source: str = ""                          # the whole file with the edit (for a preview)

    @property
    def introduces_errors(self) -> bool:
        return any(d.get("severity") == "error" for d in self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "new_text": self.new_text,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "diagnostics": self.diagnostics,
            "introduces_errors": self.introduces_errors,
            "imports": self.imports,
            "import_line": self.import_line,
            "import_text": _import_text(self.imports),
            "proposed_source": self.proposed_source,
        }


def _strip_fences(text: str) -> str:
    # Detect fences on a whitespace-stripped copy, but return the inner content
    # verbatim so the code's own indentation is preserved.
    m = _FENCE_RE.match(text.strip())
    if m:
        return m.group(1)
    return text.strip("\n")


def _build_messages(source: str, selection: str, instruction: str) -> list[dict[str, str]]:
    user = (
        f"Instruction: {instruction}\n\n"
        f"Full file:\n```python\n{source}\n```\n\n"
        f"Selected region to rewrite:\n```python\n{selection}\n```"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _replace_lines(lines: list[str], start_line: int, end_line: int, new_text: str) -> str:
    head = lines[: start_line - 1]
    tail = lines[end_line:]
    return "\n".join([*head, *new_text.splitlines(), *tail])


def edit_region(
    source: str,
    start_line: int,
    end_line: int,
    instruction: str,
    *,
    client: Optional[Any] = None,
    path: str = "<protocol>",
    revalidate: bool = True,
) -> EditResult:
    """Rewrite lines ``start_line``..``end_line`` (1-based, inclusive) per ``instruction``.

    Returns the replacement text plus, when ``revalidate`` is set, the analyzer
    diagnostics for the file *after* applying the edit — so the caller can refuse
    or warn on a regression.
    """
    lines = source.splitlines()
    start_line = max(1, start_line)
    end_line = min(len(lines), max(start_line, end_line))
    selection = "\n".join(lines[start_line - 1 : end_line])

    if client is None:
        from ..authoring.lm_client import LMStudioChatClient

        client = LMStudioChatClient()
    message = client.complete(
        messages=_build_messages(source, selection, instruction), tools=[]
    )
    new_text = _strip_fences(message.get("content") or "")
    return propose_region(source, start_line, end_line, new_text, path=path, revalidate=revalidate)


def propose_region(source: str, start_line: int, end_line: int, new_text: str, *,
                   path: str = "<protocol>", revalidate: bool = True) -> EditResult:
    """Lines ``start_line``..``end_line`` replaced by ``new_text``: the whole proposed
    file (with any block import it needs) and, with ``revalidate``, its problems.
    Shared by Ctrl+I and the chat's "Apply to selection"."""
    lines = source.splitlines()
    start_line = max(1, start_line)
    end_line = min(len(lines), max(start_line, end_line))
    imports = _missing_block_imports(source, new_text) if new_text else []
    import_line = _import_insert_line(lines) if imports else 0
    proposed = ""
    diagnostics: list[dict[str, Any]] = []
    if new_text:
        edited_lines = _replace_lines(lines, start_line, end_line, new_text).splitlines()
        if imports:
            edited_lines.insert(import_line - 1, _import_text(imports))
        proposed = "\n".join(edited_lines) + ("\n" if source.endswith("\n") else "")
        if revalidate:
            diagnostics = [d.to_dict() for d in analyze_source(proposed, path)]

    return EditResult(
        new_text=new_text,
        start_line=start_line,
        end_line=end_line,
        diagnostics=diagnostics,
        imports=imports,
        import_line=import_line,
        proposed_source=proposed,
    )


def _import_text(names: list[str]) -> str:
    return f"from fluentvibe.blocks import {', '.join(names)}" if names else ""


def _missing_block_imports(source: str, new_text: str) -> list[str]:
    """Blocks called in ``new_text`` that ``source`` neither imports nor defines."""
    try:
        from .. import blocks

        names = set(getattr(blocks, "__all__", ()))
    except Exception:  # noqa: BLE001
        return []
    used = {n for n in re.findall(r"(?<![\w.])([a-z_]\w*)\s*\(", new_text) if n in names}
    have = set(re.findall(r"\b(\w+)\b", " ".join(re.findall(
        r"from\s+fluentvibe\.blocks\s+import\s+(\([^)]*\)|[^\n]+)", source))))
    return sorted(n for n in used if n not in have and not re.search(rf"^\s*def\s+{n}\b", source, re.M))


def _import_insert_line(lines: list[str]) -> int:
    """1-based line after the last top-level import (the new import goes there)."""
    last = 0
    depth = 0
    for i, line in enumerate(lines, 1):
        if depth:
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                depth, last = 0, i
            continue
        if re.match(r"(from\s+\S+\s+import|import)\s", line):
            depth = line.count("(") - line.count(")")
            if depth <= 0:
                depth, last = 0, i
        elif line.strip() and not line.startswith("#") and last:
            break
    return last + 1
