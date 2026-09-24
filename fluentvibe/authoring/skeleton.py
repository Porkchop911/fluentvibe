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
* turns each spec step into a ``fluentvibe.blocks`` call (``stamp``,
  ``add_reagent``, ``pool_columns``, ``spri_cleanup``), room-temperature
  incubations into ``wt.wait``, and every stretch of off-deck/manual steps into
  one ``offdeck_step``.

Choices the spec does not fix (assumed ratios, mix counts, tip boxes) are
marked ``# ASSUMED`` so a model or a person can review them. The skeleton is a
starting draft, not a verdict: authoring still simulates, compiles and checks it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bench_spec import BenchSpec, SpecReagent, SpecStep

SKELETON_MARKER = "# BENCH SPEC SKELETON"
_PLATE_LOCATION = "Nest61mm_Pos"
_TROUGH_PREFIX = "WS_"
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
    trough_small: str = "25ml_short"
    trough_large: str = "100ml"
    liquid_class: str = _DEFAULT_LC


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

    def var(self, base: str) -> str:
        name = _ident(base)
        candidate, n = name, 2
        while candidate in self.names:
            candidate, n = f"{name}_{n}", n + 1
        self.names.add(candidate)
        return candidate

    def nest(self) -> tuple[str, int]:
        if not self.deck.free_nests:
            raise ValueError("skeleton: the deck has no free 61 mm nest left for another plate or tip box")
        return self.deck.free_nests.pop(0)

    def plate(self, label: str) -> str:
        loc, pos = self.nest()
        var = self.var(label)
        self.placements.append(
            f'    {var} = wt.place(Plate96("{label}", catalog="{self.deck.plate_catalog}"), "{loc}", {pos})'
        )
        return var

    def mca_box(self, label: str) -> str:
        if not self.deck.mca_tips:
            raise ValueError("skeleton: the profile lists no MCA96 tip box")
        loc, pos = self.nest()
        var = self.var(label)
        cls = "MCA200Box" if "200" in self.deck.mca_tips else "MCA100Box"
        self.placements.append(
            f'    {var} = wt.place({cls}("{label}", catalog="{self.deck.mca_tips}"), "{loc}", {pos})'
        )
        return var

    def fca_box(self, label: str) -> str:
        if not self.deck.fca_tips:
            raise ValueError("skeleton: the profile lists no FCA tip box")
        loc, pos = self.nest()
        var = self.var(label)
        catalog, cls = self.deck.fca_tips
        self.placements.append(f'    {var} = wt.place({cls}("{label}", catalog="{catalog}"), "{loc}", {pos})')
        return var

    def trough(self, label: str, *, large: bool) -> str:
        if not self.deck.free_trough_sites:
            raise ValueError("skeleton: the deck has no free trough site left")
        loc, pos = self.deck.free_trough_sites.pop(0)
        var = self.var(label)
        catalog = self.deck.trough_large if large else self.deck.trough_small
        cls = "Trough100mL" if large else "Trough25mL"
        self.placements.append(f'    {var} = wt.place({cls}("{label}", catalog="{catalog}"), "{loc}", {pos})')
        return var


# What a skeleton 96-well plate can hold during a clean-up (sample + beads).
_PLATE_WORKING_UL = 180.0
# What one pool well receives at most (12 columns into one).
_POOL_WELL_UL = 300.0

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
    if step.op == "bead_cleanup":
        return True
    reagent = next((r for r in spec.reagents if r.id == step.reagent), None)
    return bool(reagent and reagent.role == "bead_carrier") or bool(
        re.search(r"clean-?up|ampure|spri|bead", step.text or "", re.IGNORECASE)
        and step.op == "custom"
    )


def build_skeleton(spec: BenchSpec, deck: _Deck) -> str:
    """Python source for a first, runnable protocol draft."""
    w = _Writer(deck=deck)
    # MCA96 blocks address the whole plate: every channel draws reagent.
    n = 96
    lc = deck.liquid_class
    reagent_vars: dict[str, str] = {}
    trough_vars: dict[str, str] = {}
    fill_estimate: dict[str, float] = {}
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

    def trough_for(reagent: SpecReagent, need_ul: float) -> str:
        fill_estimate[reagent.id] = fill_estimate.get(reagent.id, 0.0) + need_ul
        if reagent.id not in trough_vars:
            large = (reagent.liquid_type or "") == "ethanol" or reagent.role == "wash"
            trough_vars[reagent.id] = w.trough(f"{reagent.id}_trough", large=large)
        return trough_vars[reagent.id]

    # Samples.
    sample_reagent = _pick(spec, "sample")
    samples = w.plate("Samples")
    if spec.sample_volume_ul:
        sample_ul = float(spec.sample_volume_ul)
    else:
        # Enough for the largest volume a step takes from the samples.
        drawn = [s.volume_ul for s in spec.steps if s.op == "transfer" and s.volume_ul]
        sample_ul = round(max([10.0, *(v * 1.1 for v in drawn)]), 1)
        w.notes.append(f"no sample volume in the spec; {sample_ul:g} ul per well is ASSUMED")
    current = samples
    if sample_reagent is not None:
        w.fills.append(f"    {samples}.fill_all({reagent_var(sample_reagent)}, {sample_ul:g})")
    else:
        w.notes.append("no sample reagent in the spec; the sample fill is ASSUMED")
        w.fills.append(f'    {samples}.fill_all(Reagent("Sample", role="analyte"), {sample_ul:g})  # ASSUMED')
    well_ul = sample_ul

    magnet_var = waste_var = None

    def ensure_magnet_and_waste() -> tuple[str, str]:
        nonlocal magnet_var, waste_var
        if magnet_var is None:
            if deck.magnet is None:
                raise ValueError("skeleton: the spec has a deck bead clean-up but the profile has no magnet")
            cat, loc, pos = deck.magnet
            magnet_var = w.var("magnet")
            w.placements.append(f'    {magnet_var} = wt.place(MagnetRack("Magnet", catalog="{cat}"), "{loc}", {pos})')
        if waste_var is None:
            if deck.waste is None:
                raise ValueError("skeleton: the profile has no waste reservoir")
            cat, loc, pos = deck.waste
            waste_var = w.var("waste")
            w.placements.append(f'    {waste_var} = wt.place(Trough25mL("Waste", catalog="{cat}"), "{loc}", {pos})')
        return magnet_var, waste_var

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
            loc, pos = ensure_handoff()
            w.body.append(
                f"    offdeck_step(wt, {json.dumps(text)}, labware={current}, "
                f'handoff=("{loc}", {pos}), name={json.dumps(name)})'
            )
        pending_offdeck.clear()

    for index, step in enumerate(spec.steps):
        if step.location != "deck" or step.op in {"measure", "manual"}:
            pending_offdeck.append(step)
            continue
        flush_offdeck(final=False)
        label = json.dumps(f"{step.id}: {' '.join(step.text.split())[:50]}")
        reagent = next((r for r in spec.reagents if r.id == step.reagent), None)

        if _is_cleanup(step, spec):
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
            bead_arg = f"bead_volume_ul={bead_ul:g}" if bead_given else f"bead_ratio={ratio:g}"
            bead_trough = trough_for(beads, bead_ul * n * 1.15 + 500)
            wash_trough = trough_for(wash, wash_ul * washes * n * 1.1 + 2000)
            eluent_trough = trough_for(eluent, elute_ul * n * 1.15 + 500)
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
                f"        liquid_class={json.dumps(lc)}, name={label},\n"
                f"    ){comment}"
            )
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
            current, well_ul = pool, vol * 12
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
            well_ul += vol
            continue

        if step.op == "add" and reagent is not None:
            vol = step.volume_ul if step.volume_ul is not None else 5.0
            trough = trough_for(reagent, vol * n * 1.15 + 500)
            tips = w.mca_box(f"{step.id}_Tips")
            w.body.append(
                f"    add_reagent(wt, reagent_source={trough}, plate={current}, volume_ul={vol:g},\n"
                f"                reagent_tips={tips}, liquid_class={json.dumps(lc)}, name={label})"
            )
            well_ul += vol
            continue

        if step.op == "transfer":
            dest = w.plate(f"{step.id}_Plate")
            tips = w.mca_box(f"{step.id}_Tips")
            vol = step.volume_ul if step.volume_ul is not None else well_ul
            if vol > well_ul - 1.0:
                w.notes.append(f"{step.id}: {vol:g} ul is more than the {well_ul:g} ul in the wells; moving {well_ul - 1.0:g} ul")
                vol = well_ul - 1.0
            w.body.append(
                f"    stamp(wt, source={current}, dest={dest}, volume_ul={vol:g}, tips={tips},\n"
                f"          liquid_class={json.dumps(lc)}, name={label})"
            )
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
    for reagent_id, trough in trough_vars.items():
        reagent = next((r for r in spec.reagents if r.id == reagent_id), None) or assumed_reagents[reagent_id]
        supply = _supply(reagent)
        need = fill_estimate[reagent_id]
        amount = min(need, supply) if supply is not None else need
        note = "  # kit supply" if supply is not None else "  # lab stock, estimated need"
        w.fills.append(f"    {trough}.fill_all({reagent_var(reagent)}, {amount:.0f}){note}")

    classes = sorted({
        cls for line in w.placements
        for cls in re.findall(r"wt\.place\((\w+)\(", line)
    } | {"Reagent", "Worktable"})
    used_blocks = sorted({b for b in ("spri_cleanup", "stamp", "add_reagent", "pool_columns", "offdeck_step")
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
