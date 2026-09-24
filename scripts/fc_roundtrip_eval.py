"""Can a model carry FluentControl edits back into its Python draft?

Given a draft, the script it compiled to (base) and the script after someone
edited it in FluentControl (edited), give the model the draft plus the list of
edits (``fc_roundtrip.roundtrip_message``), let it revise the draft through the
normal authoring tools, then compile its result and diff it against the edited
script. Zero remaining differences means every edit made it into the Python.

    python scripts/fc_roundtrip_eval.py draft.py --base draft.fc-base.xscr \\
        --edited draft.fc-edited.xscr --profile build/workspaces/sat_1080_test --out <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", type=Path)
    ap.add_argument("--base", type=Path, required=True)
    ap.add_argument("--edited", type=Path, required=True)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True, help="run folder (created)")
    ap.add_argument("--run-timeout", type=float, default=2400.0)
    args = ap.parse_args()

    from eval_authoring import _activate_profile

    from fluentvibe.authoring import PromptAuthoringService
    from fluentvibe.authoring.eval_rubric import build_worktable_from_source
    from fluentvibe.authoring.fc_roundtrip import (
        diff_scripts,
        locate_variables,
        roundtrip_message,
        summarize,
    )
    from fluentvibe.authoring.trace import ModelTraceConfig

    ws_name, ws_guid = _activate_profile(args.profile)
    draft = args.draft.read_text(encoding="utf-8")
    wt = build_worktable_from_source(draft, str(args.draft.resolve()))
    changes = locate_variables(diff_scripts(args.base, args.edited, wt=wt), draft)
    print(f"[roundtrip] {len(changes)} change(s) to carry over:")
    for line in summarize(changes):
        print("   ", line)
    prompt = (
        "Here is the current protocol draft (Python):\n\n```python\n" + draft + "\n```\n\n"
        + roundtrip_message(changes)
        + "\n\nKeep everything else as it is. Declare the workflow from the draft's existing groups, "
        "submit the revised draft with simulate_python_draft, and finish with compile_and_simulate."
    )
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = PromptAuthoringService(request_timeout_s=1800.0).author(
        prompt,
        output_dir=args.out,
        workspace_name=ws_name,
        workspace_guid=ws_guid,
        lab_scope="skills",
        retry_budget=3,
        trace_config=ModelTraceConfig(enabled=True, live=False, output_dir=args.out, session_id="roundtrip"),
        run_timeout_s=args.run_timeout,
    )
    code = result.best_draft_code or result.generated_code
    report = {"status": result.status.value, "minutes": round((time.monotonic() - started) / 60, 1)}
    if code:
        revised = args.out / "revised.py"
        revised.write_text(code, encoding="utf-8")
        new_wt = build_worktable_from_source(code, str(revised.resolve()))
        compiled = args.out / "revised.xscr"
        new_wt.compile(compiled)
        remaining = locate_variables(diff_scripts(compiled, args.edited, wt=new_wt), code)
        # Derived block values are recomputed from the carried-over arguments;
        # FluentControl does not update them when an input variable is edited.
        derived = [c for c in remaining if c.kind == "variable" and c.argument == "derived"]
        missed = [c for c in remaining if c not in derived]
        report.update(remaining=len(missed), remaining_changes=summarize(missed),
                      recomputed_derived=summarize(derived))
    else:
        report.update(remaining=None, remaining_changes=["no draft produced"])
    (args.out / "roundtrip_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
