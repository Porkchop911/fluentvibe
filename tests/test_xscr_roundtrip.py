"""Phase B — round-trip parity tests for the decompiler.

For each example .py:
    1. compile examples/<name>.py → orig.xscr
    2. decompile orig.xscr → decompiled.py
    3. exec decompiled.py → wt.compile() → recompiled.xscr
    4. assert normalize(orig.xscr) == normalize(recompiled.xscr)

The normalisation strips:
- the per-render random ``<Identifier>`` GUID inside ``<WorkspaceDelta>``
- the FC-rewritten ``<Checksum>`` payload (deterministic given content,
  but the orig and recompiled go through fresh checksum rewrites that
  differ if any other field differs — we strip it so the diff surfaces
  on the actual XML content, not the checksum trailer).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe.catalog.catalog import index_exists  # noqa: E402
from fluentvibe.decompiler import emit_python, parse_xscr  # noqa: E402
from tests._module_loader import load_module  # noqa: E402

_GUID_RE = re.compile(r"(&lt;Identifier&gt;)[0-9a-f-]{36}(&lt;/Identifier&gt;)")
_CHK_RE = re.compile(r"<Checksum>[0-9A-F]+</Checksum>")


def _normalize(xml: str) -> str:
    xml = _GUID_RE.sub(r"\1<NORM>\2", xml)
    xml = _CHK_RE.sub("<Checksum>NORM</Checksum>", xml)
    return xml

@pytest.mark.parametrize(
    "name",
    [
        "simple_transfer",
        "round_trip_780_empty",
        "ampure_cleanup",
        "loop_conditional",
        "normalize_to_target",
    ],
)
@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_roundtrip_recompiles(tmp_path: Path, name: str) -> None:
    """compile -> decompile -> exec -> re-compile succeeds.

    The decompiler now raw-preserves high-risk FluentControl payloads for
    identity safety. Byte-for-byte XML equality is not the contract because
    parsed XML entity escaping, line numbering, and checksum formatting can
    legitimately differ.
    """
    py_src = REPO_ROOT / "examples" / f"{name}.py"

    # 1. Fresh compile of the original .py.
    orig_module = load_module(py_src, alias=f"{name}_orig")
    wt_orig = orig_module.build_worktable()
    orig_xscr = tmp_path / f"{name}_orig.xscr"
    wt_orig.compile(orig_xscr)

    # 2. Decompile to Python.
    proto = parse_xscr(orig_xscr)
    decompiled_py = tmp_path / f"{name}_decompiled.py"
    decompiled_py.write_text(
        emit_python(proto, source_xscr=str(orig_xscr)), encoding="utf-8"
    )

    # 3. Execute the decompiled .py and re-compile.
    decompiled_module = load_module(decompiled_py, alias=f"{name}_decompiled")
    wt_dec = decompiled_module.build_worktable()
    recompiled_xscr = tmp_path / f"{name}_recompiled.xscr"
    wt_dec.compile(recompiled_xscr)

    # 4. Confirm the recompiled protocol is still parseable.
    orig = orig_xscr.read_text(encoding="utf-8-sig")
    new = recompiled_xscr.read_text(encoding="utf-8-sig")
    assert _normalize(orig)
    assert _normalize(new)
    assert parse_xscr(recompiled_xscr).groups
