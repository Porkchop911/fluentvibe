"""Copilot Phase 1: the headless analyzer turns protocol source into diagnostics.

Exercises the editor-agnostic core that `fluentvibe check` and a future VS Code
language server both call. See docs/copilot-design.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.copilot import analyze_file, analyze_source  # noqa: E402

_GOOD = '''\
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable(name="Good")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    src.fill_all(Reagent("Buffer"), 50.0)

    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    head.dispense(dst, 20.0, liquid_class="Water Free Single")
    return wt
'''

# Same as _GOOD but the source well only holds 5 uL, so the 20 uL aspirate fails.
_INSUFFICIENT = _GOOD.replace(
    'src.fill_all(Reagent("Buffer"), 50.0)',
    'src.fill_all(Reagent("Buffer"), 5.0)',
)

_SYNTAX_ERROR = "def build_worktable(:\n    pass\n"

_UNKNOWN_METHOD = '''\
from fluentvibe import Worktable, Plate96


def build_worktable() -> Worktable:
    wt = Worktable(name="Oops")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    src.nonexistent_method()
    return wt
'''

_NO_FACTORY = "x = 1\n"


def _line_of(source: str, needle: str) -> int:
    for i, text in enumerate(source.splitlines(), start=1):
        if needle in text:
            return i
    raise AssertionError(f"{needle!r} not found in source")


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_clean_protocol_has_no_diagnostics(tmp_path: Path) -> None:
    path = _write(tmp_path, "good.py", _GOOD)
    assert analyze_file(path) == []


def test_insufficient_volume_reports_error_at_aspirate_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "short.py", _INSUFFICIENT)
    diags = analyze_file(path)
    assert len(diags) == 1
    d = diags[0]
    assert d.severity == "error"
    assert d.source == "simulate"
    assert d.code == "source_volume_short"
    assert d.line == _line_of(_INSUFFICIENT, "head.aspirate(")
    assert "source well" in d.hint.lower()
    assert Path(d.file).resolve() == path.resolve()


def test_syntax_error_is_reported_at_its_line(tmp_path: Path) -> None:
    path = _write(tmp_path, "syntax.py", _SYNTAX_ERROR)
    diags = analyze_file(path)
    assert len(diags) == 1
    assert diags[0].code == "syntax_error"
    assert diags[0].source == "build"
    assert diags[0].line == 1


def test_unknown_method_is_reported_as_build_error(tmp_path: Path) -> None:
    path = _write(tmp_path, "attr.py", _UNKNOWN_METHOD)
    diags = analyze_file(path)
    assert len(diags) == 1
    d = diags[0]
    assert d.source == "build"
    assert d.code == "missing_method"
    assert d.line == _line_of(_UNKNOWN_METHOD, "nonexistent_method")
    assert d.hint  # repair policy guidance is attached


def test_missing_factory_is_reported(tmp_path: Path) -> None:
    path = _write(tmp_path, "nofactory.py", _NO_FACTORY)
    diags = analyze_file(path)
    assert len(diags) == 1
    assert diags[0].code == "python_build_failure"


def test_analyze_source_matches_analyze_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "short.py", _INSUFFICIENT)
    from_source = analyze_source(_INSUFFICIENT, path)
    from_file = analyze_file(path)
    assert [d.to_dict() for d in from_source] == [d.to_dict() for d in from_file]
