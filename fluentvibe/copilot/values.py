"""Protocol-aware value completions: what this protocol and this deck allow here.

Pylance already completes methods and shows signatures (the API is typed); what
it cannot know is the domain: which placed labware fits ``tips=`` of this block
(an FCA box for an FCA block), where on the deck a trough can go and which head
reaches it, which liquid classes and reagent roles exist, which wells a plate
has. All deterministic, no model, no execution of the protocol.

The cursor's call is found by scanning the text before it (calls span lines),
and the placed labware by reading the ``x = wt.place(Class("Label", ...), "Site",
n)`` lines, so a file that does not parse yet still completes.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from .complete import Completion

_WINDOW_LINES = 80
_HEAD_ALIAS_RE = re.compile(r"^\s*(\w+)\s*=\s*(?:[\w.]*\.)?wt\.(liha|mca96|gripper)\s*(?:#.*)?$", re.M)
_PLACE_RE = re.compile(r"^\s*(\w+)\s*=\s*[\w.]*\.place\(", re.M)
_WELLS = tuple(f"{r}{c}" for c in range(1, 13) for r in "ABCDEFGH")
_ROLES = ("plain", "bead_carrier", "analyte", "eluent")
_FCA_BLOCKS = {"distribute_reagent", "distribute_volumes", "transfer_volumes", "pool_wells", "pool_columns"}
_TIP_KWS = {"tips", "reagent_tips", "sample_tips", "eluate_tips", "work_tips", "mix_tips", "fca_tips"}
_TROUGH_KWS = {"reagent_source", "bead_source", "elution_source", "wash_source", "waste"}
_PLATE_KWS = {"plate", "dest", "sample_plate", "eluate_plate"}
_LC_KWS = {"liquid_class", "mix_liquid_class", "empty_liquid_class"}
_WELL_KWS = {"wells", "source_wells", "dest_well"}
_SLIM = {"25ml_short", "100ml"}
# Troughs the MCA pipettes from (it cannot use slim troughs); the rest of a block's
# trough arguments are FCA (or either).
_MCA_TROUGH_KWS = {("add_reagent", "reagent_source"), ("spri_cleanup", "wash_source")}


@dataclass
class Placed:
    var: str
    cls: str
    label: str
    site: Optional[str]
    index: Optional[int]
    catalog: str = ""

    @property
    def slim(self) -> bool:
        """A slim trough (``25ml_short`` / ``100ml``): FCA only, the MCA cannot pipette in it."""
        return self.kind == "trough" and self.catalog.strip() in _SLIM

    @property
    def kind(self) -> str:
        c = self.cls
        if "Box" in c:
            return "fca_tips" if c.startswith("FCA") else "mca_tips" if c.startswith("MCA") else "tips"
        if "Trough" in c:
            return "trough"
        if "Magnet" in c:
            return "magnet"
        if "Plate" in c:
            return "plate"
        return "other"


@dataclass
class OpenCall:
    callee: str                 # "distribute_reagent", "wt.place", "fca.aspirate"
    arg_index: int              # top-level commas typed since "("
    keyword: Optional[str]      # "tips" in `tips=Fc|`
    partial: str                # the word being typed ("" at an empty value)
    quote: Optional[str]        # the open string's quote, if the cursor is inside one
    segment: str                # the current argument's text up to the cursor
    args: str = ""              # all argument text since "(" up to the cursor


# ── Reading the protocol ──────────────────────────────────────────────

def head_aliases(source: str) -> dict[str, str]:
    """``fca = wt.liha`` → {"fca": "liha"}: the head a name stands for, by assignment."""
    return {m.group(1): m.group(2) for m in _HEAD_ALIAS_RE.finditer(source)}


def _call_args(source: str, open_paren: int) -> Optional[str]:
    """The text between ``open_paren`` and its matching ``)``, or None."""
    depth, quote, i = 0, None, open_paren
    while i < len(source):
        ch = source[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return source[open_paren + 1:i]
        i += 1
    return None


def placed_labware(source: str) -> list[Placed]:
    """Every ``var = wt.place(Class("Label", ...), "Site", n)`` in ``source``."""
    out: list[Placed] = []
    for m in _PLACE_RE.finditer(source):
        args = _call_args(source, m.end() - 1)
        if args is None:
            continue
        try:
            call = ast.parse(f"f({args})", mode="eval").body
        except SyntaxError:
            continue
        if not call.args or not isinstance(call.args[0], ast.Call):
            continue
        ctor = call.args[0]
        cls = ctor.func.id if isinstance(ctor.func, ast.Name) else getattr(ctor.func, "attr", "")
        label = ctor.args[0].value if ctor.args and isinstance(ctor.args[0], ast.Constant) else m.group(1)
        site = call.args[1].value if len(call.args) > 1 and isinstance(call.args[1], ast.Constant) else None
        index = call.args[2].value if len(call.args) > 2 and isinstance(call.args[2], ast.Constant) else None
        catalog = next((k.value.value for k in ctor.keywords
                        if k.arg == "catalog" and isinstance(k.value, ast.Constant)), "")
        out.append(Placed(m.group(1), cls, str(label), site if isinstance(site, str) else None,
                          index if isinstance(index, int) else None, str(catalog)))
    return out


def open_call(source: str, line: int, character: int) -> Optional[OpenCall]:
    """The innermost call the cursor is inside, read from the text before it."""
    lines = source.splitlines()
    if not (0 <= line < len(lines)):
        return None
    first = max(0, line - _WINDOW_LINES)
    text = "\n".join(lines[first:line] + [lines[line][:character]])
    stack: list[tuple[str, int]] = []     # (bracket, position)
    commas: list[int] = []                # last top-level comma per open "(" (parallel to stack)
    quote, quote_at, i = None, -1, 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote or ch == "\n":
                quote = None
        elif ch == "#":
            nl = text.find("\n", i)
            i = len(text) if nl < 0 else nl
            continue
        elif ch in "\"'":
            quote, quote_at = ch, i
        elif ch in "([{":
            stack.append((ch, i))
            commas.append(i)
        elif ch in ")]}":
            if stack:
                stack.pop()
                commas.pop()
        elif ch == "," and stack:
            commas[-1] = i
        i += 1
    calls = [k for k, (b, _) in enumerate(stack) if b == "("]
    if not calls:
        return None
    k = calls[-1]
    pos = stack[k][1]
    name = re.search(r"([A-Za-z_][\w.]*)\s*$", text[:pos])
    if name is None:
        return None
    # The argument being typed starts after the last top-level comma of this call.
    start = commas[k] + 1
    segment = text[start:]
    arg_index = _top_level_commas(text[pos + 1:])
    kw = re.match(r"\s*(\w+)\s*=(?!=)", segment)
    word = re.search(r"([\w\- ]*)$" if quote else r"(\w*)$", text[quote_at + 1:] if quote else segment)
    return OpenCall(callee=name.group(1), arg_index=arg_index, keyword=kw.group(1) if kw else None,
                    partial=word.group(1) if word else "", quote=quote, segment=segment, args=text[pos + 1:])


def _top_level_commas(args: str) -> int:
    depth, quote, n = 0, None, 0
    for ch in args:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
    return n


# ── What the deck and lab allow ───────────────────────────────────────

def _profile():
    try:
        from ..authoring.profile import profile_from_env

        return profile_from_env()
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=8)
def _reach(root: str) -> dict:
    try:
        from pathlib import Path

        return json.loads((Path(root) / "reach.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _reach_for_profile() -> dict:
    p = _profile()
    return _reach(str(p.root)) if p is not None else {}


def liquid_classes() -> list[str]:
    try:
        from ..authoring.lab_scope import REQUIRED_LIQUID_CLASSES, load_lab_scope

        scope = load_lab_scope("enforce")
        names = set(scope.liquid_classes or ()) | set(REQUIRED_LIQUID_CLASSES)
    except Exception:  # noqa: BLE001
        names = {"Water Free Single", "Water Mix", "Empty Tip"}
    return sorted(names, key=lambda n: (n != "Water Free Single", n))


def _positions(reach: dict) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for bucket in ("reachable", "unreachable", "unknown"):
        for sites in (reach.get(bucket) or {}).values():
            if isinstance(sites, dict):
                for site, idx in sites.items():
                    out.setdefault(site, set()).update(int(i) for i in idx or [])
    return out


@lru_cache(maxsize=8)
def _deck_table(root: str) -> dict[str, set[int]]:
    """The profile deck skill's "Valid deck positions" table."""
    from pathlib import Path

    out: dict[str, set[int]] = {}
    for md in Path(root).glob("deck-*.md"):
        for m in re.finditer(r"^\|\s*`([^`]+)`\s*\|\s*([\d,\s]+)\|", md.read_text(encoding="utf-8"), re.M):
            out.setdefault(m.group(1), set()).update(int(x) for x in re.findall(r"\d+", m.group(2)))
    return out


def _all_positions() -> dict[str, set[int]]:
    """Positions the profile knows: its deck table plus the measured reach."""
    p = _profile()
    out: dict[str, set[int]] = {}
    if p is not None:
        for site, idx in _deck_table(str(p.root)).items():
            out.setdefault(site, set()).update(idx)
    for site, idx in _positions(_reach_for_profile()).items():
        out.setdefault(site, set()).update(idx)
    return out


def _reach_note(reach: dict, site: str, index: int) -> str:
    parts = []
    for head, name in (("mca96", "MCA"), ("liha", "FCA")):
        if index in ((reach.get("reachable") or {}).get(head) or {}).get(site, []):
            parts.append(f"{name} reaches")
        elif index in ((reach.get("unreachable") or {}).get(head) or {}).get(site, []):
            parts.append(f"{name} cannot reach")
    return ", ".join(parts)


# ── Completions ───────────────────────────────────────────────────────

def _function(call: OpenCall, source: str) -> tuple[str, Optional[str]]:
    """(function name, head) for the call: ("aspirate", "liha") for `fca.aspirate`."""
    if "." not in call.callee:
        return call.callee, None
    receiver, fn = call.callee.rsplit(".", 1)
    aliases = head_aliases(source)
    last = receiver.rsplit(".", 1)[-1]
    head = aliases.get(last) or (last if last in ("liha", "mca96", "gripper") else None)
    if head is None:
        low = last.lower()
        head = "liha" if low in ("fca", "liha") else "mca96" if low in ("mca", "mca96") else \
            "gripper" if low in ("rga", "gripper") else None
    return fn, head


def _wanted_kinds(fn: str, head: Optional[str], call: OpenCall) -> Optional[set[str]]:
    kw = call.keyword
    if kw in _TIP_KWS:
        if kw == "fca_tips" or fn in _FCA_BLOCKS:
            return {"fca_tips"}
        return {"mca_tips"}
    if kw in _TROUGH_KWS:
        return {"trough"}
    if kw == "source":
        return {"trough"} if fn in ("distribute_reagent", "distribute_volumes") else {"plate"}
    if kw in _PLATE_KWS or kw == "labware":
        return {"plate"}
    if kw in ("magnet", "onto"):
        return {"magnet"}
    if kw is None and call.arg_index == 0 and head is not None:
        if fn == "get_tips":
            return {"fca_tips"}
        if fn in ("pick_up", "return_tips"):
            return {"mca_tips"}
        if fn == "empty_tips":
            return {"trough"}
        if fn in ("aspirate", "dispense", "mix"):
            return {"trough", "plate"}
        if head == "gripper" and fn == "move":
            return {"plate"}
    return None


def value_completions(source: str, line: int, character: int) -> list[Completion]:
    call = open_call(source, line, character)
    if call is None:
        return []
    fn, head = _function(call, source)
    col = character - len(call.partial)
    if call.quote:
        if call.keyword in _LC_KWS:
            return _strings(liquid_classes(), call.partial, col, "liquid class")
        if call.keyword == "role":
            return _strings(_ROLES, call.partial, col, "reagent role")
        if call.keyword in _WELL_KWS:
            return _strings(_WELLS, call.partial, col, "well")
        if fn == "place" and call.arg_index == 1:
            used = {p.site for p in placed_labware(source) if p.site}
            names = sorted(set(_all_positions()) | used, key=lambda s: (s not in used, s))
            return _strings(names, call.partial, col, "deck position")
        return []
    if fn == "place" and call.arg_index == 2:
        return _free_indices(source, call, col)
    if call.keyword in _LC_KWS and not call.partial:
        return [Completion(label=f'"{n}"', kind="value", insert_text=f'"{n}"', replace_start=col,
                           detail="liquid class") for n in liquid_classes()]
    kinds = _wanted_kinds(fn, head, call)
    if not kinds:
        return []
    mca_trough = (fn, call.keyword) in _MCA_TROUGH_KWS or (head == "mca96" and call.arg_index == 0)
    fca_trough = fn in _FCA_BLOCKS or call.keyword in ("bead_source", "elution_source") or head == "liha"
    ranked = []
    for p in placed_labware(source):
        if p.kind not in kinds or not p.var.startswith(call.partial):
            continue
        if mca_trough and p.slim:
            continue                      # the MCA cannot pipette in a slim trough
        wasteish = "waste" in (p.var + p.label).lower()
        if call.keyword == "waste":
            rank = 0 if wasteish else 1
        else:
            rank = 2 if wasteish else (0 if (fca_trough and p.slim) or not fca_trough else 1)
        where = f" at {p.site} {p.index}" if p.site else ""
        cat = f", {p.catalog}" if p.catalog and p.kind == "trough" else ""
        ranked.append((rank, Completion(label=p.var, kind="value", insert_text=p.var, replace_start=col,
                                        detail=f'{p.cls}("{p.label}"{cat}){where}')))
    ranked.sort(key=lambda rc: rc[0])
    out = [c for _, c in ranked]
    for i, c in enumerate(out):
        c.sort_text = f"{i:03d}"
    return out


def _strings(values, partial: str, col: int, detail: str) -> list[Completion]:
    low = partial.lower()
    return [Completion(label=v, kind="value", insert_text=v, replace_start=col, detail=detail)
            for v in values if v.lower().startswith(low)]


def _free_indices(source: str, call: OpenCall, col: int) -> list[Completion]:
    site = re.search(r"""["'](\w+)["']\s*,\s*\d*$""", call.args)
    if site is None:
        return []
    name = site.group(1)
    reach = _reach_for_profile()
    indices = sorted(_all_positions().get(name, set()))
    used = {p.index for p in placed_labware(source) if p.site == name}
    return [Completion(label=str(i), kind="value", insert_text=str(i), replace_start=col,
                       detail=f"{name} {i}" + (f": {n}" if (n := _reach_note(reach, name, i)) else ""))
            for i in indices if i not in used and str(i).startswith(call.partial)]

