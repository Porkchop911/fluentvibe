"""Carry edits made in FluentControl back into the Python protocol.

The Python draft stays the single source of truth (blocks, variables,
simulation, rubric). When someone adjusts the compiled script in
FluentControl — a volume, a labware position, a liquid class, a variable
default, an added or deleted command — this module finds what changed so the
change can be made in the Python, where the next compile keeps it:

* both scripts, the one fluentvibe compiled and the one saved in
  FluentControl, are parsed with the same parser
  (:func:`fluentvibe.decompiler.xscr_parser.parse_xscr`), so the comparison is
  symmetric; commands the parser keeps as raw XML are compared field by field
  on FluentControl's own XML;
* commands are aligned by kind, so inserted and deleted commands show up as
  such instead of shifting every later comparison;
* every change on a compiled command points at the Python line and group that
  emitted it (``Step.source_pos`` of the compiled IR, same order).

Workflow (see ``fluentvibe fc-open`` / ``fc-pull`` and the
``pull_fluentcontrol_edits`` authoring tool): compile and open the draft in
FluentControl's shell script, edit and save there, pull the changes, apply
them to the Python, compile and check again.
"""

from __future__ import annotations

import difflib
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Fields that differ between two saves of the same command without meaning
# anything for the protocol (identifiers, UI state, serialisation details).
_NOISE = {
    "Guid", "Id", "ID", "InternalID", "ObjectID", "TemplateGuid", "IsExpanded", "IsSelected",
    "ShowLabel", "Checksum", "LastModified", "ModifiedBy", "Timestamp",
}
_SKIP_MODEL_FIELDS = {"line_number", "source_pos", "step_type", "parameters", "stack_onto"}


@dataclass
class Change:
    kind: str  # changed | added | removed | variable
    command: str
    subject: str | None = None
    fields: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    fc_line: int | None = None
    python_line: int | None = None
    group: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "command": self.command,
            "subject": self.subject,
            "fields": {k: {"before": a, "after": b} for k, (a, b) in self.fields.items()},
            "fc_line": self.fc_line,
            "python_line": self.python_line,
            "group": self.group,
        }

    def describe(self) -> str:
        where = f"line {self.python_line}" if self.python_line else "no Python line"
        group = f" in {self.group!r}" if self.group else ""
        what = f"{self.command}" + (f" {self.subject!r}" if self.subject else "")
        if self.kind == "variable":
            (before, after), = self.fields.values()
            origin = f" ({where}{group})" if self.python_line else ""
            return f"variable {self.command}: default {before!r} -> {after!r}{origin}"
        if self.kind == "added":
            return f"added in FluentControl: {what}{group} (after {where}) {self._field_text(after_only=True)}"
        if self.kind == "removed":
            return f"removed in FluentControl: {what} ({where}{group})"
        return f"{what} ({where}{group}): {self._field_text()}"

    def _field_text(self, after_only: bool = False) -> str:
        if after_only:
            return ", ".join(f"{k}={b!r}" for k, (_a, b) in list(self.fields.items())[:8])
        return ", ".join(f"{k} {a!r} -> {b!r}" for k, (a, b) in list(self.fields.items())[:8])


def _xml_fields(raw_xml: str) -> dict[str, str]:
    """Leaf values of a command's XML, keyed by tag (repeats numbered)."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return {"raw_xml": raw_xml}
    parents = {child: parent for parent in root.iter() for child in parent}
    wrappers = {"string", "int", "double", "boolean", "long", "decimal", "Object"}
    out: dict[str, str] = {}
    for elem in root.iter():
        if len(elem) or elem.text is None or not elem.text.strip():
            continue
        tag = elem.tag.split("}")[-1]
        # A value in a type wrapper (<Volume><double>10</double></Volume>)
        # is named after the first real element above it.
        node = elem
        while tag in wrappers and node in parents:
            node = parents[node]
            tag = node.tag.split("}")[-1]
        if tag in _NOISE:
            continue
        key, n = tag, 2
        while key in out:
            key, n = f"{tag}#{n}", n + 1
        out[key] = elem.text.strip()
    return out


def _fields(step) -> dict[str, Any]:
    params = getattr(step, "parameters", None)
    if isinstance(params, dict) and params.get("raw_xml"):
        return _xml_fields(params["raw_xml"])
    data = step.model_dump(exclude=_SKIP_MODEL_FIELDS)
    return {k: v for k, v in data.items() if v not in (None, [], {})}


def _kind(step) -> str:
    kind = getattr(step, "step_type", type(step).__name__)
    return str(getattr(kind, "value", kind))


def _subject(step, fields: dict[str, Any]) -> str | None:
    for key in ("label", "labware_name", "LabwareName", "Label", "prompt"):
        value = getattr(step, key, None) or fields.get(key)
        if value:
            return str(value)[:60]
    return None


def _flatten(protocol) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []

    def walk(steps, group):
        for step in steps:
            out.append((group, step))
            walk(getattr(step, "steps", None) or (), group)

    for g in protocol.groups:
        walk(g.steps, g.name)
    return out


def _python_lines(wt, count: int) -> list[tuple[int | None, int | None]]:
    """(FluentControl line, Python line) of each compiled step, in script order."""
    if wt is None:
        return [(None, None)] * count
    try:
        steps = _flatten(wt.to_protocol())
    except Exception:
        return [(None, None)] * count
    lines = [(getattr(s, "line_number", None), getattr(getattr(s, "source_pos", None), "line", None)) for _, s in steps]
    return lines if len(lines) == count else [(None, None)] * count


def diff_scripts(base_xscr: Path | str, edited_xscr: Path | str, *, wt=None) -> list[Change]:
    """What changed from ``base_xscr`` (compiled) to ``edited_xscr`` (saved in FluentControl).

    ``wt`` is the Worktable the base was compiled from; with it, changes carry
    the Python line of the command they touch.
    """
    from ..decompiler.xscr_parser import parse_xscr

    base, edited = parse_xscr(base_xscr), parse_xscr(edited_xscr)
    a, b = _flatten(base), _flatten(edited)
    positions = _python_lines(wt, len(a))
    changes: list[Change] = []

    def change_for(kind, index, group, step, fields):
        fc_line, py_line = positions[index] if index is not None and index < len(positions) else (None, None)
        return Change(kind=kind, command=_kind(step), subject=_subject(step, _fields(step)), fields=fields,
                      fc_line=fc_line, python_line=py_line, group=group)

    # Align on (command, what it acts on) so a moved or deleted command does
    # not pair unrelated neighbours; a changed subject still pairs up through
    # an equal-length "replace" block.
    def signature(step):
        return (_kind(step), _subject(step, _fields(step)))

    matcher = difflib.SequenceMatcher(a=[signature(s) for _, s in a], b=[signature(s) for _, s in b], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op in ("equal", "replace") and (i2 - i1) == (j2 - j1):
            for offset in range(i2 - i1):
                (group, sa), (_g, sb) = a[i1 + offset], b[j1 + offset]
                fa, fb = _fields(sa), _fields(sb)
                diff = {k: (fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)}
                if _kind(sa) != _kind(sb):
                    changes.append(change_for("removed", i1 + offset, group, sa, {}))
                    changes.append(change_for("added", i1 + offset, group, sb, {k: (None, v) for k, v in fb.items()}))
                elif diff:
                    changes.append(change_for("changed", i1 + offset, group, sa, diff))
            continue
        for index in range(i1, i2):
            group, step = a[index]
            changes.append(change_for("removed", index, group, step, {}))
        for index in range(j1, j2):
            group, step = b[index]
            anchor = max(i1 - 1, 0)
            changes.append(change_for("added", anchor, group, step,
                                      {k: (None, v) for k, v in _fields(step).items()}))

    before, after = dict(base.variable_defaults or {}), dict(edited.variable_defaults or {})
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changes.append(Change(kind="variable", command=name, fields={"default": (before.get(name), after.get(name))}))
    return changes


def roundtrip_message(changes: list[Change]) -> str:
    """Instructions for the authoring model to carry FluentControl edits into the Python."""
    if not changes:
        return "The script in FluentControl matches the compiled draft: nothing to carry over."
    lines = [
        "The script was edited in FluentControl. Make the same changes in the Python draft so the next "
        "compile keeps them: change the argument that produced each command (a block argument, a "
        "wt.place position/catalog, a variable default), not the generated command itself. Then run "
        "simulate_python_draft. Changes:",
    ]
    lines += [f"- {c.describe()}" for c in changes[:25]]
    if len(changes) > 25:
        lines.append(f"- ... and {len(changes) - 25} more")
    return "\n".join(lines)


def write_report(changes: list[Change], path: Path) -> None:
    path.write_text(json.dumps([c.to_dict() for c in changes], indent=2, default=str), encoding="utf-8")


def locate_variables(changes: list[Change], source: str | None) -> list[Change]:
    """Give variable changes the Python line that declares them.

    A literal ``"NAME"`` in the source wins; otherwise a block call whose
    ``name=`` produces the variable's prefix (blocks declare ``<NAME>_*``).
    """
    if not source:
        return changes
    import re

    from ..blocks.common import variable_prefix

    lines = source.splitlines()
    block_names = [
        (index + 1, variable_prefix(match.group(1)))
        for index, text in enumerate(lines)
        for match in re.finditer(r"""\bname\s*=\s*["']([^"']+)["']""", text)
    ]
    for change in changes:
        if change.kind != "variable" or change.python_line:
            continue
        literal = next((i + 1 for i, text in enumerate(lines) if f'"{change.command}"' in text
                        or f"'{change.command}'" in text), None)
        if literal:
            change.python_line = literal
            continue
        owner = max(((line, prefix) for line, prefix in block_names
                     if prefix and change.command.startswith(prefix + "_")),
                    key=lambda item: len(item[1]), default=None)
        if owner:
            # The block call starts a few lines above its name= argument.
            start = owner[0]
            while start > 1 and "(" not in lines[start - 1]:
                start -= 1
            change.python_line = start
            change.group = f"block variable of name={change.command[:len(owner[1])]!r}"
    return changes
