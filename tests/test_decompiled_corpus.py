from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tecanlab.catalog.catalog import index_exists  # noqa: E402
from tecanlab.catalog.indexer import build_index, install_path_default  # noqa: E402
from tecanlab.decompiler import run_decompiled_corpus, summarize_corpus_results  # noqa: E402


CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "decompiled_corpus"
_PRODUCTION_XSCR_ENV = os.environ.get("TECANLAB_PRODUCTION_XSCR")
PRODUCTION_XSCR = Path(_PRODUCTION_XSCR_ENV) if _PRODUCTION_XSCR_ENV else None


def _install_present() -> bool:
    return (install_path_default() / "SystemSpecific" / "Worktable" / "Components").exists()


@pytest.fixture(scope="module", autouse=True)
def _refresh_index_for_real_install() -> None:
    if _install_present():
        build_index()


@pytest.mark.skipif(not index_exists(), reason="catalog index empty")
def test_decompiled_corpus_classifies_strict_outcomes(tmp_path: Path) -> None:
    corpus_paths = [
        CORPUS_DIR / "liha_selected_transfer.xscr",
        CORPUS_DIR / "catalog_gap.xscr",
        CORPUS_DIR / "unsupported_runtime_step.xscr",
        CORPUS_DIR / "liquid_overdraw.xscr",
    ]

    results = run_decompiled_corpus(corpus_paths, output_dir=tmp_path)
    summary = summarize_corpus_results(results)

    by_name = {result["name"]: result for result in summary["protocols"]}

    assert summary["classification_counts"] == {
        "liquid_semantics": 1,
        "passes_strictly": 1,
        "unsupported_command": 1,
        "workspace_or_catalog": 1,
    }
    assert summary["status_counts"] == {"failed": 3, "passed": 1}

    passed = by_name["liha_selected_transfer"]
    assert passed["status"] == "passed"
    assert passed["classification"] == "passes_strictly"
    assert passed["failure"] is None
    assert passed["unsupported_command_ids"] == {}
    assert passed["modeled_coverage"] == pytest.approx(1.0)
    assert passed["generated_python"] is not None

    catalog = by_name["catalog_gap"]
    assert catalog["status"] == "failed"
    assert catalog["classification"] == "workspace_or_catalog"
    assert catalog["failure"]["category"] == "catalog"
    assert "Custom Plate Not In Index" in catalog["failure"]["message"]

    unsupported = by_name["unsupported_runtime_step"]
    assert unsupported["status"] == "failed"
    assert unsupported["classification"] == "unsupported_command"
    assert unsupported["failure"]["category"] == "opaque_policy"
    assert unsupported["unsupported_command_ids"] == {"execute_application": 1}

    liquid = by_name["liquid_overdraw"]
    assert liquid["status"] == "failed"
    assert liquid["classification"] == "liquid_semantics"
    assert liquid["failure"]["category"] in {"liquid_state", "source_volume_short"}
    assert liquid["total_executed_steps"] == 2


@pytest.mark.skipif(
    PRODUCTION_XSCR is None or not PRODUCTION_XSCR.exists(),
    reason="production corpus fixture not configured",
)
def test_production_corpus_reclassifies_to_next_catalog_issue(tmp_path: Path) -> None:
    results = run_decompiled_corpus([PRODUCTION_XSCR], output_dir=tmp_path)
    summary = summarize_corpus_results(results)
    assert summary["classification_counts"] == {"workspace_or_catalog": 1}
    assert summary["status_counts"] == {"failed": 1}

    result = summary["protocols"][0]
    assert result["generated_python"] is not None
    assert result["classification"] == "workspace_or_catalog"
    assert result["failure"]["category"] == "catalog"
    assert "TroughMP_1" not in result["failure"]["message"]
    assert "25ml_short_1" in result["failure"]["message"]
    assert "not installed in the local tecanlab catalog index" in result["failure"]["message"]
