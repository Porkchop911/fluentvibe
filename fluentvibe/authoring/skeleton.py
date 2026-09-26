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

from .bench_spec import BenchSpec, SpecReagent, SpecStep, open_values

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
        if not self.deck.free_trough_sites:
            raise ValueError("skeleton: the deck has no free trough site left for an FCA reagent")
        loc, pos = self.deck.free_trough_sites.pop(0)
        var = self.var(label)
        big = need_ul > _SLIM_TROUGH_FILL_UL
        cls, catalog = ("Trough100mL", self.deck.slim_large) if big else ("Trough25mL", self.deck.slim_small)
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
_PLATE_WORKING_UL = 180.0
# Most a slim 25 ml trough is filled with before the 100 ml one is used.
_SLIM_TROUGH_FILL_UL = 22000.0
# Most one MCA reservoir ("60ml SBS MCA96") is filled with; more need opens another.
_RESERVOIR_FILL_UL = 55000.0
# ... and a "300ml SBS" on a 7 mm nest.
_LARGE_RESERVOIR_FILL_UL = 250000.0
# What one pool well receives at most (12 columns into one).
_POOL_WELL_UL = 300.0

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
    if any(r.role == "sample" for r in spec.reagents):
        return False
    first = next((s for s in spec.steps if s.location == "deck"
                  and s.op not in {"incubate", "measure", "manual"}), None)
    return first is not None and first.op == "add" and first.reagent is not None


def build_skeleton(spec: BenchSpec, deck: _Deck) -> str:
    """Python source for a first, runnable protocol draft.

    Raises :class:`OpenValues` when the spec leaves a number open that the
    physics needs, and :class:`DeckMismatch` when the volumes do not fit.
    """
    questions = [p.message for p in open_values(spec)]
    # A kit reagent the deck steps draw beyond its supply (MCA96 blocks fill all 96 wells).
    for reagent in spec.reagents:
        supply = _supply(reagent)
        if supply is None:
            continue
        drawn = sum(float(st.volume_ul) * 96 for st in spec.steps
                    if st.location == "deck" and st.op == "add" and st.reagent == reagent.id and st.volume_ul)
        if drawn > supply:
            questions.append(
                f"{reagent.id} is added at {drawn:g} µl for 96 wells but the kit supplies {supply:g} µl: "
                f"more vials, fewer wells, or less per well?"
            )
    if questions:
        raise OpenValues(questions)
    w = _Writer(deck=deck)
    # MCA96 blocks address the whole plate: every channel draws reagent.
    n = 96
    lc = deck.liquid_class
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
        if key not in trough_vars:
            trough_vars[key] = [w.slim_trough(f"{reagent.id}_trough", need_ul)]
            fill_estimate[trough_vars[key][0]] = 0.0
            reagent_of[key] = reagent
        fill_estimate[trough_vars[key][0]] += need_ul
        return trough_vars[key][0]

    def trough_for(reagent: SpecReagent, need_ul: float) -> str:
        reagent_of[reagent.id] = reagent
        troughs = trough_vars.setdefault(reagent.id, [])
        large = (reagent.liquid_type or "") == "ethanol" or reagent.role == "wash"
        cap = _LARGE_RESERVOIR_FILL_UL if troughs and troughs[-1] in large_troughs else _RESERVOIR_FILL_UL
        if not troughs or fill_estimate[troughs[-1]] + need_ul > cap:
            use_large = large and bool(deck.free_large_sites)
            troughs.append(w.trough(f"{reagent.id}_trough", large=use_large))
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
        w.fills.append(f"    {samples}.fill_all(Reagent({json.dumps(matrix_name)}), {sample_ul - marker_ul:g})")
        w.fills.append(f"    {samples}.layer_all({analyte_var}, {marker_ul:g})")
    well_ul = sample_ul
    # The analyte marker binds to beads added to its wells and leaves the free
    # liquid (the simulator counts it as bound), and comes back with an eluent.
    free_marker_ul, bound_marker_ul = marker_ul, 0.0
    pooled = False

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
        if step.op == "separate" and deck.magnet is not None and step.location != "deck":
            # A magnet is a deck device here: documents written for a hand-held
            # magnet (DynaMag) still separate on the deck's magnet.
            w.notes.append(f"{step.id}: separation runs on the deck magnet")
            step = replace(step, location="deck")
        if step.location != "deck" or step.op in {"measure", "manual"}:
            pending_offdeck.append(step)
            continue
        flush_offdeck(final=False)
        label = json.dumps(f"{step.id}: {' '.join(step.text.split())[:50]}")
        reagent = next((r for r in spec.reagents if r.id == step.reagent), None)

        if pooled and step.op != "incubate":
            # The pool plate holds 8 wells in column 1; MCA96 full-plate
            # blocks would address 96. Leave the step for LiHa authoring.
            w.notes.append(f"{step.id} ({step.op}) runs on the pooled column; author it with the LiHa")
            w.body.append(f"    wt.group({label})")
            w.body.append(f"    wt.add_comment({json.dumps('TODO (LiHa, pooled column 1) ' + step.text)})")
            continue

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
            w.body.append(
                f"    remove_liquid(wt, plate={current}, waste={waste}, volume_ul={vol:g}, tips={plate_tips(current)},\n"
                f"                  liquid_class={json.dumps(lc)}, name={label}){comment}"
            )
            well_ul -= vol
            continue

        if step.op == "mix" and not (reagent is not None and reagent.role == "per_sample"):
            if well_ul <= 0:
                w.notes.append(f"{step.id}: nothing to mix; skipped")
                continue
            cycles = step.cycles if step.cycles is not None else 10
            vol = step.volume_ul if step.volume_ul is not None else round(0.8 * well_ul, 1)
            comment = "  # ASSUMED: cycles" if step.cycles is None else ""
            w.body.append(
                f"    mix_wells(wt, plate={current}, tips={plate_tips(current)}, volume_ul={vol:g}, "
                f"cycles={cycles}, name={label}){comment}"
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
                f"        eluate_tips={tips[2]}, sample_volume_ul={well_ul:g}, {bead_arg},\n"
                f"        elution_volume_ul={elute_ul:g}, wash_volume_ul={wash_ul:g}, wash_count={washes},\n"
                f"        liquid_class={json.dumps(lc)}, fca_tips={fca_tips}, name={label},\n"
                f"    ){comment}"
            )
            w.retire(current, sample_tips)
            current, well_ul = eluate, elute_ul - 2.0
            continue

        if step.op == "pool":
            pool = w.plate(f"{step.id}_Pool")
            tips = w.fca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else min(10.0, well_ul)
            if vol > well_ul - 1.0:
                w.notes.append(f"{step.id}: {vol:g} ul is more than the {well_ul:g} ul in the wells; pooling {well_ul - 1.0:g} ul")
                vol = well_ul - 1.0
            if vol * 12 > _POOL_WELL_UL:
                w.notes.append(f"{step.id}: 12 x {vol:g} ul overflows one pool well; pooling {_POOL_WELL_UL / 12:g} ul per column")
                vol = _POOL_WELL_UL / 12
            w.body.append(
                f"    pool_columns(wt, source={current}, dest={pool}, volume_ul={vol:g}, tips={tips},\n"
                f"                 liquid_class={json.dumps(lc)}, dest_column=1, name={label})"
            )
            w.retire(current, tips)
            current, well_ul = pool, vol * 12
            pooled = True
            continue

        if step.op in {"add", "transfer", "mix"} and reagent is not None and reagent.role == "per_sample":
            source = w.plate(f"{reagent.id}_Plate")
            per_well = reagent.supply_ul if reagent.supply_ul is not None else (step.volume_ul or 1.0) * 2
            w.fills.append(f"    {source}.fill_all({reagent_var(reagent)}, {per_well:g})")
            tips = w.mca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else 1.0
            mix = ", mix_cycles=5" if re.search(r"\bmix", step.text or "", re.IGNORECASE) else ""
            w.body.append(
                f"    stamp(wt, source={source}, dest={current}, volume_ul={vol:g}, tips={tips},\n"
                f"          liquid_class={json.dumps(lc)}{mix}, name={label})"
            )
            w.retire(source, tips)
            well_ul += vol
            fits(step, well_ul)
            continue

        if step.op == "add" and reagent is not None:
            vol = step.volume_ul if step.volume_ul is not None else 5.0
            cheap = (reagent.liquid_type or "") in {"ethanol", "water"} or reagent.role == "wash"
            if cheap:
                # Cheap bulk liquid: MCA96 from an SBS reservoir, one shared box.
                trough = trough_for(reagent, vol * n * 1.15 + 500)
                if "reagent" not in shared_tips:
                    shared_tips["reagent"] = w.mca_box("ReagentTips")
                w.body.append(
                    f"    add_reagent(wt, reagent_source={trough}, plate={current}, volume_ul={vol:g},\n"
                    f"                reagent_tips={shared_tips['reagent']}, liquid_class={json.dumps(lc)}, name={label})"
                )
            else:
                # Reagents: the FCA from a slim trough (little dead volume).
                trough = fca_trough_for(reagent, vol * n * 1.1 + 2000)
                w.body.append(
                    f"    distribute_reagent(wt, source={trough}, plate={current}, volume_ul={vol:g},\n"
                    f"                       tips={w.fca_reagent_tips()}, liquid_class={json.dumps(lc)}, name={label})"
                )
            well_ul += vol
            if reagent.role == "bead_carrier" and free_marker_ul:
                well_ul -= free_marker_ul
                free_marker_ul, bound_marker_ul = 0.0, free_marker_ul
            elif reagent.role == "eluent" and bound_marker_ul:
                well_ul += bound_marker_ul
                free_marker_ul, bound_marker_ul = bound_marker_ul, 0.0
            fits(step, well_ul)
            continue

        if step.op == "transfer":
            dest = w.plate(f"{step.id}_Plate")
            # Sample-lineage tips: channel i only ever meets sample i.
            tips = carry[0] if carry and carry[1] == current else w.mca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else well_ul
            if vol > well_ul - 1.0:
                w.notes.append(f"{step.id}: {vol:g} ul is more than the {well_ul:g} ul in the wells; moving {well_ul - 1.0:g} ul")
                vol = well_ul - 1.0
            fits(step, vol)
            w.body.append(
                f"    stamp(wt, source={current}, dest={dest}, volume_ul={vol:g}, tips={tips},\n"
                f"          liquid_class={json.dumps(lc)}, name={label})"
            )
            # The emptied plate leaves the magnet so the next separation can use it.
            release_current(json.dumps(f"{step.id}: spent plate off the magnet"))
            w.retire(current)
            carry = (tips, dest)
            current, well_ul = dest, vol
            continue

        if step.op == "incubate" and not step.temp_c:
            seconds = int(sum(step.minutes) * 60) if step.minutes else 300
            w.body.append(f"    wt.group({label})")
            w.body.append(f"    wt.wait(duration_seconds={seconds})  # room temperature")
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

    if w.swaps:
        w.notes.append(f"the deck is full: {w.swaps} operator swap(s) replace spent labware mid-run")
    classes = sorted({
        cls for line in w.placements + w.body
        for cls in re.findall(r"wt\.place\((\w+)\(", line)
    } | {"Reagent", "Worktable"})
    used_blocks = sorted({b for b in ("spri_cleanup", "stamp", "add_reagent", "distribute_reagent", "pool_columns",
                                      "offdeck_step", "remove_liquid", "mix_wells", "separate", "release")
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
