"""Headless analyzer: protocol source -> structured diagnostics.

Reuses the existing pipeline rather than re-implementing it:
- module load mirrors the validator / CLI ``build_worktable()`` contract,
- physical errors come from ``Worktable.simulate`` (``SimulationFailure`` already
  carries ``source_pos`` after copilot Phase 0),
- repair hints come from ``authoring.repair_policy`` (the same text the LLM
  authoring loop uses), so deterministic and LLM paths stay consistent.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..authoring.repair_policy import resolve_repair_policy
from .fixes import Fix, compute_fixes

Severity = str  # "error" | "warning" | "info"


@dataclass
class Diagnostic:
    """One editor-facing finding, positioned in the author's source."""

    line: int
    severity: Severity
    code: str  # failure category, e.g. "source_volume_short", "missing_method"
    message: str
    source: str  # "build" | "simulate"
    file: Optional[str] = None
    end_line: Optional[int] = None
    col: Optional[int] = None
    hint: str = ""
    repair_options: list[str] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "col": self.col,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "source": self.source,
            "hint": self.hint,
            "repair_options": list(self.repair_options),
            "fixes": [f.to_dict() for f in self.fixes],
        }


def analyze_file(path: str | Path) -> list[Diagnostic]:
    """Analyze a protocol ``.py`` on disk and return its diagnostics."""
    path = Path(path)
    source = path.read_text(encoding="utf-8")
    return analyze_source(source, path)


def analyze_source(source: str, path: str | Path) -> list[Diagnostic]:
    """Analyze protocol ``source`` (attributed to ``path``) and return diagnostics.

    The empty list means "no problems found". ``path`` is used for the module
    name, source-line attribution, and the ``file`` field on each diagnostic.
    """
    path = Path(path)
    wt, build_diag = _load_worktable(source, path)
    if build_diag is not None:
        diagnostics = [build_diag]
    elif wt is None:
        diagnostics = [
            Diagnostic(
                line=1,
                severity="error",
                code="python_build_failure",
                message=f"{path.name}: expected a build_worktable() factory or top-level `wt`.",
                source="build",
                file=str(path),
                hint=resolve_repair_policy(category="python_build_failure").guidance,
            )
        ]
    else:
        diagnostics = _simulate_diagnostics(wt, path)

    lines = source.splitlines()
    for d in diagnostics:
        d.fixes = compute_fixes(d.code, d.line, d.source, lines)
    return diagnostics


# ── module loading ─────────────────────────────────────────────────


def _load_worktable(source: str, path: Path) -> tuple[Optional[Any], Optional[Diagnostic]]:
    """Exec ``source`` and return ``(worktable, None)`` or ``(None, diagnostic)``.

    Any exception raised while importing or running ``build_worktable()`` becomes
    a single build diagnostic, positioned at the offending line in ``path`` when
    it can be recovered from the traceback.
    """
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(path.stem, loader=None)
    )
    module.__file__ = str(path)
    code_obj_error = _compile(source, path)
    if isinstance(code_obj_error, Diagnostic):
        return None, code_obj_error

    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        exec(code_obj_error, module.__dict__)  # noqa: S102 - user runs their own file
        factory = getattr(module, "build_worktable", None)
        if callable(factory):
            return factory(), None
        existing = getattr(module, "wt", None)
        return existing, None
    except Exception as exc:  # noqa: BLE001 - surfaced as a diagnostic
        return None, _build_error_diagnostic(exc, path)
    finally:
        sys.dont_write_bytecode = original


def _compile(source: str, path: Path) -> Any:
    """Compile ``source``; return the code object or a SyntaxError diagnostic."""
    try:
        return compile(source, str(path), "exec")
    except SyntaxError as exc:
        return Diagnostic(
            line=exc.lineno or 1,
            severity="error",
            code="syntax_error",
            message=exc.msg or "Syntax error.",
            source="build",
            file=str(path),
            col=exc.offset,
            hint="Fix the Python syntax error before the protocol can be analyzed.",
        )


def _build_error_diagnostic(exc: Exception, path: Path) -> Diagnostic:
    """Map an exception raised during build to a positioned diagnostic."""
    line = _line_in_file(exc, path) or 1
    category = _classify_build_error(exc)
    policy = resolve_repair_policy(category=category, message=str(exc))
    return Diagnostic(
        line=line,
        severity="error",
        code=category,
        message=f"{type(exc).__name__}: {exc}",
        source="build",
        file=str(path),
        hint=policy.guidance,
        repair_options=list(policy.options),
    )


def _line_in_file(exc: Exception, path: Path) -> Optional[int]:
    """Last traceback line that lives in the user's protocol file."""
    target = path.resolve()
    line: Optional[int] = None
    for frame, lineno in traceback.walk_tb(exc.__traceback__):
        try:
            if Path(frame.f_code.co_filename).resolve() == target:
                line = lineno
        except (OSError, ValueError):
            continue
    return line


def _classify_build_error(exc: Exception) -> str:
    if isinstance(exc, AttributeError):
        return "missing_method"
    if isinstance(exc, (ImportError, NameError)):
        return "python_build_failure"
    return "python_build_failure"


# ── simulation ─────────────────────────────────────────────────────


def _simulate_diagnostics(wt: Any, path: Path) -> list[Diagnostic]:
    """Run the simulator and turn a failure into a positioned diagnostic."""
    try:
        wt.simulate(strict=False)
    except Exception as exc:  # noqa: BLE001 - surfaced as a diagnostic
        report = getattr(wt, "simulation_report", None)
        failure = getattr(report, "failure", None)
        if failure is None:
            return [
                Diagnostic(
                    line=1,
                    severity="error",
                    code="simulation_state",
                    message=str(exc),
                    source="simulate",
                    file=str(path),
                )
            ]
        policy = resolve_repair_policy(category=failure.category, message=failure.message)
        sp = failure.source_pos or {}
        return [
            Diagnostic(
                line=int(sp.get("line") or 1),
                severity="error",
                code=failure.category,
                message=failure.message,
                source="simulate",
                file=str(sp.get("file") or path),
                end_line=sp.get("end_line"),
                col=sp.get("col"),
                hint=policy.guidance,
                repair_options=list(failure.repair_options or policy.options),
            )
        ]
    return []
