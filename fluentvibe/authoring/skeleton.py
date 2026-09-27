"""Skeleton draft: a runnable first protocol built from a spec and a deck profile.

Stage D of docs/authoring-strategy.md, made deterministic where it can be.
Given an approved :class:`~fluentvibe.authoring.bench_spec.BenchSpec` and a
workspace-app profile, :func:`build_skeleton` writes Python source that:

* binds the profile's workspace and places labware on free positions of the
  right kind (plates on 61 mm nests, troughs on trough sites, the magnet and
  waste on their preferred positions), skipping positions the workspace already
  occupies;
* fills reagents — kit reagents with their spec supply, lab stock with an
  estimate of what the run needs;
* turns each spec step into a ``fluentvibe.blocks`` call: the physical
  primitives (add: ``distribute_reagent`` / ``add_reagent``; transfer:
  ``stamp``; ``remove_liquid``; ``mix_wells``; magnet ``separate`` /
  ``release``), the macros (``spri_cleanup`` for a ``bead_cleanup`` step,
  ``pool_columns`` for ``pool``), room-temperature incubations into
  ``wt.wait``, and every stretch of off-deck/manual steps into one
  ``offdeck_step``.

A step is never mapped onto a block of another kind: a ``custom`` step stays a
``TODO`` for hand authoring, and a spec with open values (an ``add`` without a
volume) raises :class:`OpenValues` listing the questions to ask. Procedural
choices the spec does not fix (mix counts, settle times, tip boxes) are marked
``# ASSUMED`` so a model or a person can review them. The skeleton is a
starting draft, not a verdict: authoring still simulates, compiles and checks it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .bench_spec import BenchSpec, SpecReagent, SpecStep, expand_repeats, open_values

SKELETON_MARKER = "# BENCH SPEC SKELETON"
_PLATE_LOCATION = "Nest61mm_Pos"
_TROUGH_PREFIX = "WS_"
_LARGE_RESERVOIR_LOCATION = "Nest7mm_Pos"
_DEFAULT_LC = "Water Free Single"


@dataclass
class _Deck:
    workspace_name: str
    workspace_guid: str
    plate_catalog: str
    free_nests: list[tuple[str, int]]
    free_trough_sites: list[tuple[str, int]]
    magnet: tuple[str, str, int] | None          # (catalog, location, position)
    waste: tuple[str, str, int] | None           # (catalog, location, position)
    mca_tips: str | None
    fca_tips: tuple[str, str] | None             # (catalog, python class)
    # MCA96 blocks pipette reagents from SBS reservoirs (slim troughs do not
    # fit the 96-tip head). Verified in FluentControl on the 1080 deck:
    # "60ml SBS MCA96" connects to a 61 mm nest, "300ml SBS" to a 7 mm nest;
    # "MCA96 200ml" has no connector on the 61 mm nest. Large reservoirs are
    # only used on 7 mm nests the profile's reach.json measured as MCA-reachable.
    free_large_sites: list[tuple[str, int]] = field(default_factory=list)
    reservoir_small: str = "60ml SBS MCA96"
    reservoir_large: str = "300ml SBS"
    # Reagents (beads, buffers, master mixes) come from slim troughs via the
    # FCA; the MCA only takes cheap bulk liquids (ethanol, wash, water).
    slim_small: str = "25ml_short"
    slim_large: str = "100ml"
    liquid_class: str = _DEFAULT_LC


def _reach(root: Path) -> dict[str, Any]:
    path = root / "reach.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_deck(profile_dir: Path | str) -> _Deck:
    """Read the placement facts a skeleton needs from a workspace-app profile."""
    root = Path(profile_dir)
    data = json.loads((root / "workspace_profile.json").read_text(encoding="utf-8"))
    ws = data["workspace"]
    common = data.get("common_labware") or []
    deck = data.get("deck") or {}
    occupied = {
        (p["location"], int(p["position"]))
        for p in deck.get("positions") or []
        if p.get("occupied_by")
    }
    summary = deck.get("position_summary_by_location") or {}

    def first(pred) -> dict[str, Any] | None:
        return next((item for item in common if pred(item)), None)

    plate = first(lambda x: x.get("category") == "plate" and x.get("python_class") == "Plate96")
    magnet = first(lambda x: x.get("category") == "magnet_rack")
    waste = first(lambda x: "waste" in str(x.get("label", "")).lower())
    mca = first(lambda x: x.get("category") == "tip_box" and "MCA96" in str(x.get("catalog_name"))
                and "200" in str(x.get("catalog_name")))
    mca = mca or first(lambda x: x.get("category") == "tip_box" and "MCA96" in str(x.get("catalog_name")))
    fca = first(lambda x: x.get("category") == "tip_box" and "FCA" in str(x.get("catalog_name"))
                and "200" in str(x.get("catalog_name")))
    fca = fca or first(lambda x: x.get("category") == "tip_box" and "FCA" in str(x.get("catalog_name")))
    liquid = (data.get("liquid_class") or {}).get("name") or _DEFAULT_LC

    reserved: set[tuple[str, int]] = set()
    magnet_slot = None
    if magnet and magnet.get("preferred_location"):
        magnet_slot = (magnet["catalog_name"], magnet["preferred_location"], int(magnet["preferred_position"]))
        reserved.add((magnet_slot[1], magnet_slot[2]))
    waste_slot = None
    if waste and waste.get("preferred_location"):
        waste_slot = (waste["catalog_name"], waste["preferred_location"], int(waste["preferred_position"]))
        reserved.add((waste_slot[1], waste_slot[2]))

    free_nests = [
        (_PLATE_LOCATION, pos) for pos in summary.get(_PLATE_LOCATION, [])
        if (_PLATE_LOCATION, pos) not in occupied and (_PLATE_LOCATION, pos) not in reserved
    ]
    mca_reachable = (_reach(root).get("reachable") or {}).get("mca96") or {}
    free_large_sites = [
        (_LARGE_RESERVOIR_LOCATION, pos) for pos in summary.get(_LARGE_RESERVOIR_LOCATION, [])
        if (_LARGE_RESERVOIR_LOCATION, pos) not in occupied and (_LARGE_RESERVOIR_LOCATION, pos) not in reserved
        and pos in mca_reachable.get(_LARGE_RESERVOIR_LOCATION, [])
    ]
    free_troughs = [
        (loc, pos) for loc, positions in summary.items() if loc.startswith(_TROUGH_PREFIX)
        for pos in positions if (loc, pos) not in occupied and (loc, pos) not in reserved
    ]
    # A profile that lists the magnet / waste without a position: take a free
    # one (the magnet on a plate nest, the waste reservoir on an MCA-reachable
    # 7 mm nest, as on the verified decks) instead of failing.
    if magnet and magnet_slot is None and free_nests:
        loc, pos = free_nests.pop()
        magnet_slot = (magnet["catalog_name"], loc, pos)
    if waste and waste_slot is None and free_large_sites:
        loc, pos = free_large_sites.pop(0)
        waste_slot = (waste["catalog_name"], loc, pos)
    return _Deck(
        workspace_name=ws["name"],
        workspace_guid=ws["guid"],
        plate_catalog=(plate or {}).get("catalog_name", "96_ABgene_SuperPlate_Thermo_AB2800"),
        free_nests=free_nests,
        free_trough_sites=free_troughs,
        free_large_sites=free_large_sites,
        magnet=magnet_slot,
        waste=waste_slot,
        mca_tips=(mca or {}).get("catalog_name"),
        fca_tips=((fca or {}).get("catalog_name"), (fca or {}).get("python_class", "FCA200Box")) if fca else None,
        liquid_class=liquid,
    )


def _label(text: str) -> str:
    """A FluentControl-safe labware name: letters, digits, '_' and '-' only
    ("EtOH 70%_trough" -> "EtOH_70_trough"; FC rejects e.g. '%')."""
    out = re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_")
    return re.sub(r"_+", "_", out) or "Labware"


def _ident(text: str) -> str:
    out = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    if not out or out[0].isdigit():
        out = f"x_{out}"
    return out


@dataclass
class _Writer:
    deck: _Deck
    placements: list[str] = field(default_factory=list)
    fills: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    names: set[str] = field(default_factory=set)
    handoff: tuple[str, int] | None = None
    positions: dict[str, tuple[str, int]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    retired: list[str] = field(default_factory=list)
    swaps: int = 0
    # Labware placed mid-run (after a swap): its fills follow its placement.
    placed_in_body: set[str] = field(default_factory=set)
    fca_boxes: list[str] = field(default_factory=list)
    fca_tip_uses: int = 0
    catalog_of: dict[str, str] = field(default_factory=dict)

    def var(self, base: str) -> str:
        name = _ident(base)
        candidate, n = name, 2
        while candidate in self.names:
            candidate, n = f"{name}_{n}", n + 1
        self.names.add(candidate)
        return candidate

    def retire(self, *variables: str) -> None:
        """Labware no later step uses: its nest may be reclaimed by an operator swap."""
        for var in variables:
            if var in self.positions and var not in self.retired:
                self.retired.append(var)

    def _reclaim(self) -> tuple[str, int]:
        """Free a nest mid-run: the operator takes spent labware off the deck."""
        if not self.retired:
            raise ValueError("skeleton: the deck has no free 61 mm nest left for another plate or tip box")
        old = self.retired.pop(0)
        loc, pos = self.positions.pop(old)
        self.body.append(f"    wt.user_prompt({json.dumps(f'Take the spent {self.labels[old]} off {loc} {pos}.')})")
        self.body.append(f"    wt.remove({old})")
        self.swaps += 1
        return loc, pos

    def nest(self) -> tuple[str, int]:
        if self.deck.free_nests:
            return self.deck.free_nests.pop(0)
        return self._reclaim()

    def _put(self, label: str, expr: str) -> str:
        safe = _label(label)
        expr = expr.replace(f'("{label}"', f'("{safe}"', 1)
        label = safe
        # FluentControl labware names are unique for the whole script, even
        # after the first one was removed.
        taken = set(self.labels.values())
        if label in taken:
            n = 2
            while f"{label}_{n}" in taken:
                n += 1
            expr = expr.replace(f'("{label}"', f'("{label}_{n}"', 1)
            label = f"{label}_{n}"
        var = self.var(label)
        if self.deck.free_nests:
            loc, pos = self.deck.free_nests.pop(0)
            self.placements.append(f'    {var} = wt.place({expr}, "{loc}", {pos})')
        else:
            loc, pos = self._reclaim()
            self.body[-2] = self.body[-2].replace(".\")", f' and put a fresh {label} there.\")')
            self.body.append(f'    {var} = wt.place({expr}, "{loc}", {pos})')
            self.placed_in_body.add(var)
        self.positions[var] = (loc, pos)
        self.labels[var] = label
        return var

    def plate(self, label: str) -> str:
        return self._put(label, f'Plate96("{label}", catalog="{self.deck.plate_catalog}")')

    def mca_box(self, label: str) -> str:
        if not self.deck.mca_tips:
            raise ValueError("skeleton: the profile lists no MCA96 tip box")
        cls = "MCA200Box" if "200" in self.deck.mca_tips else "MCA100Box"
        return self._put(label, f'{cls}("{label}", catalog="{self.deck.mca_tips}")')

    def fca_box(self, label: str) -> str:
        if not self.deck.fca_tips:
            raise ValueError("skeleton: the profile lists no FCA tip box")
        catalog, cls = self.deck.fca_tips
        return self._put(label, f'{cls}("{label}", catalog="{catalog}")')

    def slim_trough(self, label: str, need_ul: float) -> str:
        """A slim trough on a trough site, for reagents the FCA dispenses."""
        label = _label(label)
        taken = set(self.labels.values())
        base, n = label, 2
        while label in taken:
            label, n = f"{base}_{n}", n + 1
        if not self.deck.free_trough_sites:
            raise ValueError("skeleton: the deck has no free trough site left for an FCA reagent")
        loc, pos = self.deck.free_trough_sites.pop(0)
        var = self.var(label)
        big = need_ul > _SLIM_TROUGH_FILL_UL
        cls, catalog = ("Trough100mL", self.deck.slim_large) if big else ("Trough25mL", self.deck.slim_small)
        self.catalog_of[var] = catalog
        self.placements.append(f'    {var} = wt.place({cls}("{label}", catalog="{catalog}"), "{loc}", {pos})')
        self.labels[var] = label
        return var

    def fca_reagent_tips(self) -> str:
        """The FCA tip box for reagent dispensing; 8 tips per distribution, a new box every 12."""
        if self.fca_tip_uses % 12 == 0:
            self.fca_boxes.append(self.fca_box(f"FcaReagentTips{len(self.fca_boxes) + 1}"))
        self.fca_tip_uses += 1
        return self.fca_boxes[-1]

    def trough(self, label: str, *, large: bool) -> str:
        label = _label(label)
        """An MCA-compatible SBS reservoir: large ones on a 7 mm nest, small on a plate nest."""
        if large and self.deck.free_large_sites:
            loc, pos = self.deck.free_large_sites.pop(0)
            var = self.var(label)
            self.placements.append(
                f'    {var} = wt.place(Trough25mL("{label}", catalog="{self.deck.reservoir_large}"), "{loc}", {pos})'
            )
            return var
        return self._put(label, f'Trough100mL("{label}", catalog="{self.deck.reservoir_small}")')


# What a skeleton 96-well plate can hold during a clean-up (sample + beads).
# A per-well term that is the analyte marker in the simulator and 0 on the
# instrument: expressions stay exact on the bench and consistent in simulation.
_SIM_ANALYTE = "SIM_ANALYTE_UL"

_PLATE_WORKING_UL = 180.0
# Most a slim 25 ml trough is filled with before the 100 ml one is used.
_SLIM_TROUGH_FILL_UL = 22000.0
# Most one MCA reservoir ("60ml SBS MCA96") is filled with; more need opens another.
_RESERVOIR_FILL_UL = 55000.0
# ... and a "300ml SBS" on a 7 mm nest.
_LARGE_RESERVOIR_FILL_UL = 250000.0
# What one pool well receives at most (12 columns into one).
_POOL_WELL_UL = 300.0

# Deck incubations up to this are room temperature (a wait); warmer ones need a device.
_ROOM_TEMP_MAX_C = 30.0
# Most a skeleton 96-well plate well may hold at any point.
_PLATE_MAX_UL = 330.0


class DeckMismatch(ValueError):
    """The spec cannot run on this deck as written (e.g. deep-well volumes)."""


class OpenValues(ValueError):
    """The spec leaves numbers open that the skeleton must not guess."""

    def __init__(self, questions: list[str]) -> None:
        self.questions = questions
        super().__init__("skeleton: the spec leaves values open: " + " ".join(questions))


_ROLE_FOR_SIM = {"sample": "analyte", "bead_carrier": "bead_carrier", "eluent": "eluent"}


def _supply(reagent: SpecReagent) -> float | None:
    if reagent.supply_ul is None:
        return None
    return float(reagent.supply_ul) * float(reagent.supply_count or 1)


def _is_lab_stock(reagent: SpecReagent) -> bool:
    return reagent.supply_ul is None


def _pick(spec: BenchSpec, role: str, *, lab_stock: bool | None = None) -> SpecReagent | None:
    for reagent in spec.reagents:
        if reagent.role != role:
            continue
        if lab_stock is None or _is_lab_stock(reagent) == lab_stock:
            return reagent
    return None


def _is_cleanup(step: SpecStep, spec: BenchSpec) -> bool:
    # Only the explicit macro. Beads added with ``add`` or described in a
    # ``custom`` step are not a SPRI clean-up (a streptavidin bead wash keeps
    # the beads and never elutes).
    return step.op == "bead_cleanup"


def _starts_empty(spec: BenchSpec) -> bool:
    """No sample reagent and the first deck liquid step adds a reagent: the
    protocol builds its wells from reagents (e.g. beads), so the plate starts empty."""
    if spec.starts_empty is not None:
        return spec.starts_empty
    if any(r.role == "sample" for r in spec.reagents):
        return False
    first = next((s for s in spec.steps if s.location == "deck"
                  and s.op not in {"incubate", "measure", "manual"}), None)
    return first is not None and first.op == "add" and first.reagent is not None


_STEP_MARK = "#@step "


def _repeat_groups(spec: BenchSpec) -> list[dict[str, Any]]:
    """The spec's ``repeat`` steps before unrolling: which steps, how often in total."""
    groups: list[dict[str, Any]] = []
    ids: list[str] = []
    for step in spec.steps:
        if step.op != "repeat":
            ids.append(step.id)
            continue
        if step.first_step in ids and step.last_step in ids and step.times and step.times >= 1                 and ids.index(step.first_step) <= ids.index(step.last_step):
            block = ids[ids.index(step.first_step): ids.index(step.last_step) + 1]
            first = next(s for s in spec.steps if s.id == block[0])
            passes = [block] + [[f"{b}-{n}" for b in block] for n in range(2, int(step.times) + 2)]
            groups.append({"id": step.id, "passes": passes, "text": " ".join((first.text or "").split())})
            ids.extend(f"{b}-{n}" for n in range(2, int(step.times) + 2) for b in block)
    return groups


_SAMPLE_WORDS = re.compile(r"(?i)\b(dna|rna|nucleic acids?|template|amplicons?|lysates?|samples?|specimens?)\b")


def _looks_like_samples(reagent: SpecReagent) -> bool:
    """Sample material by its name (the DNA to bind, a lysate), not a kit reagent."""
    return bool(_SAMPLE_WORDS.search(f"{reagent.id} {reagent.name}"))


def _unnumbered(text: str) -> str:
    """A step name without its pass number ("Discard (wash 2 of 3)" -> "Discard")."""
    text = re.sub(r"\s*\([^()]*\d[^()]*\)", "", text)
    text = re.sub(r"(?i)\b(wash|round|pass|cycle)\s+\d+\b(\s*(of|/)\s*\d+)?", r"\1", text)
    return " ".join(text.split())


def _step_signature(step: SpecStep) -> tuple:
    """What a step does, without its id and wording."""
    return (step.op, step.location, step.reagent, step.volume_ul, step.ratio, step.washes, step.wash_ul,
            step.elute_ul, tuple(step.temp_c), tuple(step.minutes), step.residual_ul, step.cycles,
            step.engage, getattr(step, "head", None))


def _implicit_repeats(steps: list[SpecStep], taken: set[str]) -> list[dict[str, Any]]:
    """Repetitions the document wrote out step by step ("wash 1 … wash 2 …"):
    consecutive runs of the same steps. Longest runs first; steps of an
    explicit repeat are left alone. The code check in _loop_repeats decides
    whether a run really becomes a loop."""
    groups: list[dict[str, Any]] = []
    sig = [_step_signature(s) for s in steps]
    i = 0
    while i < len(steps):
        best = None
        for length in range(min(8, (len(steps) - i) // 2), 0, -1):
            count = 1
            while i + (count + 1) * length <= len(steps) and \
                    sig[i + count * length: i + (count + 1) * length] == sig[i: i + length]:
                count += 1
            window = [s.id for s in steps[i: i + count * length]]
            if count >= 2 and not taken.intersection(window) and \
                    any(s.op in {"add", "remove", "mix", "transfer"} for s in steps[i: i + length]):
                if best is None or count * length > best[0] * best[1]:
                    best = (count, length)
        if best is None:
            i += 1
            continue
        count, length = best
        # A pass reads best starting with its addition ("add, mix, magnet,
        # remove"), not mid-way ("magnet, remove, …, add, mix"): shift the
        # start onto an add when the run still repeats from there.
        for shift in range(1, length):
            j = i + shift
            if steps[j].op != "add":
                continue
            again = 1
            while j + (again + 1) * length <= len(steps) and \
                    sig[j + again * length: j + (again + 1) * length] == sig[j: j + length]:
                again += 1
            if again >= 2:
                i, count = j, again
            break
        passes = [[s.id for s in steps[i + k * length: i + (k + 1) * length]] for k in range(count)]
        groups.append({"id": f"repeat_{passes[0][0]}", "passes": passes,
                       "text": " ".join((steps[i].text or "").split())})
        i += count * length
    return groups


def _segment(body: list[str], first_id: str, end_id: str | None) -> tuple[int, int] | None:
    marks = {line[len(_STEP_MARK):]: i for i, line in enumerate(body) if line.startswith(_STEP_MARK)}
    if first_id not in marks:
        return None
    start = marks[first_id]
    if end_id is None:
        return start, len(body)
    return (start, marks[end_id]) if end_id in marks else None


def _loop_repeats(body: list[str], repeats: list[dict[str, Any]], notes: list[str],
                  vols: dict[str, Any] | None = None) -> dict[str, int]:
    """Turn unrolled repetitions into native FluentControl loops.

    Repetitions whose code is identical (apart from their step ids) leave the
    wells in the same state every pass, so they become one
    ``with wt.loop(times=<VARIABLE>)`` and the count stays editable in
    FluentControl. Passes that differ (fresh tips, an operator step, a
    different volume) stay unrolled. Returns the loop-count variables.
    """
    loop_vars: dict[str, int] = {}
    for group in repeats:
        passes = group["passes"]
        block, total = passes[0], len(passes)

        def normal(text: str, n: int) -> str:
            # One pass over every spelling of every step id of pass n (step id,
            # its variable-name form), so no replacement is replaced again.
            forms: dict[str, str] = {}
            for i, c in enumerate(passes[n - 1]):
                forms.setdefault(_ident(c).upper(), f"@{i}@")
                forms.setdefault(c, f"@{i}@")
                forms.setdefault(_ident(c), f"@{i}@")
            pattern = re.compile("|".join(re.escape(k) for k in sorted(forms, key=len, reverse=True)))
            text = pattern.sub(lambda m: forms[m.group(0)], text)
            # Group names are wording ("wash 1", "wash 2 of 3"), not behaviour.
            return re.sub(r'name="[^"]*"', 'name=@name@', text)

        marks = [line[len(_STEP_MARK):] for line in body if line.startswith(_STEP_MARK)]
        last = passes[-1][-1]
        after = marks[marks.index(last) + 1] if last in marks and marks.index(last) + 1 < len(marks) else None
        spans = []
        for n in range(1, total + 1):
            end = passes[n][0] if n < total else after
            span = _segment(body, passes[n - 1][0], end)
            if span is None:
                spans = []
                break
            spans.append(span)
        if not spans:
            continue
        texts = ["\n".join(body[a:b]) for a, b in spans]
        if any("offdeck_step(" in t or "wt.place(" in t for t in texts):
            continue   # an operator step or a labware swap: not the same every pass
        if not any(line.strip() and not line.startswith(_STEP_MARK) for t in texts for line in t.split("\n")):
            continue   # no deck code (e.g. repeats the operator does after pooling): nothing to loop
        def with_definitions(text: str) -> str:
            # The same variable names can hide different formulas (the first
            # wash removes sample + buffer, the next ones buffer + residual):
            # a pass is only the same if what its variables stand for is too.
            used = [name for name in (vols or {}) if re.search(rf"\b{re.escape(name)}\b", text)]
            return text + "".join(f"\n{name} = {vols[name]}" for name in sorted(used))

        norm = [normal(with_definitions(t), n) for n, t in enumerate(texts, start=1)]
        if total < 2 or len(set(norm[1:])) != 1:
            continue
        first = 0 if norm[0] == norm[1] else 1
        count = total - first
        if count < 2:
            continue
        kept = texts[first]
        for i, b in enumerate(block):   # "wash1-2: Add ..." reads "wash1: Add ..." inside the loop
            kept = kept.replace(f'"{passes[first][i]}: ', f'"{b}: ')
        # "(wash 2)", "(wash 2 of 3)" in a name is wrong on every other pass.
        kept = re.sub(r'name="([^"]*)"', lambda m: f'name="{_unnumbered(m.group(1))}"', kept)
        variable = f"{_ident(group['id']).upper()}_TIMES"
        # The count is the variable (editable in FluentControl), not the name.
        label = json.dumps(f"Repeat: {_unnumbered(group['text']) or block[0]}"[:60])
        lines = [f'    with wt.loop(times="{variable}", name={label}):']
        for line in kept.split("\n"):
            lines.append(line if line.startswith(_STEP_MARK) or not line.strip() else "    " + line)
        a, b = spans[first][0], spans[-1][1]
        body[a:b] = lines
        loop_vars[variable] = count
        # Notes about the copies now inside the loop repeat the first pass's.
        copies = {f"{c}:" for p in passes[first + 1:] for c in p}
        notes[:] = [note for note in notes if not any(note.startswith(c) for c in copies)]
        notes.append(f"{group['id']}: {len(block)} step(s) run {count} times as a FluentControl loop "
                     f"(count: {variable})")
    return loop_vars


def _drop_unused_volumes(vols: dict[str, Any], w: Any) -> None:
    """Volume variables only an unrolled copy used: gone with the copy."""
    code = "\n".join([*w.body, *w.fills, *w.placements])
    changed = True
    while changed:
        changed = False
        text = code + "\n" + "\n".join(str(v) for v in vols.values())
        for name in list(vols):
            if name == "SAMPLE_UL":
                continue
            if not re.search(rf"\b{re.escape(name)}\b", text.replace(f"{name} = ", "")):
                del vols[name]
                changed = True
                break


SAMPLE_SHEET_QUESTION = (
    "Normalisation needs each sample's concentration: attach a sample sheet (CSV with the well or "
    "sample number and ng/µl) or give them here, e.g. \"A1 45, A2 30.5, B1 12\"."
)


def build_skeleton(spec: BenchSpec, deck: _Deck, *, sample_sheet: dict[str, float] | None = None) -> str:
    """Python source for a first, runnable protocol draft.

    ``sample_sheet`` (well -> ng/µl) drives a ``normalize`` step's per-well
    volumes. Raises :class:`OpenValues` when the spec leaves a number open that
    the physics needs, and :class:`DeckMismatch` when the volumes do not fit.
    """
    from .sample_sheet import normalisation

    repeats = _repeat_groups(spec)
    spec, repeat_problems = expand_repeats(spec)
    # Repetitions written out step by step ("wash 1", "wash 2") loop too.
    repeats += _implicit_repeats(spec.steps, {c for g in repeats for p in g["passes"] for c in p})
    questions = [p.message for p in repeat_problems] + [p.message for p in open_values(spec)]
    # Per-sample normalisation: the volumes come from the sample sheet, not the model.
    norm_step = next((s for s in spec.steps if s.op == "normalize" and s.location == "deck"), None)
    norm_plan = None
    if norm_step is not None:
        if not sample_sheet:
            questions.append(SAMPLE_SHEET_QUESTION)
        elif not norm_step.target_ng or not norm_step.volume_ul:
            questions.append(f"“{norm_step.text[:70]}”: how many ng per sample, in how many µl?")
        else:
            norm_plan = normalisation(sample_sheet, float(norm_step.target_ng), float(norm_step.volume_ul))
            questions += norm_plan.problems
    # Partial plate: the samples fill the first columns; MCA96 steps pipette
    # whole columns, so every well of a used column is addressed.
    sample_n = max(1, min(int(spec.sample_count or 96), 96))
    if sample_sheet:
        # The sheet says which wells hold samples (column-major, as FluentControl counts).
        sample_n = max((int(w[1:]) - 1) * 8 + "ABCDEFGH".index(w[0]) + 1 for w in sample_sheet)
    used_columns = -(-sample_n // 8)
    n = 8 * used_columns
    column_list = list(range(1, used_columns + 1))
    cols_arg = "" if used_columns == 12 else f", columns={column_list}"
    # A kit reagent the deck steps draw beyond its supply. After a pool step
    # an addition goes into the one pool, not into every sample well.
    pool_at = next((i for i, st in enumerate(spec.steps) if st.op == "pool"), len(spec.steps))
    for reagent in spec.reagents:
        supply = _supply(reagent)
        if supply is None:
            continue
        drawn = sum(float(st.volume_ul) * (n if i < pool_at else 1) for i, st in enumerate(spec.steps)
                    if st.location == "deck" and st.op == "add" and st.reagent == reagent.id and st.volume_ul)
        if drawn > supply:
            questions.append(
                f"{reagent.id} is added at {drawn:g} µl for {n} wells but the kit supplies {supply:g} µl: "
                f"more vials, fewer wells, or less per well?"
            )
    if questions:
        raise OpenValues(questions)
    w = _Writer(deck=deck)
    if sample_n < 96:
        blanks = n - sample_n
        w.notes.append(
            f"{sample_n} samples in columns 1-{used_columns}; MCA96 steps pipette whole columns"
            + (f" ({blanks} unused well(s) of column {used_columns} are modelled as blank liquid)" if blanks else "")
        )
    lc = deck.liquid_class
    # Liquid classes as FluentControl string variables (the request asked):
    # LC_<reagent> for additions, LC_SAMPLE for sample moves, LC_MIX for
    # mixing (a mix needs a class with a Mix section, so it defaults to
    # "Water Mix"). Otherwise literals, as before.
    lc_vars: dict[str, str] = {}

    def lc_for(kind: str, reagent: SpecReagent | None = None) -> str:
        if not spec.liquid_class_variables:
            return json.dumps(lc)
        name = {"sample": "LC_SAMPLE", "mix": "LC_MIX"}.get(kind) or f"LC_{_ident(reagent.id).upper()}"
        lc_vars.setdefault(name, "Water Mix" if kind == "mix" else lc)
        return json.dumps(name)
    reagent_vars: dict[str, str] = {}
    # Reagent id -> its reservoirs (a new one whenever the current one would
    # exceed _RESERVOIR_FILL_UL); reservoir variable -> what it must hold.
    trough_vars: dict[str, list[str]] = {}
    fill_estimate: dict[str, float] = {}
    large_troughs: set[str] = set()
    reagent_of: dict[str, SpecReagent] = {}
    assumed_reagents: dict[str, SpecReagent] = {}
    shared_tips: dict[str, str] = {}
    # Eluate tips of the last clean-up and the plate they served: on the MCA96
    # channel i only ever meets sample i, so they can be the next clean-up's
    # sample tips on that plate.
    carry: tuple[str, str] | None = None

    def assumed(role: str, reagent_id: str, name: str, liquid_type: str | None = None) -> SpecReagent:
        if reagent_id not in assumed_reagents:
            assumed_reagents[reagent_id] = SpecReagent(reagent_id, name, role=role, liquid_type=liquid_type)
            w.notes.append(f"the spec names no {role} reagent; {name} is ASSUMED (lab stock)")
        return assumed_reagents[reagent_id]

    def reagent_var(reagent: SpecReagent) -> str:
        if reagent.id not in reagent_vars:
            var = w.var(f"r_{reagent.id}")
            role = _ROLE_FOR_SIM.get(reagent.role)
            role_arg = f', role="{role}"' if role else ""
            name = reagent.name if _is_lab_stock(reagent) else f"{reagent.name} ({reagent.id})"
            w.fills.append(f'    {var} = Reagent({json.dumps(name)}{role_arg})')
            reagent_vars[reagent.id] = var
        return reagent_vars[reagent.id]

    def fca_trough_for(reagent: SpecReagent, need_ul: float) -> str:
        """Slim trough for a reagent the FCA dispenses (one per reagent)."""
        key = f"fca:{reagent.id}"
        troughs = trough_vars.setdefault(key, [])
        reagent_of[key] = reagent
        # A 25 ml trough chosen for the first use can overflow on later ones:
        # then the reagent gets another trough (the 100 ml one holds far more).
        full = bool(troughs) and w.catalog_of.get(troughs[-1]) == deck.slim_small \
            and fill_estimate[troughs[-1]] + need_ul > _SLIM_TROUGH_FILL_UL
        if not troughs or full:
            troughs.append(w.slim_trough(_label(f"{reagent.id}_trough"), need_ul))
            fill_estimate[troughs[-1]] = 0.0
        fill_estimate[troughs[-1]] += need_ul
        return troughs[-1]

    def trough_for(reagent: SpecReagent, need_ul: float) -> str:
        reagent_of[reagent.id] = reagent
        troughs = trough_vars.setdefault(reagent.id, [])
        large = (reagent.liquid_type or "") == "ethanol" or reagent.role == "wash"
        cap = _LARGE_RESERVOIR_FILL_UL if troughs and troughs[-1] in large_troughs else _RESERVOIR_FILL_UL
        if not troughs or fill_estimate[troughs[-1]] + need_ul > cap:
            use_large = large and bool(deck.free_large_sites)
            troughs.append(w.trough(_label(f"{reagent.id}_trough"), large=use_large))
            if use_large:
                large_troughs.add(troughs[-1])
            fill_estimate[troughs[-1]] = 0.0
        fill_estimate[troughs[-1]] += need_ul
        return troughs[-1]

    # Samples.
    sample_reagent = _pick(spec, "sample")
    empty_start = _starts_empty(spec)
    samples = w.plate("Work" if empty_start else "Samples")
    if empty_start:
        sample_ul = 0.0
        w.notes.append("the protocol starts from reagents; the working plate starts empty")
    elif spec.sample_volume_ul:
        sample_ul = float(spec.sample_volume_ul)
    elif norm_plan is not None:
        # Enough for the largest draw of the normalisation, plus a dead volume.
        draws = [*norm_plan.sample_ul.values(), *(s for s, _ in norm_plan.predilute.values())]
        sample_ul = round(max(draws, default=10.0) + 5.0, 1)
        w.notes.append(f"no sample volume in the spec; {sample_ul:g} ul per well is ASSUMED "
                       "(the largest normalisation draw + 5 ul)")
    else:
        # Enough for the largest volume a step takes from the samples.
        drawn = [s.volume_ul for s in spec.steps if s.op == "transfer" and s.volume_ul]
        sample_ul = round(max([10.0, *(v * 1.1 for v in drawn)]), 1)
        w.notes.append(f"no sample volume in the spec; {sample_ul:g} ul per well is ASSUMED")
    current = samples
    marker_ul = 0.0
    if empty_start:
        pass
    else:
        if sample_reagent is not None:
            analyte_var = reagent_var(sample_reagent)
            matrix_name = f"{sample_reagent.name} matrix"
        else:
            w.notes.append("no sample reagent in the spec; the sample fill is ASSUMED")
            analyte_var = 'Reagent("Sample", role="analyte")'
            matrix_name = "Sample matrix"
        # The simulator takes bound analyte out of the free liquid, so the analyte
        # is a small marker in plain sample liquid; copies of the plate (stamps)
        # then keep their volume through a clean-up too.
        marker_ul = min(2.0, sample_ul / 10)
        if sample_n == 96:
            w.fills.append(f"    {samples}.fill_all(Reagent({json.dumps(matrix_name)}), SAMPLE_UL - {marker_ul:g})")
            w.fills.append(f"    {samples}.layer_all({analyte_var}, {marker_ul:g})")
        else:
            wells = f"{samples}.first_wells({sample_n})"
            w.fills.append(f"    {samples}.fill_wells({wells}, Reagent({json.dumps(matrix_name)}), SAMPLE_UL - {marker_ul:g})")
            w.fills.append(f"    {samples}.layer_wells({wells}, {analyte_var}, {marker_ul:g})")
            if n > sample_n:
                # Simulation only: the unused wells of the last column hold nothing
                # on the bench; modelled as blank so whole-column pipetting is checked.
                w.fills.append(f"    {samples}.fill_wells({samples}.first_wells({n})[{sample_n}:], "
                               f"Reagent(\"Blank (unused well of a pipetted column)\"), SAMPLE_UL)")
    well_ul = sample_ul
    # Every per-well volume is a FluentControl variable in a variables group
    # at the top (SAMPLE_UL, S3_AXP_UL, ...); dependent volumes are variables
    # set from expressions of those, so an edit in the Python or in
    # FluentControl carries through to every removal, mix and transfer.
    vols: dict[str, float | str] = {}
    well_terms: list[str] = []
    if not empty_start:
        vols["SAMPLE_UL"] = sample_ul
        well_terms.append("SAMPLE_UL")

    def vol_name(step: SpecStep, what: str) -> str:
        name = f"{_ident(step.id).upper()}_{_ident(what).upper()}_UL"
        base, k = name, 2
        while name in vols:
            name, k = f"{base}_{k}", k + 1
        return name

    def derive(step: SpecStep, what: str, expr: str) -> str:
        """A dependent volume: a named variable set from ``expr``."""
        if expr.isidentifier():
            return expr
        name = vol_name(step, what)
        vols[name] = expr
        return name

    def well_expr(minus: float | None = None) -> str:
        terms = [t for t in well_terms if t not in {"0", "-0"}] or ["0"]
        out = terms[0]
        for term in terms[1:]:
            out += f" - {term[1:]}" if term.startswith("-") else f" + {term}"
        if minus:
            out += f" - {minus:g}"
        return out

    # The analyte marker binds to beads added to its wells and leaves the free
    # liquid (the simulator counts it as bound), and comes back with an eluent.
    free_marker_ul, bound_marker_ul = marker_ul, 0.0
    beads_in_wells = False   # bead suspension already in the working wells
    sim_analyte_ul = 0.0     # > 0 once the marker term appears in a volume
    pooled = False
    pool_desc = ""      # where the pool is and how much it holds, for the first operator step

    def fits(step: SpecStep, volume: float) -> None:
        if volume > _PLATE_MAX_UL:
            raise DeckMismatch(
                f"skeleton: {step.id} needs {volume:g} ul per well; a 96-well plate on this deck holds "
                f"about {_PLATE_MAX_UL:g} ul (deep-well protocol? the profile has no deep-well plate)"
            )

    magnet_var = waste_var = None
    on_magnet = False

    def ensure_magnet() -> str:
        nonlocal magnet_var
        if magnet_var is None:
            if deck.magnet is None:
                raise ValueError("skeleton: the spec separates on a magnet but the profile has no magnet")
            cat, loc, pos = deck.magnet
            magnet_var = w.var("magnet")
            w.placements.append(f'    {magnet_var} = wt.place(MagnetRack("Magnet", catalog="{cat}"), "{loc}", {pos})')
        return magnet_var

    def ensure_waste() -> str:
        nonlocal waste_var
        if waste_var is None:
            if deck.waste is None:
                raise ValueError("skeleton: the profile has no waste reservoir")
            cat, loc, pos = deck.waste
            waste_var = w.var("waste")
            w.placements.append(f'    {waste_var} = wt.place(Trough25mL("Waste", catalog="{cat}"), "{loc}", {pos})')
        return waste_var

    def ensure_magnet_and_waste() -> tuple[str, str]:
        return ensure_magnet(), ensure_waste()

    def plate_tips(plate: str) -> str:
        """The working plate's sample tip box (MCA96: channel i only ever meets well i)."""
        nonlocal carry
        if carry and carry[1] == plate:
            return carry[0]
        tips = w.mca_box(f"{w.labels.get(plate, plate)}_SampleTips")
        carry = (tips, plate)
        return tips

    def release_current(label: str) -> None:
        nonlocal on_magnet
        if on_magnet:
            loc, pos = w.positions[current]
            w.body.append(f'    release(wt, plate={current}, to=("{loc}", {pos}), name={label})')
            on_magnet = False

    def ensure_handoff() -> tuple[str, int]:
        if w.handoff is None:
            w.handoff = w.nest()
            w.notes.append(f"hand-off position for operator steps: {w.handoff}")
        return w.handoff

    pending_offdeck: list[SpecStep] = []

    def flush_offdeck(*, final: bool) -> None:
        if not pending_offdeck:
            return
        text = " Then: ".join(" ".join(s.text.split()) for s in pending_offdeck)
        name = "Operator: " + ", ".join(s.id for s in pending_offdeck)
        if final:
            w.body.append(f"    offdeck_step(wt, {json.dumps(text)}, name={json.dumps(name)})")
        else:
            release_current(json.dumps(f"{name}: plate off the magnet"))
            loc, pos = ensure_handoff()
            w.body.append(
                f"    offdeck_step(wt, {json.dumps(text)}, labware={current}, "
                f'handoff=("{loc}", {pos}), name={json.dumps(name)})'
            )
        pending_offdeck.clear()

    for index, step in enumerate(spec.steps):
        w.body.append(f"{_STEP_MARK}{step.id}")      # segment marker, removed below
        if step.op == "separate" and deck.magnet is not None and step.location != "deck":
            # A magnet is a deck device here: documents written for a hand-held
            # magnet (DynaMag) still separate on the deck's magnet.
            w.notes.append(f"{step.id}: separation runs on the deck magnet")
            step = replace(step, location="deck")
        if step.op == "incubate" and step.location == "deck" and any(t > _ROOM_TEMP_MAX_C for t in step.temp_c):
            # The deck has no heater: a warm incubation is an operator step.
            w.notes.append(f"{step.id}: {max(step.temp_c):g} C incubation handed to the operator")
            step = replace(step, location="off_deck")
        if pooled:
            # After pooling, the work is on one pool (often hundreds of µl plus
            # as much bead suspension and ml of ethanol: a tube, a tube magnet,
            # a rotator). The plate blocks do not apply, and model-written code
            # for it took most of an hour and was wrong: the operator does it,
            # in one hand-off, told where the pool is and how much it holds.
            if pool_desc:
                step = replace(step, text=f"{pool_desc} {step.text}")
                pool_desc = ""
            if step.location == "deck":
                w.notes.append(f"{step.id} ({step.op}) is done by the operator on the pool")
            pending_offdeck.append(step)
            continue
        if step.location != "deck" or step.op in {"measure", "manual"}:
            pending_offdeck.append(step)
            continue
        flush_offdeck(final=False)
        label = json.dumps(f"{step.id}: {' '.join(step.text.split())[:50]}")
        reagent = next((r for r in spec.reagents if r.id == step.reagent), None)
        if step.op == "transfer" and well_ul <= 1.0:
            # "Transfer 25 µl of bead suspension to each well" into a plate that
            # is still empty is an addition of that liquid, not a transfer of
            # well contents (which would be "everything minus 1 µl" = negative).
            liquid = reagent or (_pick(spec, "bead_carrier") if re.search(r"bead", step.text, re.I) else None)
            if liquid is None:
                raise OpenValues([f"“{' '.join(step.text.split())[:70]}” transfers out of wells that are still "
                                  "empty: which liquid is added here, and how many µl per well?"])
            w.notes.append(f"{step.id}: the wells are still empty, so this transfer adds {liquid.id}")
            step = replace(step, op="add", reagent=liquid.id)
            reagent = liquid

        if step.op == "separate":
            if step.engage is False:
                if not on_magnet:
                    w.notes.append(f"{step.id}: magnet off, but the plate is not on the magnet; skipped")
                    continue
                release_current(label)
                continue
            if on_magnet:
                w.notes.append(f"{step.id}: magnet on, but the plate is already on the magnet; skipped")
                continue
            magnet = ensure_magnet()
            settle = int(step.minutes[0] * 60) if step.minutes else 120
            assumed_settle = "" if step.minutes else "  # ASSUMED: settle time"
            w.body.append(f"    separate(wt, plate={current}, magnet={magnet}, settle_seconds={settle}, "
                          f"name={label}){assumed_settle}")
            on_magnet = True
            continue

        if step.op == "remove":
            waste = ensure_waste()
            residual = step.residual_ul if step.residual_ul is not None else 2.0
            vol = step.volume_ul if step.volume_ul is not None else well_ul - residual
            if vol > well_ul:
                w.notes.append(f"{step.id}: removing {vol:g} ul but the wells hold {well_ul:g} ul; removing {well_ul:g} ul")
                vol = well_ul
            if vol <= 0:
                w.notes.append(f"{step.id}: nothing to remove ({well_ul:g} ul in the wells); skipped")
                continue
            if not on_magnet and any(s.op == "separate" for s in spec.steps):
                w.notes.append(f"{step.id}: removing liquid off the magnet takes suspended beads along")
            comment = "  # ASSUMED: residual" if step.volume_ul is None and step.residual_ul is None else ""
            if step.volume_ul is not None and step.volume_ul <= well_ul:
                vol_text = vol_name(step, "remove")
                vols[vol_text] = vol
                well_terms.append(f"-{vol_text}")
            elif step.volume_ul is not None:      # more than the wells hold: all of it
                vol_text = derive(step, "remove", well_expr())
                well_terms[:] = []
            else:                                 # all but the residual
                vol_text = derive(step, "remove", well_expr(residual))
                well_terms[:] = [f"{residual:g}"]
            w.body.append(
                f"    remove_liquid(wt, plate={current}, waste={waste}, volume_ul={vol_text}, tips={plate_tips(current)},\n"
                f"                  liquid_class={lc_for('sample')}, name={label}{cols_arg}){comment}"
            )
            well_ul -= vol
            continue

        if step.op == "mix" and not (reagent is not None and reagent.role == "per_sample"):
            if well_ul <= 0:
                w.notes.append(f"{step.id}: nothing to mix; skipped")
                continue
            cycles = step.cycles if step.cycles is not None else 10
            vol = step.volume_ul if step.volume_ul is not None else round(0.8 * well_ul, 1)
            if step.volume_ul is not None:
                vol_text = vol_name(step, "mix")
                vols[vol_text] = vol
            else:
                terms = well_expr()
                vol_text = derive(step, "mix", f"0.8 * {terms}" if terms.isidentifier() else f"0.8 * ({terms})")
            comment = "  # ASSUMED: cycles" if step.cycles is None else ""
            w.body.append(
                f"    mix_wells(wt, plate={current}, tips={plate_tips(current)}, volume_ul={vol_text}, "
                f"cycles={cycles}{', liquid_class=' + lc_for('mix') if spec.liquid_class_variables else ''}, "
                f"name={label}{cols_arg}){comment}"
            )
            continue

        if _is_cleanup(step, spec):
            if on_magnet:
                release_current(json.dumps(f"{step.id}: magnet off before the clean-up"))
            magnet, waste = ensure_magnet_and_waste()
            beads = reagent if reagent and reagent.role == "bead_carrier" else _pick(spec, "bead_carrier")
            lab = _is_lab_stock(beads) if beads else True
            wash = _pick(spec, "wash")
            eluent = _pick(spec, "eluent", lab_stock=lab) or _pick(spec, "eluent")
            bead_given = step.ratio is None and step.volume_ul is not None
            if bead_given and float(step.volume_ul) + well_ul > _PLATE_WORKING_UL:
                w.notes.append(f"{step.id}: {step.volume_ul:g} ul beads do not fit a 96-well plate with "
                               f"{well_ul:g} ul sample (deep-well protocol?); a 1.8x ratio is ASSUMED")
                bead_given = False
            ratio = step.ratio if step.ratio is not None else 1.8
            washes = step.washes if step.washes is not None else 2
            wash_ul = step.wash_ul if step.wash_ul is not None and step.wash_ul <= 190 else 150.0
            elute_ul = step.elute_ul if step.elute_ul is not None else 15.0
            guessed = [k for k, v in (("ratio", step.ratio if not bead_given else step.volume_ul),
                                      ("washes", step.washes), ("elute_ul", step.elute_ul)) if v is None]
            beads = beads or assumed("bead_carrier", "ASSUMED_BEADS", "SPRI beads")
            wash = wash or assumed("wash", "ASSUMED_ETOH", "80% ethanol", "ethanol")
            eluent = eluent or assumed("eluent", "ASSUMED_EB", "Elution buffer")
            bead_ul = float(step.volume_ul) if bead_given else ratio * well_ul
            fits(step, well_ul + bead_ul)
            bead_arg = f"bead_volume_ul={bead_ul:g}" if bead_given else f"bead_ratio={ratio:g}"
            # Beads and elution buffer: FCA from slim troughs; ethanol: MCA.
            bead_trough = fca_trough_for(beads, bead_ul * n * 1.1 + 2000)
            wash_trough = trough_for(wash, wash_ul * washes * n * 1.1 + 2000)
            eluent_trough = fca_trough_for(eluent, elute_ul * n * 1.1 + 2000)
            fca_tips = w.fca_reagent_tips()
            w.fca_tip_uses += 1  # beads and elution buffer: two distributions
            eluate = w.plate(f"{step.id}_Eluate")
            if "reagent" not in shared_tips:
                shared_tips["reagent"] = w.mca_box("ReagentTips")
            sample_tips = carry[0] if carry and carry[1] == current else w.mca_box(f"{step.id}_SampleTips")
            tips = [shared_tips["reagent"], sample_tips, w.mca_box(f"{step.id}_EluateTips")]
            carry = (tips[2], eluate)
            comment = f"  # ASSUMED: {', '.join(guessed)}" if guessed else ""
            w.body.append(
                f"    spri_cleanup(\n"
                f"        wt, sample_plate={current}, magnet={magnet}, bead_source={bead_trough},\n"
                f"        wash_source={wash_trough}, elution_source={eluent_trough}, waste={waste},\n"
                f"        eluate_plate={eluate}, reagent_tips={tips[0]}, sample_tips={tips[1]},\n"
                f"        eluate_tips={tips[2]}, sample_volume_ul={well_expr()}, {bead_arg},\n"
                f"        elution_volume_ul={elute_ul:g}, wash_volume_ul={wash_ul:g}, wash_count={washes},\n"
                f"        liquid_class={lc_for('sample')}, fca_tips={fca_tips}, name={label}{cols_arg},\n"
                f"    ){comment}"
            )
            w.retire(current, sample_tips)
            current, well_ul = eluate, elute_ul - 2.0
            well_terms[:] = [f"{well_ul:g}"]
            continue

        if step.op == "normalize" and norm_plan is not None:
            # Per-well volumes from the sample sheet: diluent by the FCA into a
            # new plate, then each sample's volume (fresh tips per column);
            # samples too concentrated for a direct draw via a pre-dilution.
            diluent = reagent or _pick(spec, "reagent")
            if diluent is None:
                raise OpenValues([f"“{step.text[:70]}”: which diluent (water, EB)?"])
            final = float(step.volume_ul)
            dest = w.plate(_label(f"{step.id}_Plate"))
            need = sum(norm_plan.diluent_ul.values()) + sum(d for _, d in norm_plan.predilute.values())
            trough = fca_trough_for(diluent, need * 1.1 + 2000)
            w.notes.extend(f"{step.id}: {note}" for note in norm_plan.notes)

            def per_well(values: dict[str, float]) -> str:
                return "{" + ", ".join(f'"{k}": {v:g}' for k, v in values.items()) + "}"

            if norm_plan.predilute:
                inter = w.plate(_label(f"{step.id}_Predilution"))
                w.body.append(
                    f"    distribute_volumes(wt, source={trough}, plate={inter}, "
                    f"volumes={per_well({k: d for k, (_, d) in norm_plan.predilute.items()})},\n"
                    f"                       tips={w.fca_reagent_tips()}, liquid_class={lc_for('add', diluent)}, "
                    f"name={json.dumps(f'{step.id}: pre-dilute concentrated samples (diluent)')})")
                w.body.append(
                    f"    transfer_volumes(wt, source={current}, dest={inter}, "
                    f"volumes={per_well({k: s for k, (s, _) in norm_plan.predilute.items()})},\n"
                    f"                     tips={w.fca_box(f'{step.id}_PreTips')}, liquid_class={lc_for('sample')}, "
                    f"name={json.dumps(f'{step.id}: pre-dilute concentrated samples (sample)')})")
            if norm_plan.diluent_ul:
                w.body.append(
                    f"    distribute_volumes(wt, source={trough}, plate={dest}, volumes={per_well(norm_plan.diluent_ul)},\n"
                    f"                       tips={w.fca_reagent_tips()}, liquid_class={lc_for('add', diluent)}, "
                    f"name={json.dumps(f'{step.id}: diluent per sample')})")
            if norm_plan.sample_ul:
                w.body.append(
                    f"    transfer_volumes(wt, source={current}, dest={dest}, volumes={per_well(norm_plan.sample_ul)},\n"
                    f"                     tips={w.fca_box(f'{step.id}_Tips')}, liquid_class={lc_for('sample')}, "
                    f"name={json.dumps(f'{step.id}: sample per well (' + ' '.join(step.text.split())[:40] + ')')})")
            if norm_plan.predilute:
                w.body.append(
                    f"    transfer_volumes(wt, source={inter}, dest={dest}, "
                    f"volumes={per_well({k: final for k in norm_plan.predilute})},\n"
                    f"                     tips={w.fca_box(f'{step.id}_DilTips')}, liquid_class={lc_for('sample')}, "
                    f"name={json.dumps(f'{step.id}: {final:g} ul of each pre-dilution')})")
            w.retire(current)
            current, well_ul = dest, final
            well_terms[:] = [f"{final:g}"]
            continue

        if step.op == "pool":
            pool = w.plate(f"{step.id}_Pool")
            tips = w.fca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else min(10.0, well_ul)
            if vol > well_ul - 1.0:
                w.notes.append(f"{step.id}: {vol:g} ul is more than the {well_ul:g} ul in the wells; pooling {well_ul - 1.0:g} ul")
                vol = well_ul - 1.0
            if used_columns == 1:
                # The samples fit one column: pool them into one well, one channel per sample.
                if vol * sample_n > _POOL_WELL_UL:
                    w.notes.append(f"{step.id}: {sample_n} x {vol:g} ul overflows one pool well; "
                                   f"pooling {_POOL_WELL_UL / sample_n:g} ul per sample")
                    vol = _POOL_WELL_UL / sample_n
                w.body.append(
                    f"    pool_wells(wt, source={current}, dest={pool}, volume_ul={vol:g}, tips={tips},\n"
                    f"               liquid_class={lc_for('sample')}, source_wells={current}.first_wells({sample_n}), "
                    f"dest_well=\"A1\", name={label})"
                )
                w.retire(current, tips)
                current, well_ul = pool, vol * sample_n
                well_terms[:] = [f"{well_ul:g}"]
                pooled = True
                pool_desc = (f"The pool ({sample_n} samples x {vol:g} µl = {well_ul:g} µl) is in well A1 of "
                             f"{w.labels.get(pool, pool)}; continue with it in a tube.")
                continue
            if vol * used_columns > _POOL_WELL_UL:
                w.notes.append(f"{step.id}: {used_columns} x {vol:g} ul overflows one pool well; "
                               f"pooling {_POOL_WELL_UL / used_columns:g} ul per column")
                vol = _POOL_WELL_UL / used_columns
            w.body.append(
                f"    pool_columns(wt, source={current}, dest={pool}, volume_ul={vol:g}, tips={tips},\n"
                f"                 liquid_class={lc_for('sample')}, dest_column=1, name={label}{cols_arg})"
            )
            w.retire(current, tips)
            current, well_ul = pool, vol * used_columns
            well_terms[:] = [f"{well_ul:g}"]
            pooled = True
            pool_desc = (f"The samples are pooled into column 1 of {w.labels.get(pool, pool)} "
                         f"(8 wells x {well_ul:g} µl = {8 * well_ul:g} µl): combine column 1 into one tube "
                         "(the document's LoBind tube) and continue with the pool.")
            continue

        # Samples added to the working plate (DNA onto washed beads) are 96
        # different liquids: they come from a sample plate, one well each,
        # stamped 1:1 -- never one trough into every well.
        adds_samples = step.op == "add" and reagent is not None and (
            reagent.role == "sample"
            # A spec that marks no reagent as the samples but counts samples
            # on an empty-start plate: the nucleic acid it adds is the samples.
            or (empty_start and sample_reagent is None and spec.sample_count and _looks_like_samples(reagent)))
        if adds_samples and reagent.role != "sample":
            w.notes.append(f"{step.id}: {reagent.id} is the samples (one per well, from a sample plate)")
            reagent = replace(reagent, role="sample")
        if (step.op in {"add", "transfer", "mix"} and reagent is not None and reagent.role == "per_sample") \
                or adds_samples:
            source = w.plate(_label(f"{reagent.id}_Plate"))
            per_well = reagent.supply_ul if reagent.supply_ul is not None and not adds_samples \
                else (step.volume_ul or 1.0) * 2
            if adds_samples:
                # As on a sample plate: plain liquid with a small analyte marker
                # on top (the simulator takes bound analyte out of the free
                # liquid; a whole well of analyte would vanish on the beads).
                m = marker_ul or min(2.0, per_well / 10)
                wells = f"{source}.first_wells({sample_n})"
                w.fills.append(f"    {source}.fill_wells({wells}, Reagent({json.dumps(reagent.name + ' matrix')}), "
                               f"{per_well - m:g})")
                w.fills.append(f"    {source}.layer_wells({wells}, {reagent_var(reagent)}, {m:g})")
            else:
                w.fills.append(f"    {source}.fill_all({reagent_var(reagent)}, {per_well:g})")
            tips = w.mca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else 1.0
            vol_text = vol_name(step, reagent.id)
            vols[vol_text] = vol
            well_terms.append(vol_text)
            mix = ", mix_cycles=5" if re.search(r"\bmix", step.text or "", re.IGNORECASE) else ""
            w.body.append(
                f"    stamp(wt, source={source}, dest={current}, volume_ul={vol_text}, tips={tips},\n"
                f"          liquid_class={lc_for('add', reagent)}{mix}, name={label}{cols_arg})"
            )
            w.retire(source, tips)
            well_ul += vol
            if adds_samples:
                if beads_in_wells:
                    # The marker binds to the beads already there at once: it
                    # leaves the (simulated) free liquid now.
                    well_ul -= m
                    well_terms.append(f"-{_SIM_ANALYTE}")
                    sim_analyte_ul = m
                    bound_marker_ul += m
                else:
                    free_marker_ul += m
            fits(step, well_ul)
            continue

        if step.op == "add" and reagent is not None:
            vol = step.volume_ul if step.volume_ul is not None else 5.0
            vol_text = vol_name(step, reagent.id)
            vols[vol_text] = vol
            cheap = (reagent.liquid_type or "") in {"ethanol", "water"} or reagent.role == "wash"
            if step.head is not None:
                # The request names the head: it is a requirement, not a default.
                w.notes.append(f"{step.id}: {reagent.id} dispensed by the {step.head.upper()} (requested)")
                cheap = step.head == "mca"
            if cheap:
                # Cheap bulk liquid: MCA96 from an SBS reservoir, one shared box.
                trough = trough_for(reagent, vol * n * 1.15 + 500)
                if "reagent" not in shared_tips:
                    shared_tips["reagent"] = w.mca_box("ReagentTips")
                w.body.append(
                    f"    add_reagent(wt, reagent_source={trough}, plate={current}, volume_ul={vol_text},\n"
                    f"                reagent_tips={shared_tips['reagent']}, liquid_class={lc_for('add', reagent)}, name={label}{cols_arg})"
                )
            else:
                # Reagents: the FCA from a slim trough (little dead volume).
                trough = fca_trough_for(reagent, vol * n * 1.1 + 2000)
                w.body.append(
                    f"    distribute_reagent(wt, source={trough}, plate={current}, volume_ul={vol_text},\n"
                    f"                       tips={w.fca_reagent_tips()}, liquid_class={lc_for('add', reagent)}, name={label}{cols_arg})"
                )
            well_ul += vol
            well_terms.append(vol_text)
            if reagent.role == "bead_carrier":
                beads_in_wells = True
            if reagent.role == "bead_carrier" and free_marker_ul:
                well_ul -= free_marker_ul
                well_terms.append(f"-{_SIM_ANALYTE}")
                sim_analyte_ul = free_marker_ul
                free_marker_ul, bound_marker_ul = 0.0, free_marker_ul
            elif reagent.role == "eluent" and bound_marker_ul:
                well_ul += bound_marker_ul
                well_terms.append(_SIM_ANALYTE)
                free_marker_ul, bound_marker_ul = bound_marker_ul, 0.0
            fits(step, well_ul)
            continue

        if step.op == "transfer":
            dest = w.plate(f"{step.id}_Plate")
            # Sample-lineage tips: channel i only ever meets sample i.
            tips = carry[0] if carry and carry[1] == current else w.mca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else well_ul
            if step.volume_ul is not None and vol <= well_ul - 1.0:
                vol_text = vol_name(step, "transfer")
                vols[vol_text] = vol
            elif vol > well_ul - 1.0:
                w.notes.append(f"{step.id}: {vol:g} ul is more than the {well_ul:g} ul in the wells; moving {well_ul - 1.0:g} ul")
                vol = well_ul - 1.0
                vol_text = derive(step, "transfer", well_expr(1.0))
            else:
                vol_text = derive(step, "transfer", well_expr())
            fits(step, vol)
            w.body.append(
                f"    stamp(wt, source={current}, dest={dest}, volume_ul={vol_text}, tips={tips},\n"
                f"          liquid_class={lc_for('sample')}, name={label}{cols_arg})"
            )
            # The emptied plate leaves the magnet so the next separation can use it.
            release_current(json.dumps(f"{step.id}: spent plate off the magnet"))
            w.retire(current)
            carry = (tips, dest)
            current, well_ul = dest, vol
            well_terms[:] = [vol_text if vol_text.isidentifier() else f"({vol_text})"]
            continue

        if step.op == "incubate":  # room temperature (warmer ones went to the operator above)
            seconds = int(sum(step.minutes) * 60) if step.minutes else 300
            assumed_time = "" if step.minutes else ", ASSUMED: 5 min"
            w.body.append(f"    wt.group({label})")
            w.body.append(f"    wt.wait(duration_seconds={seconds})  # room temperature{assumed_time}")
            continue

        # Anything else on the deck: leave it for review.
        w.notes.append(f"{step.id} ({step.op}) has no block mapping; review it")
        w.body.append(f"    wt.group({label})")
        w.body.append(f"    wt.add_comment({json.dumps('TODO ' + step.text)})")
    flush_offdeck(final=True)

    # Fills for troughs, now that every step's need is known.
    for reagent_id, troughs in trough_vars.items():
        reagent = reagent_of[reagent_id]
        supply = _supply(reagent)
        for trough in troughs:
            need = fill_estimate[trough]
            amount = min(need, supply) if supply is not None else need
            if supply is not None:
                supply = max(0.0, supply - amount)
            note = "  # kit supply" if _supply(reagent) is not None else "  # lab stock, estimated need"
            line = f"    {trough}.fill_all({reagent_var(reagent)}, {amount:.0f}){note}"
            if trough in w.placed_in_body:
                at = next(i for i, text in enumerate(w.body) if text.startswith(f"    {trough} = wt.place("))
                w.body.insert(at + 1, line)
            else:
                w.fills.append(line)

    loop_vars = _loop_repeats(w.body, repeats, w.notes, vols)
    w.body[:] = [line for line in w.body if not line.startswith(_STEP_MARK)]
    _drop_unused_volumes(vols, w)

    if w.swaps:
        w.notes.append(f"the deck is full: {w.swaps} operator swap(s) replace spent labware mid-run")
    classes = sorted({
        cls for line in w.placements + w.body
        for cls in re.findall(r"wt\.place\((\w+)\(", line)
    } | {"Reagent", "Worktable"})
    used_blocks = sorted({b for b in ("spri_cleanup", "stamp", "add_reagent", "distribute_reagent", "pool_columns", "pool_wells",
                                      "offdeck_step", "remove_liquid", "mix_wells", "separate", "release",
                                      "distribute_volumes", "transfer_volumes")
                          if any(f"{b}(" in line for line in w.body)})
    header = [
        SKELETON_MARKER,
        '"""Skeleton generated from an approved Bench Spec and the deck profile.',
        "",
        f"Spec: {spec.title}",
        *[f"Note: {note}" for note in w.notes],
        '"""',
        "",
        f"from fluentvibe import {', '.join(classes)}",
    ]
    if used_blocks:
        header.append(f"from fluentvibe.blocks import {', '.join(used_blocks)}")
    slug = re.sub(r"[^0-9a-z]+", "_", spec.title.lower()).strip("_")[:40] or "run"
    source = [
        *header,
        "",
        "",
        "def build_worktable() -> Worktable:",
        "    wt = Worktable.from_workspace(",
        f"        {json.dumps(deck.workspace_name)},",
        f"        workspace_guid={json.dumps(deck.workspace_guid)},",
        "        auto_place=False,",
        f"        protocol_name={json.dumps(spec.title[:80])},",
        '        comment="Skeleton from Bench Spec",',
        "    )",
        f'    wt.declare_variable("RunId", "{slug}")',
        f'    wt.set_sim_value("RunId", "{slug}")',
        *[line for name, count in loop_vars.items()
          for line in (f"    wt.declare_variable({json.dumps(name)}, {count})  # how often the loop runs",
                       f"    wt.set_sim_value({json.dumps(name)}, {count})")],
        *[line for name, default in lc_vars.items()
          for line in (f"    wt.declare_variable({json.dumps(name)}, {json.dumps(default)})",
                       f"    wt.set_sim_value({json.dumps(name)}, {json.dumps(default)})")],
        *(["", '    wt.group("Variables")',
           "    # Per-well volumes (ul) as FluentControl variables. Removals, mixes and",
           "    # transfers are variables computed from these, so a change here or in",
           "    # FluentControl carries through."]
          + ([f"    # Simulation only: the analyte marker ({sim_analyte_ul:g} ul) bound to beads",
              "    # leaves the simulated free liquid; 0 on the instrument.",
              f'    {_SIM_ANALYTE} = wt.volume("{_SIM_ANALYTE}", 0)',
              f'    wt.set_sim_value("{_SIM_ANALYTE}", {sim_analyte_ul:g})'] if sim_analyte_ul else [])
          + [f"    {name} = wt.volume({json.dumps(name)}, {value if isinstance(value, str) else format(value, 'g')})"
             for name, value in vols.items()] if vols else []),
        "",
        '    wt.group("Labware Placement")',
        *w.placements,
        "",
        *w.fills,
        "",
        *w.body,
        "    return wt",
        "",
    ]
    return "\n".join(source)
