"""Deterministic quality rubric for an authored protocol.

Turns "is this generation any good?" into a fixed set of pass/fail invariants so
generation quality is a *measured number*, comparable run-over-run and across
model / KV-cache / skill changes. Two tiers:

* **Source tier** — cheap AST/regex checks over the generated Python
  (``score_source``): is the analyte role-tagged, are the derived bead-cleanup
  volumes right, is there a separate eluate tip box, every source-document stage
  covered (reuses :func:`document_adherence.coverage_gaps`).
* **Semantic tier** — the ground truth (``score_semantic``): execute
  ``build_worktable``, ``simulate()``, and inspect the final snapshot — did the
  magnet round-trip (bind → elute → recover), did the analyte actually land in a
  clean eluate labware, and is it kept out of waste? This mirrors the assertions
  in ``tests/test_ampure_sat_1080.py``.

The catastrophic bead-cleanup error the f16 model still makes — eluting but never
recovering the eluate off the beads — is caught by the semantic tier
(``eluate_recovered`` fails) and corroborated by the source tier
(``analyte_role_tagged`` / ``separate_eluate_destination``).
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from typing import Any, Iterable

from .document_adherence import coverage_gaps, document_adherence_report

SOURCE_KEYS = (
    "coverage_complete",
    "analyte_role_tagged",
    "derived_supernatant",
    "derived_eluate",
    "off_magnet_elution",
    "separate_eluate_destination",
)
SEMANTIC_KEYS = (
    "magnet_roundtrip",
    "eluate_recovered",
    "analyte_not_in_waste",
)
ALL_KEYS = SOURCE_KEYS + SEMANTIC_KEYS

_PASS, _FAIL, _NA = "pass", "fail", "na"


@dataclass(frozen=True)
class Invariant:
    """One scored check. ``status`` is ``pass`` / ``fail`` / ``na``."""

    key: str
    status: str
    evidence: str

    @property
    def ok(self) -> bool:
        return self.status == _PASS


@dataclass(frozen=True)
class RubricResult:
    invariants: tuple[Invariant, ...]

    def get(self, key: str) -> Invariant | None:
        for inv in self.invariants:
            if inv.key == key:
                return inv
        return None

    @property
    def passed(self) -> int:
        return sum(1 for i in self.invariants if i.status == _PASS)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.invariants if i.status == _FAIL)

    @property
    def na(self) -> int:
        return sum(1 for i in self.invariants if i.status == _NA)

    @property
    def applicable(self) -> int:
        return self.passed + self.failed

    @property
    def score(self) -> float:
        """Fraction of *applicable* (non-NA) invariants that passed; 0..1."""
        return self.passed / self.applicable if self.applicable else 0.0

    def to_row(self) -> dict[str, str]:
        """Flat ``{key: status}`` mapping plus the aggregate, for CSV rows."""
        row = {inv.key: inv.status for inv in self.invariants}
        row["score"] = f"{self.score:.3f}"
        return row


# ── Source tier ──────────────────────────────────────────────────────────


def _parse(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _declared_numeric_vars(tree: ast.AST) -> dict[str, float]:
    """``wt.declare_variable("NAME", <number>)`` calls → ``{NAME: value}``."""
    out: dict[str, float] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "declare_variable"):
            continue
        if len(node.args) < 2 or not isinstance(node.args[0], ast.Constant):
            continue
        name = str(node.args[0].value).strip().upper()
        val = node.args[1]
        if isinstance(val, ast.Constant) and isinstance(val.value, (int, float)):
            out[name] = float(val.value)
        elif (
            isinstance(val, ast.UnaryOp)
            and isinstance(val.op, ast.USub)
            and isinstance(val.operand, ast.Constant)
            and isinstance(val.operand.value, (int, float))
        ):
            out[name] = -float(val.operand.value)
    return out


def _first(vars_: dict[str, float], *needles: str) -> float | None:
    for name, value in vars_.items():
        if any(n in name for n in needles):
            return value
    return None


def _check_analyte_role(source: str) -> Invariant:
    if re.search(r"role\s*=\s*[\"']analyte[\"']", source):
        return Invariant("analyte_role_tagged", _PASS, "Reagent(..., role=\"analyte\")")
    return Invariant(
        "analyte_role_tagged",
        _FAIL,
        "no analyte reagent — the bead model cannot track the product",
    )


def _check_derived_supernatant(vars_: dict[str, float]) -> Invariant:
    sup = _first(vars_, "SUPERNATANT")
    sample = _first(vars_, "SAMPLE_VOL", "TARGET_VOL")
    bead = _first(vars_, "BEAD_VOL")
    retain = _first(vars_, "RETAIN")
    if sup is None:
        return Invariant("derived_supernatant", _NA, "no supernatant aspirate variable")
    if None in (sample, bead, retain):
        return Invariant(
            "derived_supernatant", _FAIL,
            f"supernatant={sup} but missing primitive(s) to derive it",
        )
    expected = sample + bead - retain
    if abs(sup - expected) <= 0.5:
        return Invariant("derived_supernatant", _PASS, f"{sup} == {sample}+{bead}-{retain}")
    return Invariant(
        "derived_supernatant", _FAIL,
        f"{sup} != sample+bead-retain ({expected})",
    )


def _check_derived_eluate(vars_: dict[str, float]) -> Invariant:
    transfer = _first(vars_, "TRANSFER", "ELUATE")
    elution = _first(vars_, "ELUTION_VOL")
    retain = _first(vars_, "RETAIN")
    if transfer is None:
        return Invariant(
            "derived_eluate", _NA,
            "no eluate-transfer volume — protocol may never recover the eluate",
        )
    if None in (elution, retain):
        return Invariant(
            "derived_eluate", _FAIL,
            f"eluate transfer={transfer} but missing elution/retain to derive it",
        )
    expected = elution - retain
    if abs(transfer - expected) <= 0.5:
        return Invariant("derived_eluate", _PASS, f"{transfer} == {elution}-{retain}")
    return Invariant("derived_eluate", _FAIL, f"{transfer} != elution-retain ({expected})")


def _gripper_move_sequence(source: str) -> list[str]:
    """Ordered list of ``'onto'`` / ``'to'`` for each ``gripper.move(...)``."""
    seq: list[str] = []
    for m in re.finditer(r"gripper\.move\((.*?)\)", source, flags=re.DOTALL):
        seg = m.group(1)
        if "onto=" in seg:
            seq.append("onto")
        elif "to=" in seg:
            seq.append("to")
    return seq


def _check_off_magnet_elution(source: str) -> Invariant:
    """The canonical bind→elute shape: an ``onto=`` (bind) followed by a ``to=``
    (move off the magnet to elute). Semantic ``magnet_roundtrip`` is the
    authoritative ordering proof; this is the cheap source-level smell test."""
    seq = _gripper_move_sequence(source)
    for i, kind in enumerate(seq):
        if kind == "onto" and "to" in seq[i + 1:]:
            return Invariant("off_magnet_elution", _PASS, "onto=magnet then to=(off magnet)")
    if not seq:
        return Invariant("off_magnet_elution", _NA, "no gripper.move — not a magnet protocol")
    return Invariant(
        "off_magnet_elution", _FAIL,
        f"gripper.move sequence {seq} never leaves the magnet to elute",
    )


def _count_mca_tip_boxes(source: str) -> int:
    return len(re.findall(r"MCA\d+Box\(", source))


def _check_separate_eluate_destination(source: str) -> Invariant:
    boxes = _count_mca_tip_boxes(source)
    if boxes >= 2:
        return Invariant(
            "separate_eluate_destination", _PASS,
            f"{boxes} MCA tip boxes (dedicated eluate box avoids bead carryover)",
        )
    return Invariant(
        "separate_eluate_destination", _FAIL,
        f"only {boxes} MCA tip box — eluate transfer reuses waste/wash tips (bead carryover)",
    )


def _without_comment_only_evidence(source: str) -> str:
    """Remove comments, docstrings, and ``wt.add_comment(...)`` evidence.

    The general document-adherence report intentionally implements an
    "automate or justify" policy. The quality rubric is stricter: prose may
    explain a manual stage, but it must not make that stage count as automated.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        source = tokenize.untokenize(
            token._replace(string="") if token.type == tokenize.COMMENT else token
            for token in tokens
        )
        tree = ast.parse(source)
    except (IndentationError, SyntaxError, tokenize.TokenError):
        return source

    lines = source.splitlines(keepends=True)
    ignored_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_comment"
        ):
            ignored_ranges.append((node.lineno, node.end_lineno or node.lineno))
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            ignored_ranges.append((node.lineno, node.end_lineno or node.lineno))
    for start, end in ignored_ranges:
        for index in range(start - 1, min(end, len(lines))):
            lines[index] = "\n" if lines[index].endswith("\n") else ""
    return "".join(lines)


def _check_coverage(source: str, source_text: str | None) -> Invariant:
    if not source_text:
        return Invariant("coverage_complete", _NA, "no source document supplied")
    report = document_adherence_report(source_text=source_text, protocol_source=source)
    gaps = coverage_gaps(report)
    if gaps:
        codes = ", ".join(sorted({g.get("code", "?") for g in gaps}))
        return Invariant("coverage_complete", _FAIL, f"uncovered stages: {codes}")

    automated_report = document_adherence_report(
        source_text=source_text,
        protocol_source=_without_comment_only_evidence(source),
    )
    comment_only_gaps = coverage_gaps(automated_report)
    if comment_only_gaps:
        codes = ", ".join(sorted({g.get("code", "?") for g in comment_only_gaps}))
        return Invariant(
            "coverage_complete",
            _FAIL,
            f"stages represented only by comments/manual notes: {codes}",
        )
    return Invariant("coverage_complete", _PASS, "no automated coverage gaps")


def score_source(source: str, source_text: str | None = None) -> list[Invariant]:
    """The cheap source-tier invariants. Never raises."""
    coverage = _check_coverage(source, source_text)
    tree = _parse(source)
    if tree is None:
        # Source tier still reports what regex can; volume checks need the AST.
        vars_: dict[str, float] = {}
    else:
        vars_ = _declared_numeric_vars(tree)
    return [
        coverage,
        _check_analyte_role(source),
        _check_derived_supernatant(vars_),
        _check_derived_eluate(vars_),
        _check_off_magnet_elution(source),
        _check_separate_eluate_destination(source),
    ]


# ── Semantic tier ────────────────────────────────────────────────────────


def build_worktable_from_source(source: str, filename: str = "<generated>"):
    """Execute ``source`` and return its ``build_worktable()`` result."""
    namespace: dict[str, Any] = {}
    exec(compile(source, filename, "exec"), namespace)  # noqa: S102 - trusted authoring output
    builder = namespace.get("build_worktable")
    if not callable(builder):
        raise ValueError("source defines no build_worktable() callable")
    return builder()


def _iter_labware(snapshot) -> Iterable[Any]:
    seen: set[int] = set()
    for stack in snapshot.slot_map.values():
        for lw in stack:
            if id(lw) not in seen:
                seen.add(id(lw))
                yield lw


def _well_reagents(well) -> list[tuple[Any, str]]:
    out: list[tuple[Any, str]] = [(layer.reagent, "free") for layer in well.layers]
    bead_phase = getattr(well, "bead_phase", None)
    if bead_phase is not None:
        out.extend((layer.reagent, "bound") for layer in bead_phase.bound)
    return out


def _is_analyte(reagent) -> bool:
    return getattr(reagent, "role", None) == "analyte"


def _is_waste(labware) -> bool:
    return "waste" in str(getattr(labware, "label", "")).lower()


def _magnet_plate_label(snapshots) -> str | None:
    """Label of the plate that is ever magnetized.

    The bead phase alone does not identify it — the supernatant draw carries
    beads into the waste, so waste also shows a bead phase. The plate that the
    gripper actually stacks onto the magnet is the cleanup plate.
    """
    for snap in snapshots:
        for lw in _iter_labware(snap):
            if getattr(lw, "is_magnetized", False):
                return lw.label
    return None


def _has_analyte_anywhere(snapshot) -> bool:
    for lw in _iter_labware(snapshot):
        for well in getattr(lw, "wells", {}).values():
            if any(_is_analyte(r) for r, _ in _well_reagents(well)):
                return True
    return False


def _check_magnet_roundtrip(snapshots, magnet_label: str | None) -> Invariant:
    if magnet_label is None:
        return Invariant("magnet_roundtrip", _FAIL, "no plate is ever magnetized")
    series: list[bool] = []
    for snap in snapshots:
        try:
            series.append(bool(snap.labware(magnet_label).is_magnetized))
        except KeyError:
            continue
    # Collapse consecutive duplicates, then look for bind→off→recover (T,F,T).
    collapsed: list[bool] = []
    for value in series:
        if not collapsed or collapsed[-1] != value:
            collapsed.append(value)
    for i in range(len(collapsed) - 2):
        if collapsed[i] and not collapsed[i + 1] and collapsed[i + 2]:
            return Invariant("magnet_roundtrip", _PASS, "magnet bind → off (elute) → on (recover)")
    return Invariant(
        "magnet_roundtrip", _FAIL,
        f"magnet never re-engaged to recover eluate (pattern {collapsed})",
    )


def _check_eluate_recovered(
    final,
    magnet_label: str | None,
    has_analyte: bool,
    *,
    magnet_roundtrip_ok: bool,
) -> Invariant:
    if magnet_label is None:
        return Invariant(
            "eluate_recovered", _FAIL,
            "no cleanup plate was magnetized — recovery cannot be verified",
        )
    if not magnet_roundtrip_ok:
        return Invariant(
            "eluate_recovered", _FAIL,
            "magnet bind → off-magnet elution → recovery round-trip did not complete",
        )
    if not has_analyte:
        return Invariant(
            "eluate_recovered", _FAIL,
            "no analyte reagent in the simulated state — recovery cannot be verified",
        )
    for lw in _iter_labware(final):
        if lw.label == magnet_label or _is_waste(lw):
            continue
        for well in getattr(lw, "wells", {}).values():
            if any(_is_analyte(r) and phase == "free" for r, phase in _well_reagents(well)):
                return Invariant(
                    "eluate_recovered", _PASS,
                    f"analyte recovered as free eluate in {lw.label!r}",
                )
    return Invariant(
        "eluate_recovered", _FAIL,
        "analyte never transferred to a clean plate (stranded on beads / in sample well)",
    )


def _check_analyte_not_in_waste(final, has_analyte: bool) -> Invariant:
    if not has_analyte:
        return Invariant("analyte_not_in_waste", _NA, "no analyte reagent to track")
    for lw in _iter_labware(final):
        if not _is_waste(lw):
            continue
        for well in getattr(lw, "wells", {}).values():
            if any(_is_analyte(r) for r, _ in _well_reagents(well)):
                return Invariant(
                    "analyte_not_in_waste", _FAIL,
                    f"analyte dumped into waste labware {lw.label!r}",
                )
    return Invariant("analyte_not_in_waste", _PASS, "no analyte in any waste labware")


def score_semantic(wt) -> list[Invariant]:
    """Simulate ``wt`` (if needed) and score the bead-model ground truth."""
    if not getattr(wt, "snapshots", None):
        wt.simulate()
    snapshots = wt.snapshots
    if not snapshots:
        return [Invariant(k, _NA, "simulation produced no snapshots") for k in SEMANTIC_KEYS]
    final = snapshots[-1]
    magnet_label = _magnet_plate_label(snapshots)
    has_analyte = _has_analyte_anywhere(final)
    magnet_roundtrip = _check_magnet_roundtrip(snapshots, magnet_label)
    return [
        magnet_roundtrip,
        _check_eluate_recovered(
            final,
            magnet_label,
            has_analyte,
            magnet_roundtrip_ok=magnet_roundtrip.ok,
        ),
        _check_analyte_not_in_waste(final, has_analyte),
    ]


# ── Combined ─────────────────────────────────────────────────────────────


def score_protocol(
    source: str,
    *,
    source_text: str | None = None,
    simulate: bool = True,
    filename: str = "<generated>",
) -> RubricResult:
    """Score one generated protocol across both tiers.

    The semantic tier degrades to ``na`` (never crashes the score) if the source
    cannot be executed/simulated — its source tier still scores.
    """
    invariants = list(score_source(source, source_text))
    if simulate:
        try:
            wt = build_worktable_from_source(source, filename)
            invariants.extend(score_semantic(wt))
        except Exception as exc:  # noqa: BLE001 - any failure → semantic NA
            invariants.extend(
                Invariant(k, _NA, f"did not simulate: {exc}") for k in SEMANTIC_KEYS
            )
    return RubricResult(tuple(invariants))
