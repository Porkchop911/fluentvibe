"""Deterministic resolution of deck mechanics behind high-level calls.

``wt.add(reagent, to=plate, volume_ul=20)`` says *what* happens; the resolver
decides *how* on this deck: which head, which source labware on which free
position, which tips, how much to fill. The same rules the spec skeleton uses
(see ``authoring/skeleton.py``), read from the deck profile
(``FLUENTVIBE_PROFILE_DIR``):

* reagents (beads, buffers, master mixes) go through the FCA from a slim
  trough; cheap bulk liquids (water, ethanol, washes) through the MCA96 from an
  SBS reservoir;
* the MCA96 never pipettes from a slim trough (FluentControl: out of range);
* troughs are placed on free positions of the right kind, skipping positions
  the workspace or the protocol already occupies;
* fill volumes are the resolved demand (plus dead volume), set when the
  protocol is simulated or compiled.

Explicit arguments (``head=``, ``source=``, ``liquid_class_var=``) are
requirements: an impossible combination raises :class:`ResolutionConflict`
instead of silently choosing something else. Every automatic choice is
recorded with its reason (:meth:`Resolver.report`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

_CHEAP_MARKERS = ("ethanol", "etoh", "water", "wash", "isopropanol")
_SLIM_FILL_MAX_UL = 22000.0   # 25 ml slim trough; above this the 100 ml one
_SBS_SMALL_MAX_UL = 55000.0   # "60ml SBS MCA96"
_DEAD_UL = {"fca": 2000.0, "mca": 500.0}
_FCA_USES_PER_BOX = 12        # 8 tips per distribution, 96 per box


class ResolutionError(ValueError):
    """The deck cannot provide what the call needs (no free site, no profile)."""


class ResolutionConflict(ResolutionError):
    """An explicit requirement cannot be met as stated."""


@dataclass
class Decision:
    call: str
    choice: str
    reason: str


@dataclass
class _Source:
    labware: Any
    head: str
    reagent: Any
    demand_ul: float = 0.0
    explicit: bool = False


@dataclass
class Resolver:
    wt: Any
    deck: Any
    decisions: list[Decision] = field(default_factory=list)
    sources: dict[tuple[int, str], _Source] = field(default_factory=dict)
    fca_boxes: list[Any] = field(default_factory=list)
    fca_uses: int = 0
    mca_reagent_tips: Any = None
    finalized: bool = False

    # ── construction ────────────────────────────────────────────────────
    @classmethod
    def for_worktable(cls, wt) -> "Resolver":
        from .authoring.profile import profile_from_env
        from .authoring.skeleton import load_deck

        profile = profile_from_env()
        if profile is None:
            raise ResolutionError(
                "wt.add() resolves sources and tips from the deck profile; set FLUENTVIBE_PROFILE_DIR "
                "to the workspace profile (or place labware and use the blocks directly)."
            )
        return cls(wt=wt, deck=load_deck(profile.root))

    # ── deck bookkeeping ────────────────────────────────────────────────
    def _occupied(self) -> set[tuple[str, int]]:
        taken = set()
        for labware in getattr(self.wt, "_placed", {}).values():
            slot = getattr(labware, "slot", None)
            if slot:
                taken.add((slot[0], int(slot[1])))
        return taken

    def _free(self, candidates: Iterable[tuple[str, int]]) -> Optional[tuple[str, int]]:
        taken = self._occupied()
        return next((c for c in candidates if c not in taken), None)

    def _unique_label(self, base: str) -> str:
        import re

        base = re.sub(r"[^0-9A-Za-z_-]+", "_", base).strip("_") or "Labware"
        names = set(getattr(self.wt, "_placed", {}))
        label, n = base, 2
        while label in names:
            label, n = f"{base}_{n}", n + 1
        return label

    def _place(self, labware, site: Optional[tuple[str, int]], what: str):
        if site is None:
            raise ResolutionError(f"no free deck position left for {what}")
        placed = self.wt.place(labware, site[0], site[1])
        self._hoist_last_placement()
        return placed

    def _hoist_last_placement(self) -> None:
        """Move the placement just emitted into the protocol's labware group, so
        resolved labware is on the deck from the start, not placed mid-run."""
        wt = self.wt
        groups = getattr(wt, "_groups", [])
        target = next((g for g in groups if "labware" in g.name.lower() or "placement" in g.name.lower()),
                      groups[0] if groups else None)
        current = wt._emit_target_stack[-1] if wt._emit_target_stack else (
            wt._active_group.steps if wt._active_group is not None else None)
        if target is None or current is None or current is target.steps or not current:
            return
        step = current.pop()
        last_add = max((i for i, st in enumerate(target.steps) if type(st).__name__ == "AddLabwareStep"),
                       default=len(target.steps) - 1)
        target.steps.insert(last_add + 1, step)

    # ── choices ─────────────────────────────────────────────────────────
    @staticmethod
    def _is_cheap(reagent) -> bool:
        kind = str((getattr(reagent, "metadata", {}) or {}).get("liquid_type") or "").lower()
        name = str(getattr(reagent, "name", "")).lower()
        return kind in {"ethanol", "water", "wash"} or any(m in name for m in _CHEAP_MARKERS)

    def _head(self, reagent, head: Optional[str], source) -> tuple[str, str]:
        if head is not None:
            head = head.lower()
            if head not in {"fca", "mca"}:
                raise ResolutionConflict(f"head must be 'fca' or 'mca', got {head!r}")
            return head, "requested"
        if source is not None and getattr(source, "category", None) == "trough" and not self._sbs(source):
            return "fca", "the given source is a slim trough (the MCA96 cannot pipette from it)"
        if self._is_cheap(reagent):
            return "mca", "cheap bulk liquid (water/ethanol/wash): MCA96 from an SBS reservoir"
        return "fca", "reagent: FCA from a slim trough (little dead volume)"

    def _capacity(self, entry: _Source) -> float:
        catalog = str(getattr(entry.labware, "catalog_name", "") or "")
        if catalog == self.deck.slim_small:
            return _SLIM_FILL_MAX_UL
        if catalog == self.deck.reservoir_small:
            return _SBS_SMALL_MAX_UL
        if catalog == self.deck.reservoir_large:
            return 250000.0
        return 90000.0  # 100 ml slim trough

    @staticmethod
    def _sbs(labware) -> bool:
        catalog = str(getattr(labware, "catalog_name", "") or "").lower()
        return "sbs" in catalog or "mca96" in catalog

    def _source(self, reagent, head: str, need_ul: float, source, call: str) -> _Source:
        key = (id(reagent), head)
        if source is not None:
            if head == "mca" and getattr(source, "category", None) == "trough" and not self._sbs(source):
                raise ResolutionConflict(
                    f"{call}: head='mca' with source {source.label!r} ({source.catalog_name!r}): the MCA96 "
                    f"cannot pipette from a slim trough (FluentControl: out of range). Use head='fca', or an "
                    f"SBS reservoir as the source."
                )
            entry = self.sources.get(key)
            if entry is None or entry.labware is not source:
                entry = _Source(labware=source, head=head, reagent=reagent, explicit=True)
                self.sources[key] = entry
            entry.demand_ul += need_ul
            return entry
        entry = self.sources.get(key)
        if entry is not None and entry.demand_ul + need_ul <= self._capacity(entry):
            entry.demand_ul += need_ul
            return entry
        if entry is not None:
            self.decisions.append(Decision(call, "new source", f"{entry.labware.label} would hold "
                                           f"{entry.demand_ul + need_ul:.0f} ul, more than it takes"))
        from .labware.troughs import Trough25mL, Trough100mL

        name = self._unique_label(f"{reagent.name}_{head.upper()}_source")
        if head == "fca":
            big = need_ul > _SLIM_FILL_MAX_UL
            catalog = self.deck.slim_large if big else self.deck.slim_small
            cls = Trough100mL if big else Trough25mL
            site = self._free(self.deck.free_trough_sites)
            labware = self._place(cls(name, catalog=catalog), site, f"a slim trough for {reagent.name}")
            reason = f"slim trough {catalog!r} on free trough site {site}"
        else:
            large = need_ul > _SBS_SMALL_MAX_UL or self._is_cheap(reagent)
            site = self._free(self.deck.free_large_sites) if large else None
            if site is not None:
                labware = self._place(Trough25mL(name, catalog=self.deck.reservoir_large), site,
                                      f"an SBS reservoir for {reagent.name}")
                reason = f"{self.deck.reservoir_large!r} on MCA-reachable {site}"
            else:
                site = self._free(self.deck.free_nests)
                labware = self._place(Trough100mL(name, catalog=self.deck.reservoir_small), site,
                                      f"an SBS reservoir for {reagent.name}")
                reason = f"{self.deck.reservoir_small!r} on free plate nest {site}"
        self.decisions.append(Decision(call, f"source {name}", reason))
        previous = self.sources.get(key)
        if previous is not None:  # full: keep it under its own key, the new one takes over
            self.sources[(id(previous), head)] = previous
        entry = _Source(labware=labware, head=head, reagent=reagent, demand_ul=need_ul)
        self.sources[key] = entry
        return entry

    def _fca_tips(self, call: str):
        if not self.deck.fca_tips:
            raise ResolutionError("the deck profile lists no FCA tip box")
        if self.fca_uses % _FCA_USES_PER_BOX == 0:
            from . import FCA200Box, FCA1000Box, FCA50Box

            catalog, cls_name = self.deck.fca_tips
            cls = {"FCA200Box": FCA200Box, "FCA1000Box": FCA1000Box, "FCA50Box": FCA50Box}.get(cls_name, FCA200Box)
            site = self._free(self.deck.free_nests)
            box = self._place(cls(self._unique_label(f"FcaTips{len(self.fca_boxes) + 1}"), catalog=catalog), site,
                              "an FCA tip box")
            self.fca_boxes.append(box)
            self.decisions.append(Decision(call, f"tips {box.label}", f"FCA box {catalog!r} on {site}; "
                                           f"8 tips per distribution, {_FCA_USES_PER_BOX} per box"))
        self.fca_uses += 1
        return self.fca_boxes[-1]

    def _mca_tips(self, call: str):
        if self.mca_reagent_tips is None:
            if not self.deck.mca_tips:
                raise ResolutionError("the deck profile lists no MCA96 tip box")
            from . import MCA100Box, MCA200Box

            cls = MCA200Box if "200" in self.deck.mca_tips else MCA100Box
            site = self._free(self.deck.free_nests)
            self.mca_reagent_tips = self._place(cls(self._unique_label("McaReagentTips"), catalog=self.deck.mca_tips),
                                                site, "an MCA96 tip box")
            self.decisions.append(Decision(call, f"tips {self.mca_reagent_tips.label}",
                                           f"MCA96 reagent tips {self.deck.mca_tips!r} on {site}; reagent-only "
                                           f"contact, reused for every MCA addition"))
        return self.mca_reagent_tips

    def _liquid_class(self, liquid_class: Optional[str], liquid_class_var: Optional[str], call: str) -> str:
        default = liquid_class or self.deck.liquid_class
        if liquid_class_var is None:
            return default
        variables = self.wt.protocol_variables
        if liquid_class_var not in variables:
            self.wt.declare_variable(liquid_class_var, default)
            self.wt.set_sim_value(liquid_class_var, default)
            self.decisions.append(Decision(call, f"variable {liquid_class_var}",
                                           f"string variable, default {default!r}"))
        elif not isinstance(variables[liquid_class_var], str):
            raise ResolutionConflict(f"{call}: {liquid_class_var} is declared but not a string variable")
        return liquid_class_var

    # ── the call ────────────────────────────────────────────────────────
    def add(self, reagent, *, to, volume_ul: float, head: Optional[str] = None, source=None,
            liquid_class: Optional[str] = None, liquid_class_var: Optional[str] = None,
            columns: Optional[Iterable[int]] = None, name: Optional[str] = None) -> None:
        from .blocks import add_reagent, distribute_reagent

        if self.finalized:
            raise ResolutionError("wt.add() after the protocol was simulated or compiled")
        call = name or f"add {reagent.name}"
        chosen_head, why = self._head(reagent, head, source)
        self.decisions.append(Decision(call, f"head {chosen_head}", why))
        cols = sorted({int(c) for c in columns}) if columns is not None else list(range(1, 13))
        need = float(volume_ul) * 8 * len(cols) * 1.1
        src = self._source(reagent, chosen_head, need, source, call)
        lc = self._liquid_class(liquid_class, liquid_class_var, call)
        column_arg = None if cols == list(range(1, 13)) else cols
        if chosen_head == "fca":
            distribute_reagent(self.wt, source=src.labware, plate=to, volume_ul=volume_ul,
                               tips=self._fca_tips(call), liquid_class=lc, columns=column_arg, name=name)
        else:
            add_reagent(self.wt, reagent_source=src.labware, plate=to, volume_ul=volume_ul,
                        reagent_tips=self._mca_tips(call), liquid_class=lc, columns=column_arg, name=name)

    # ── finishing ───────────────────────────────────────────────────────
    def finalize(self) -> None:
        """Fill resolved sources with their demand (plus dead volume). Idempotent."""
        if self.finalized:
            return
        for src in self.sources.values():
            if src.explicit and any(w.layers for w in src.labware.wells.values()):
                continue  # the author filled it
            amount = math.ceil(src.demand_ul + _DEAD_UL[src.head])
            src.labware.fill_all(src.reagent, amount)
            self.decisions.append(Decision(f"fill {src.labware.label}", f"{amount} ul {src.reagent.name}",
                                           "resolved demand x1.1 plus dead volume"))
        self.finalized = True

    def report(self) -> list[dict[str, str]]:
        return [{"call": d.call, "choice": d.choice, "reason": d.reason} for d in self.decisions]
