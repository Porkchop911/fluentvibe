"""Copilot inline edit: LLM rewrite of a region, re-validated by the analyzer."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.copilot.edit import _build_messages, edit_region  # noqa: E402

_GOOD = '''\
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable(name="Edit")
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


def _aspirate_line() -> int:
    return next(i for i, t in enumerate(_GOOD.splitlines(), start=1) if "head.aspirate(" in t)


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def complete(self, *, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return {"content": self.content, "role": "assistant"}


def test_edit_strips_code_fences() -> None:
    line = _aspirate_line()
    client = _FakeClient("```python\n    head.aspirate(src, 10.0, liquid_class=\"Water Free Single\")\n```")
    result = edit_region(_GOOD, line, line, "halve the volume", client=client, revalidate=False)
    assert result.new_text == '    head.aspirate(src, 10.0, liquid_class="Water Free Single")'


def test_build_messages_contains_file_selection_instruction() -> None:
    line = _aspirate_line()
    selection = _GOOD.splitlines()[line - 1]
    messages = _build_messages(_GOOD, selection, "use 10 uL")
    user = messages[1]["content"]
    assert "use 10 uL" in user
    assert "def build_worktable()" in user  # full file for context
    assert "head.aspirate(" in user  # the selection


def test_revalidation_clean_when_edit_is_fine() -> None:
    line = _aspirate_line()
    same = _GOOD.splitlines()[line - 1]
    result = edit_region(_GOOD, line, line, "no-op", client=_FakeClient(same), path="p.py")
    assert result.diagnostics == []
    assert result.introduces_errors is False


def test_revalidation_flags_an_edit_that_overdraws() -> None:
    line = _aspirate_line()
    # 200 uL from a well filled with only 50 uL -> simulator failure.
    bad = '    head.aspirate(src, 200.0, liquid_class="Water Free Single")'
    result = edit_region(_GOOD, line, line, "aspirate 200", client=_FakeClient(bad), path="p.py")
    assert result.introduces_errors is True
    assert any(d["severity"] == "error" for d in result.diagnostics)


def test_cli_edit_prints_replacement(tmp_path, monkeypatch, capsys) -> None:
    import fluentvibe.copilot as copilot
    from fluentvibe import cli
    from fluentvibe.copilot.edit import EditResult

    path = tmp_path / "p.py"
    path.write_text(_GOOD, encoding="utf-8")
    line = _aspirate_line()
    monkeypatch.setattr(
        copilot,
        "edit_region",
        lambda *a, **k: EditResult(new_text="    head.aspirate(src, 5.0)", start_line=line, end_line=line),
    )
    monkeypatch.setattr(
        sys, "argv",
        ["fluentvibe", "edit", str(path), "--start", str(line), "--end", str(line), "-m", "x"],
    )
    rc = cli.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "head.aspirate(src, 5.0)" in out
