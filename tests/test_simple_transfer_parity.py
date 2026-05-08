"""v1 acceptance: byte-equal parity render against a fluentdsl reference.

Both pipelines must produce identical Protocol IR. The vendored renderer is
the same code in both repos, so identical IR → identical XML, modulo one
unavoidable random GUID (`workspace_delta_guid` is `uuid.uuid4()` inside the
renderer — two runs of *fluentdsl on its own input* also differ on it).
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FLUENTDSL_ROOT = REPO_ROOT.parent / "fluentdsl"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "simple_transfer_fluentdsl_reference.py"

sys.path.insert(0, str(REPO_ROOT))
from tecanlab.catalog.catalog import index_exists  # noqa: E402

_WORKSPACE_GUID_RE = re.compile(
    r"&lt;Identifier&gt;[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}&lt;/Identifier&gt;"
)
_WORKTABLE_REF_RE = re.compile(
    r"(<Reference>\s*<Guid>)[^<]+(</Guid>\s*<TypeId>WorktableWorkspace</TypeId>\s*<ObjectName>)[^<]+(</ObjectName>\s*</Reference>)"
)
_BASE_WORKSPACE_RE = re.compile(
    r"(<BaseWorkspaceName>)[^<]+(</BaseWorkspaceName>)"
)


def _normalize(xml: str) -> str:
    """Collapse environment-specific workspace metadata for parity comparison."""
    xml = _WORKSPACE_GUID_RE.sub(
        "&lt;Identifier&gt;<NORMALIZED>&lt;/Identifier&gt;",
        xml,
    )
    xml = _WORKTABLE_REF_RE.sub(
        r"\1<NORMALIZED>\2<NORMALIZED>\3",
        xml,
    )
    xml = _BASE_WORKSPACE_RE.sub(r"\1<NORMALIZED>\2", xml)
    return xml


def _render_via_fluentdsl(source_path: Path) -> str:
    """Render a fluentdsl protocol script to its `.xscr` XML string.

    Run in a subprocess so fluentdsl's package state can't leak into the
    tecanlab import graph (both packages register top-level names like
    `examples.simple_transfer`).
    """
    code = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, r"{FLUENTDSL_ROOT}")
        from fluentdsl.compiler import compile_dsl_file
        _, xml = compile_dsl_file(r"{source_path}")
        sys.stdout.write(xml)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return result.stdout


def _render_via_tecanlab() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    from examples.simple_transfer import build_worktable
    from tecanlab.compiler import render_protocol

    wt = build_worktable()
    proto = wt.to_protocol()
    return render_protocol(proto)


def test_simple_transfer_parity_xml() -> None:
    """tecanlab and fluentdsl render identical XML for the same protocol."""
    if not FLUENTDSL_ROOT.exists():
        pytest.skip("fluentdsl reference repo not available next to tecanlab")
    if not FIXTURE.exists():
        pytest.fail(f"missing fixture: {FIXTURE}")
    if not index_exists():
        pytest.skip("catalog index empty")

    fluent_xml = _render_via_fluentdsl(FIXTURE)
    tecan_xml = _render_via_tecanlab()

    assert _normalize(tecan_xml) == _normalize(fluent_xml), (
        "tecanlab and fluentdsl rendered different XML "
        f"(tecanlab len={len(tecan_xml)}, fluentdsl len={len(fluent_xml)})"
    )


def test_simple_transfer_protocol_ir_shape() -> None:
    """Sanity: the protocol has the right groups and step types."""
    if not index_exists():
        pytest.skip("catalog index empty")
    from examples.simple_transfer import build_worktable

    wt = build_worktable()
    proto = wt.to_protocol()
    assert proto.name == "Simple transfer"
    assert [g.name for g in proto.groups] == ["Setup", "Transfer"]
    setup_types = [type(s).__name__ for s in proto.groups[0].steps]
    transfer_types = [type(s).__name__ for s in proto.groups[1].steps]
    assert setup_types == ["AddLabwareStep"] * 3
    assert transfer_types == [
        "GetHeadAdapterStep",
        "PickUpTipsStep",
        "AspirateStep",
        "DispenseStep",
        "SetTipsBackStep",
        "DropHeadAdapterStep",
    ]
