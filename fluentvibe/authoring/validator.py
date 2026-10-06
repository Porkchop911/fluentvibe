"""Validation harness for generated authoring output."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

from .models import FailureCategory, IntentSpec, ValidationReport
from .repair_policy import resolve_repair_policy
from .workspace_modules import WorkspaceModule, copy_workspace_modules

_BLOCK_LIQUID_CALL = re.compile(r"\b(?:spri_cleanup|stamp|add_reagent|pool_columns)\(")


class AuthoringValidator:
    def __init__(self, *, workspace_modules: tuple[WorkspaceModule, ...] = ()) -> None:
        self.workspace_modules = tuple(workspace_modules)

    def validate(
        self,
        source: str,
        *,
        output_dir: Path,
        stem: str,
        attempt_index: int,
        prompt: str | None = None,
        intent: IntentSpec | None = None,
    ) -> ValidationReport:
        output_dir.mkdir(parents=True, exist_ok=True)
        copy_workspace_modules(self.workspace_modules, output_dir)
        python_path = output_dir / f"{stem}.py"
        xscr_path = output_dir / f"{stem}.xscr"
        python_path.write_text(source, encoding="utf-8")

        contract_error = self._check_contract(source)
        if contract_error is None:
            contract_error = self._check_prompt_intent(source, prompt)
        if contract_error is not None:
            return ValidationReport(
                success=False,
                python_build_ok=False,
                compile_ok=False,
                strict_simulation_ok=False,
                failure_category=FailureCategory.PYTHON_BUILD_FAILURE,
                failure_message=contract_error,
                python_path=python_path,
                attempt_index=attempt_index,
            )

        try:
            wt = self._load_protocol(python_path)
        except Exception as exc:
            failure_details = _python_build_failure_details(exc)
            return ValidationReport(
                success=False,
                python_build_ok=False,
                compile_ok=False,
                strict_simulation_ok=False,
                failure_category=FailureCategory.PYTHON_BUILD_FAILURE,
                failure_message=str(exc),
                python_path=python_path,
                simulation_failure_category=failure_details.get("category"),
                simulation_failure_details=failure_details,
                repair_options=tuple(failure_details.get("repair_options") or ()),
                attempt_index=attempt_index,
            )

        geometry_error = self.check_transfer_geometry(wt, prompt)
        if geometry_error:
            return ValidationReport(
                success=False, python_build_ok=True, compile_ok=False,
                strict_simulation_ok=False,
                failure_category=FailureCategory.PYTHON_BUILD_FAILURE,
                failure_message=geometry_error, python_path=python_path,
                attempt_index=attempt_index,
            )

        try:
            wt.compile(xscr_path)
        except Exception as exc:
            return ValidationReport(
                success=False,
                python_build_ok=True,
                compile_ok=False,
                strict_simulation_ok=False,
                failure_category=FailureCategory.COMPILE_FAILURE,
                failure_message=str(exc),
                python_path=python_path,
                xscr_path=xscr_path,
                attempt_index=attempt_index,
            )

        try:
            wt.simulate(strict=True)
        except Exception as exc:
            report = getattr(wt, "simulation_report", None)
            simulation_failure_category = None
            simulation_failure_details = None
            repair_options: tuple[str, ...] = ()
            state_summary = getattr(report, "state_summary", None) if report is not None else None
            failure_category = FailureCategory.STRICT_SIMULATION_FAILURE
            if report is not None and getattr(report, "failure", None) is not None:
                simulation_failure_category = report.failure.category
                simulation_failure_details = report.failure.to_dict()
                repair_options = tuple(report.failure.repair_options)
                state_summary = report.state_summary
                if report.failure.category == "workspace_slot":
                    failure_category = FailureCategory.WORKSPACE_SLOT_INVALIDITY
                elif report.failure.category == "catalog":
                    failure_category = FailureCategory.MISSING_CATALOG_ITEM
                policy = resolve_repair_policy(
                    category=report.failure.category,
                    message=report.failure.message,
                )
                repair_hint = policy.guidance if policy.guidance else None
            return ValidationReport(
                success=False,
                python_build_ok=True,
                compile_ok=True,
                strict_simulation_ok=False,
                failure_category=failure_category,
                failure_message=str(exc),
                python_path=python_path,
                xscr_path=xscr_path,
                simulation_failure_category=simulation_failure_category,
                simulation_failure_details=simulation_failure_details,
                repair_options=repair_options,
                repair_hint=repair_hint,
                attempt_index=attempt_index,
                state_summary=state_summary if report is not None else None,
            )

        sim_report = getattr(wt, "simulation_report", None)
        final_labware = getattr(sim_report, "final_labware", None) if sim_report is not None else None

        # Narrowed scopes have no declare_intent tool. Recover the simple
        # spatial transfer contract from the request and built IR instead.
        inferred_intent = self.partial_stamp_intent(wt, prompt)
        if inferred_intent is not None:
            intent = inferred_intent

        intent_ok: bool | None = None
        if intent is not None and intent.is_specified():
            intent_failure = _check_intent_against_final_labware(intent, final_labware or {})
            if intent_failure is not None:
                return ValidationReport(
                    success=False,
                    python_build_ok=True,
                    compile_ok=True,
                    strict_simulation_ok=True,
                    failure_category=FailureCategory.STRICT_SIMULATION_FAILURE,
                    failure_message=intent_failure,
                    python_path=python_path,
                    xscr_path=xscr_path,
                    simulation_failure_category="intent_not_satisfied",
                    simulation_failure_details={
                        "category": "intent_not_satisfied",
                        "message": intent_failure,
                    },
                    repair_options=("ensure_destination_wells_receive_declared_target_volume",),
                    repair_hint="ensure_destination_wells_receive_declared_target_volume",
                    attempt_index=attempt_index,
                    final_labware=final_labware,
                    intent_check_ok=False,
                )
            intent_ok = True

        return ValidationReport(
            success=True,
            python_build_ok=True,
            compile_ok=True,
            strict_simulation_ok=True,
            python_path=python_path,
            xscr_path=xscr_path,
            attempt_index=attempt_index,
            state_summary=getattr(sim_report, "state_summary", None),
            final_labware=final_labware,
            intent_check_ok=intent_ok,
        )

    def _check_contract(self, source: str) -> str | None:
        uses_worklist = ".worklist(" in source or ".load_worklist(" in source
        required_snippets = (
            "def build_worktable() -> Worktable:",
            "Worktable.from_workspace(",
            "workspace_guid=",
            "auto_place=False",
        )
        for snippet in required_snippets:
            if snippet not in source:
                return f"Generated source is missing required contract snippet {snippet!r}."
        if not uses_worklist and "wt.place(" not in source:
            return "Generated source is missing required contract snippet 'wt.place('."
        forbidden_snippets = ("raw_xml_step(", "generic_step(")
        for snippet in forbidden_snippets:
            if snippet in source:
                return f"Generated source used unsupported fallback {snippet!r}."
        staged_error = _check_staged_source_contract(source)
        if staged_error is not None:
            return staged_error
        return None

    def _check_prompt_intent(self, source: str, prompt: str | None) -> str | None:
        if "print(" in source:
            return "Generated source contains debug output via print(...), which is not allowed in protocol drafts."
        if prompt is None:
            return None
        lowered = prompt.lower()
        transfer_intent = any(
            token in lowered
            for token in ("transfer", "stamp", "fill", "dispense", "aspirate", "pipette", "add ")
        )
        if not transfer_intent:
            return None
        if ".worklist(" in source or ".load_worklist(" in source:
            return None
        if _BLOCK_LIQUID_CALL.search(source):
            return None  # fluentvibe.blocks stages aspirate and dispense internally
        if ".aspirate(" not in source or ".dispense(" not in source:
            return (
                "Prompt asks for liquid handling, but generated source does not contain both "
                "aspirate(...) and dispense(...) calls."
            )
        return None

    @staticmethod
    def partial_stamp_intent(wt, prompt: str | None) -> IntentSpec | None:
        """Recognize the unambiguous left-half-to-center rectangular stamp.

        Supports repeated transfer pairs (split volumes) between 96-well
        plates; other geometries require their own resolved intent.
        """
        from ..ir.schema import AspirateStep, DispenseStep

        text = (prompt or "").lower()
        latest = text.splitlines()[-1] if text else ""
        if "instead" in latest and ("right half" in latest or "columns" in latest):
            return None  # An explicit spatial correction supersedes the default.
        if not ("left half" in text and re.search(r"\b(?:center(?:ed)?|centre(?:d)?|central)\b", text)
                and re.search(r"\bmca(?:96)?\b", text)):
            return None
        steps = [s for g in wt.to_protocol().groups for s in g.steps]
        aspirates = [s for s in steps if isinstance(s, AspirateStep)]
        dispenses = [s for s in steps if isinstance(s, DispenseStep)]
        if not aspirates or len(aspirates) != len(dispenses):
            return None
        src, dst = aspirates[0], dispenses[0]
        if any(s.labware_name != src.labware_name for s in aspirates) or any(
                s.labware_name != dst.labware_name for s in dispenses):
            return None
        labware = {lw.label: lw for stack in wt.slot_map.values() for lw in stack}
        if any(len(getattr(labware.get(label), "wells", {})) != 96
               for label in (src.labware_name, dst.labware_name)):
            return None
        return IntentSpec(
            target_volume_ul=_requested_transfer_volume(text),
            source_label=src.labware_name, destination_label=dst.labware_name,
            destination_wells=tuple(f"{row}{col}" for col in range(4, 10) for row in "ABCDEFGH"),
        )

    @classmethod
    def check_transfer_geometry(cls, wt, prompt: str | None) -> str | None:
        from ..ir.schema import AspirateStep, DispenseStep

        intent = cls.partial_stamp_intent(wt, prompt)
        if intent is None:
            return None
        steps = [s for g in wt.to_protocol().groups for s in g.steps]
        aspirates = [s for s in steps if isinstance(s, AspirateStep)]
        dispenses = [s for s in steps if isinstance(s, DispenseStep)]
        if any(s.columns != list(range(1, 7)) for s in aspirates) or any(
                s.columns != list(range(4, 10)) for s in dispenses):
            return ("Left-half-to-center MCA stamp on 96-well plates requires source "
                    "columns [1,2,3,4,5,6] and destination columns [4,5,6,7,8,9]. "
                    "Columns 7-12 are the right half, not the center.")
        return None

    def _load_protocol(self, input_path: Path):
        spec = importlib.util.spec_from_file_location(input_path.stem, input_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load {input_path}")
        module = importlib.util.module_from_spec(spec)
        original = sys.dont_write_bytecode
        original_path = list(sys.path)
        sys.dont_write_bytecode = True
        try:
            sys.path.insert(0, str(input_path.parent))
            spec.loader.exec_module(module)
        finally:
            sys.dont_write_bytecode = original
            sys.path[:] = original_path

        if hasattr(module, "build_worktable"):
            from ..worktable import Worktable

            wt = module.build_worktable()
            if not isinstance(wt, Worktable):
                raise ValueError("build_worktable() must return a Worktable; add 'return wt' at the end of the function")
            return wt
        raise ValueError(f"{input_path}: expected build_worktable()")


_VOLUME_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*[uµμ]l\b(?!\s*-?\s*(?:filter\s+)?(?:tip|box|rack))", re.I)
_CATALOG_SIZE_RE = re.compile(r"\b(?:mca(?:96|384)?|fca|liha)\s*,\s*\d+(?:\.\d+)?\s*[uµμ]l\b", re.I)


def _requested_transfer_volume(text: str) -> float | None:
    """The one transfer volume the request names, or None when it is not unique.

    A tip or box size ("200 ul tips", "MCA96, 200ul") is not a transfer volume:
    it was read as one, a 50 ul stamp was checked against 200 ul and a draft
    padded with unrequested liquid was accepted. The latest line wins over
    earlier turns (a correction); two different volumes leave the check to the
    declared intent."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    for chunk in ([lines[-1]] if lines else []) + ["\n".join(lines)]:
        values = {float(v) for v in _VOLUME_RE.findall(_CATALOG_SIZE_RE.sub(" ", chunk))}
        if len(values) == 1:
            return values.pop()
        if len(values) > 1:
            return None
    return None


def _check_intent_against_final_labware(
    intent: IntentSpec,
    final_labware: dict[str, Any],
) -> str | None:
    """Confirm the simulator delivered the LM-declared intent.

    Returns a human-readable failure message if the post-sim state does not
    match the declared destination well coverage and per-well target volume.
    Returns None on success or when the intent is too underspecified to check.
    """
    if intent.destination_label is None or intent.target_volume_ul is None:
        return None
    dest = final_labware.get(intent.destination_label) if final_labware else None
    if not isinstance(dest, dict):
        return (
            f"Intent declares destination labware {intent.destination_label!r} "
            "but the simulator did not record any final state for it."
        )
    wells = dest.get("wells") or {}
    if not isinstance(wells, dict) or not wells:
        return (
            f"Destination {intent.destination_label!r} has no recorded wells in the "
            "simulation report."
        )
    expected = (
        list(intent.destination_wells)
        if intent.destination_wells
        else sorted(wells.keys())
    )
    target = float(intent.target_volume_ul)
    tolerance = max(0.5, target * 0.05)
    incorrect: list[str] = []
    missing: list[str] = []
    for address in expected:
        well = wells.get(address)
        if well is None:
            missing.append(address)
            continue
        volume = float(well.get("volume_ul") or 0.0)
        if abs(volume - target) > tolerance:
            incorrect.append(f"{address}={volume:.1f}uL")
    unexpected = [
        address for address, well in wells.items()
        if intent.destination_wells and address not in expected
        and float(well.get("volume_ul") or 0.0) > tolerance
    ]
    if missing or incorrect or unexpected:
        details = []
        if missing:
            details.append(f"missing wells: {', '.join(missing[:8])}"
                           + (" ..." if len(missing) > 8 else ""))
        if incorrect:
            details.append(f"incorrect volumes: {', '.join(incorrect[:8])}"
                           + (" ..." if len(incorrect) > 8 else ""))
        if unexpected:
            details.append(f"unexpected filled wells: {', '.join(unexpected[:8])}")
        return (
            f"Intent not satisfied on {intent.destination_label!r}: expected "
            f"{target:.1f} uL in {len(expected)} wells. "
            + "; ".join(details)
        )
    return None


def _check_staged_source_contract(source: str) -> str | None:
    if ".worklist(" in source or ".load_worklist(" in source:
        return None
    group_pattern = re.compile(r"wt\.group\(\s*['\"]([^'\"]+)['\"]\s*\)")
    first_group_match = group_pattern.search(source)
    if first_group_match is None:
        return "Generated source must start executable steps with wt.group('Labware Placement')."
    if first_group_match.group(1) == "Variables":
        # A variables group (wt.volume: volume variables and their
        # calculations) may come first; placement follows it.
        next_group = group_pattern.search(source, first_group_match.end())
        if next_group is not None and "wt.place(" not in source[first_group_match.end():next_group.start()]:
            first_group_match = next_group
    first_group_start = first_group_match.start()
    first_declare = source.find("wt.declare_variable(")
    if first_declare < 0:
        return "Generated source must declare at least one protocol variable with wt.declare_variable(...) before labware placement."
    first_sim_value = source.find("wt.set_sim_value(")
    if first_sim_value < 0:
        return "Generated source must seed simulator variable values with wt.set_sim_value(...) before labware placement."
    if first_declare > first_group_start or first_sim_value > first_group_start:
        return "Generated source must declare variables and set sim values before any wt.group(...)."
    if first_group_match.group(1) != "Labware Placement":
        return "The first executable group after variables must be wt.group('Labware Placement')."
    first_place = source.find("wt.place(")
    if first_place < 0 or first_place < first_group_start:
        return "Labware placement must occur inside the Labware Placement group."
    return None


def _python_build_failure_details(exc: Exception) -> dict[str, Any]:
    message = str(exc)
    valid_classes = [
        "Adapter", "EvaAdapter", "FCA1000Box", "FCA200Box", "FCA50Box",
        "FixedDeck", "Hotel", "Labware", "MCA100Box", "MCA200Box", "MCA500Box",
        "MagnetRack", "Plate", "Plate384", "Plate96", "Plate96Deep",
        "TipBox", "Trough", "Trough25mL", "Trough100mL", "TubeRack",
        "Waste", "WasteChute", "WashStation", "Worktable",
    ]
    if isinstance(exc, (ImportError, NameError)):
        return {
            "category": FailureCategory.PYTHON_BUILD_FAILURE.value,
            "exception_type": type(exc).__name__,
            "message": message,
            "details": {"valid_exported_classes": valid_classes},
            "valid_exported_classes": valid_classes,
            "repair_options": ["use_exported_fluentvibe_class", "call_lookup_api_for_unknown_symbol"],
        }
    if isinstance(exc, AttributeError):
        match = re.search(r"'([^']+)' object has no attribute '([^']+)'", message)
        details = {}
        if match:
            details = {"object": match.group(1), "method": match.group(2)}
        return {
            "category": "missing_method",
            "exception_type": type(exc).__name__,
            "message": message,
            "details": details,
            **details,
            "repair_options": ["call_lookup_api", "rewrite_using_supported_method"],
        }
    return {
        "category": FailureCategory.PYTHON_BUILD_FAILURE.value,
        "exception_type": type(exc).__name__,
        "message": message,
        "details": {},
        "repair_options": [],
    }
