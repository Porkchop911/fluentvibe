"""Copilot Phase 4: LLM 'explain this error' (offline, with an injected client)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.copilot.explain import build_messages, explain_diagnostic  # noqa: E402

_SOURCE = '''\
def build_worktable():
    wt = Worktable(name="x")
    head = wt.mca96
    head.pick_up(tips)
    head.aspirate(src, 20.0)
    head.dispense(dst, 20.0)
    return wt
'''

_DIAG = {
    "code": "source_volume_short",
    "severity": "error",
    "message": "Aspirate: well 'A1' on 'Source' short by 15.00 uL",
    "hint": "Increase the initial fill volume on the source labware.",
    "line": 5,
}


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def complete(self, *, messages, tools):
        self.calls.append({"messages": messages, "tools": tools})
        return {"content": self.content, "role": "assistant"}


def test_explain_returns_client_content() -> None:
    client = _FakeClient("You aspirate 20 uL but the source only holds 5 uL.")
    out = explain_diagnostic(_DIAG, _SOURCE, client=client)
    assert out == "You aspirate 20 uL but the source only holds 5 uL."


def test_explain_calls_client_without_tools() -> None:
    client = _FakeClient("ok")
    explain_diagnostic(_DIAG, _SOURCE, client=client)
    assert len(client.calls) == 1
    assert client.calls[0]["tools"] == []


def test_build_messages_grounds_on_failure_and_snippet() -> None:
    messages = build_messages(_DIAG, _SOURCE)
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "source_volume_short" in user
    assert "short by 15.00 uL" in user
    assert "Increase the initial fill volume" in user
    # The failing line (5) is included and marked.
    assert ">>    5 | " in user
    assert "head.aspirate(src, 20.0)" in user


def test_explain_strips_whitespace_and_handles_empty() -> None:
    assert explain_diagnostic(_DIAG, _SOURCE, client=_FakeClient("  spaced  ")) == "spaced"
    assert explain_diagnostic(_DIAG, _SOURCE, client=_FakeClient("")) == ""


_BAD_PROTOCOL = '''\
from fluentvibe import Worktable, Reagent, Plate96, MCA100Box


def build_worktable() -> Worktable:
    wt = Worktable(name="Short")
    wt.group("Setup")
    src = wt.place(Plate96("Source", catalog="96 Well Flat"), "Nest", 1)
    dst = wt.place(Plate96("Dest", catalog="96 Well Flat"), "Nest", 2)
    tips = wt.place(MCA100Box("Tips", catalog="MCA96, 100ul, Box"), "Nest", 4)
    src.fill_all(Reagent("Buffer"), 5.0)

    wt.group("Transfer")
    head = wt.mca96
    head.mount_adapter()
    head.pick_up(tips)
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    return wt
'''


def test_cli_check_explain_integrates(tmp_path, monkeypatch, capsys) -> None:
    import fluentvibe.copilot as copilot
    from fluentvibe import cli

    monkeypatch.setattr(
        copilot, "explain_diagnostic", lambda d, s, client=None: "FAKE EXPLANATION"
    )
    path = tmp_path / "bad.py"
    path.write_text(_BAD_PROTOCOL, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["fluentvibe", "check", str(path), "--explain"])

    rc = cli.main()

    captured = capsys.readouterr()
    assert rc == 1
    assert "FAKE EXPLANATION" in captured.err
