"""fluentvibe CLI.

Subcommands:

- `fluentvibe compile <protocol.py>`   — execute the script and write `.xscr`.
- `fluentvibe simulate <protocol.py>`  — run the simulator, print snapshot summary.
- `fluentvibe decompile <file.xscr>`   — emit a fluentvibe Python protocol from a .xscr.
- `fluentvibe catalog refresh [...]`   — rebuild the SQL catalog index.
- `fluentvibe catalog info`            — show install path, fingerprint, counts.
- `fluentvibe catalog find <pattern>`  — substring-search components by name.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# ── Entry point ────────────────────────────────────────────────────


def _force_utf8_streams() -> None:
    """Model-authored text routinely contains →, µ, — etc. On Windows the
    console defaults to cp1252 and `print()` raises UnicodeEncodeError,
    crashing the chat loop at the approval gate. Reconfigure stdout/stderr
    to UTF-8 with replacement so output never crashes the process.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: Optional[list[str]] = None) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(prog="fluentvibe", description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_compile = sub.add_parser("compile", help="render a protocol .py to .xscr")
    p_compile.add_argument("input", type=Path)
    p_compile.add_argument("--output", "-o", type=Path, default=None)
    p_compile.set_defaults(func=_cmd_compile)

    p_simulate = sub.add_parser("simulate", help="run the simulator and print snapshot summary")
    p_simulate.add_argument("input", type=Path)
    p_simulate.add_argument("--json", dest="as_json", action="store_true",
                            help="emit a JSON summary of all snapshots")
    p_simulate.add_argument("--coverage", action="store_true",
                            help="include simulator coverage in text output")
    p_simulate.add_argument("--fail-on-opaque", action="store_true",
                            help="exit 1 if any GenericStep/raw XML command is opaque")
    p_simulate.add_argument("--min-coverage", type=float, default=None,
                            help="exit 1 if modeled simulator coverage is below this fraction")
    p_simulate.add_argument("--strict", action="store_true",
                            help="require a bound workspace plus strict slot/catalog semantics")
    p_simulate.set_defaults(func=_cmd_simulate)

    p_check = sub.add_parser(
        "check",
        help="analyze a protocol .py and report diagnostics (build + simulate)",
    )
    p_check.add_argument("input", type=Path)
    p_check.add_argument("--json", dest="as_json", action="store_true",
                         help="emit diagnostics as JSON")
    p_check.add_argument("--source-doc", type=Path, default=None,
                         help="compare the protocol against a source PDF/text document")
    p_check.add_argument("--explain", action="store_true",
                         help="add a plain-language LLM explanation per diagnostic "
                              "(needs a reachable LM endpoint; see FLUENTVIBE_LM_ENDPOINT)")
    p_check.add_argument("--endpoint", default=None,
                         help="LM endpoint for --explain (default: env FLUENTVIBE_LM_ENDPOINT)")
    p_check.add_argument("--model", default=None,
                         help="model name for --explain (default: env FLUENTVIBE_LM_MODEL)")
    p_check.set_defaults(func=_cmd_check)

    p_complete = sub.add_parser(
        "complete",
        help="list authoring completions at a position (catalog names + API)",
    )
    p_complete.add_argument("input", type=Path)
    p_complete.add_argument("--line", type=int, required=True, help="1-based line")
    p_complete.add_argument("--col", type=int, required=True, help="1-based column")
    p_complete.add_argument("--json", dest="as_json", action="store_true",
                            help="emit completions as JSON")
    p_complete.set_defaults(func=_cmd_complete)

    p_edit = sub.add_parser(
        "edit",
        help="rewrite a line range with an instruction (LLM), re-validated",
    )
    p_edit.add_argument("input", type=Path)
    p_edit.add_argument("--start", type=int, required=True, help="1-based start line (inclusive)")
    p_edit.add_argument("--end", type=int, required=True, help="1-based end line (inclusive)")
    p_edit.add_argument("--instruction", "-m", required=True, help="what to change")
    p_edit.add_argument("--apply", action="store_true", help="write the edit back to the file")
    p_edit.add_argument("--no-validate", dest="validate", action="store_false",
                        help="skip re-validating the edited file")
    p_edit.add_argument("--json", dest="as_json", action="store_true")
    p_edit.add_argument("--endpoint", default=None,
                        help="LM endpoint (default: env FLUENTVIBE_LM_ENDPOINT)")
    p_edit.add_argument("--model", default=None,
                        help="model name (default: env FLUENTVIBE_LM_MODEL)")
    p_edit.set_defaults(func=_cmd_edit)

    p_lsp = sub.add_parser(
        "lsp",
        help="start the fluentvibe language server over stdio (for editors)",
    )
    # LSP clients (vscode-languageclient) append a transport flag to the launch
    # command. We always speak stdio, so accept and ignore the standard ones.
    p_lsp.add_argument("--stdio", action="store_true", help=argparse.SUPPRESS)
    p_lsp.add_argument("--node-ipc", action="store_true", help=argparse.SUPPRESS)
    p_lsp.add_argument("--socket", default=None, help=argparse.SUPPRESS)
    p_lsp.add_argument("--pipe", default=None, help=argparse.SUPPRESS)
    p_lsp.add_argument("--clientProcessId", default=None, help=argparse.SUPPRESS)
    p_lsp.set_defaults(func=_cmd_lsp)

    p_decompile = sub.add_parser(
        "decompile",
        help="parse a .xscr and emit a fluentvibe Python protocol",
    )
    p_decompile.add_argument("input", type=Path)
    p_decompile.add_argument("--output", "-o", type=Path, default=None,
                             help="output .py path (defaults to <input>.py)")
    p_decompile.add_argument("--strict", action="store_true",
                             help="exit 1 if any step decoded as GenericStep")
    p_decompile.set_defaults(func=_cmd_decompile)

    p_author = sub.add_parser("author", help="author a protocol from a free-text lab-task prompt")
    p_author.add_argument("prompt", nargs="+", help="free-text authoring prompt")
    p_author.add_argument("--output-dir", type=Path, default=Path("build") / "authored")
    p_author.add_argument("--retry-budget", type=int, default=2)
    p_author.add_argument("--workspace", default=None)
    p_author.add_argument("--workspace-guid", default=None)
    p_author.add_argument("--profile", type=Path, default=None,
                          help="workspace-app profile dir (build/workspaces/<name>); "
                               "drives the workspace, grounding snapshot, deck skill, "
                               "and labware/liquid whitelist for this run")
    p_author.add_argument("--endpoint", default=None,
                          help="OpenAI-compatible chat endpoint (default: "
                               "env FLUENTVIBE_LM_ENDPOINT or http://localhost:18020/v1/chat/completions)")
    p_author.add_argument("--model", default=None,
                          help="model name (default: env FLUENTVIBE_LM_MODEL)")
    p_author.add_argument("--request-timeout", type=float, default=None,
                          help="maximum seconds for one model response (default: 240 or "
                               "FLUENTVIBE_LM_TIMEOUT_S)")
    p_author.add_argument("--run-timeout", type=float, default=None,
                          help="maximum seconds across all model calls in this run")
    p_author.add_argument("--json", dest="as_json", action="store_true",
                          help="emit a JSON summary of the authoring result")
    p_author.add_argument("--document", type=Path, default=None,
                          help="protocol document (PDF, DOCX, text) attached to the prompt")
    p_author.add_argument("--check-instructions", action="store_true",
                          help="extract the prompt's instructions and the document's steps (separate model "
                               "calls, in parallel) and check them on the authored protocol "
                               "(<output-dir>/requirements.md, result.json)")
    p_author.add_argument("--model-trace", action="store_true",
                          help="write model request traces under <output-dir>/model_traces")
    p_author.add_argument("--model-trace-live", action="store_true",
                          help="also print compact model trace events to stderr")
    p_author.add_argument("--lab-scope", choices=["off", "cheatsheet", "enforce", "skills"],
                          default=None,
                          help="narrowed-scope experiment: inject curated lab "
                               "cheatsheet (cheatsheet), restrict labware tools "
                               "to the whitelist (enforce), or inject an "
                               "LM-selected subset of granular skill files "
                               "(skills); default off = baseline "
                               "(env FLUENTVIBE_LAB_SCOPE)")
    p_author.add_argument("--spec", type=Path, default=None,
                          help="approved Bench Spec JSON (from `fluentvibe spec`) to author against")
    p_author.set_defaults(func=_cmd_author)

    p_lookup_eval = sub.add_parser(
        "lookup-eval",
        help="evaluate SQL-backed lookup behavior through the authoring tool loop",
    )
    p_lookup_eval.add_argument("--output", type=Path, default=Path("build") / "lookup_eval.json")
    p_lookup_eval.add_argument("--live", action="store_true",
                               help="use the configured OpenAI-compatible model instead of scripted responses")
    p_lookup_eval.add_argument("--model", default=None)
    p_lookup_eval.add_argument("--endpoint", default=None)
    p_lookup_eval.set_defaults(func=_cmd_lookup_eval)

    p_render_trace = sub.add_parser(
        "render-trace",
        help="render a model trace JSONL file into a readable Markdown summary",
    )
    p_render_trace.add_argument("input", type=Path)
    p_render_trace.add_argument("--output", "-o", type=Path, default=None)
    p_render_trace.set_defaults(func=_cmd_render_trace)

    p_spec = sub.add_parser(
        "spec",
        help="extract a Bench Spec (what the protocol does, step by step) from a document",
    )
    p_spec.add_argument("document", type=Path, help="protocol document (PDF, DOCX, text)")
    p_spec.add_argument("--output", "-o", type=Path, default=None,
                        help="write the spec JSON here (a .md review table is written beside it)")
    p_spec.add_argument("--endpoint", default=None)
    p_spec.add_argument("--model", default=None)
    p_spec.add_argument("--request-timeout", type=float, default=None)
    p_spec.add_argument("--no-examples", dest="examples", action="store_false",
                        help="do not show the model outlines of similar corpus protocols")
    p_spec.set_defaults(func=_cmd_spec)

    p_skeleton = sub.add_parser(
        "skeleton",
        help="write a first protocol draft from an approved Bench Spec and a deck profile",
    )
    p_skeleton.add_argument("spec", type=Path, help="Bench Spec JSON")
    p_skeleton.add_argument("--profile", type=Path, required=True,
                            help="workspace-app profile dir (build/workspaces/<name>)")
    p_skeleton.add_argument("--output", "-o", type=Path, default=None)
    p_skeleton.set_defaults(func=_cmd_skeleton)

    p_author_spec = sub.add_parser(
        "author-spec",
        help="document -> Bench Spec -> protocol draft on a deck profile, asking about open values",
    )
    p_author_spec.add_argument("document", type=Path, help="protocol document (PDF, DOCX, text)")
    p_author_spec.add_argument("--profile", type=Path, required=True, help="workspace-app profile dir")
    p_author_spec.add_argument("--output", "-o", type=Path, required=True, help="directory for spec, draft, checks")
    p_author_spec.add_argument("--request", default=None, help="what to automate (scope, sample count)")
    p_author_spec.add_argument("--choose", action="store_true",
                               help="do not ask: the model chooses open values within the deck and kit limits")
    p_author_spec.add_argument("--fc-check", action="store_true", help="also check the draft in FluentControl")
    p_author_spec.add_argument("--check-instructions", action="store_true",
                               help="extract the request's instructions (separate model call) and check them on the protocol")
    p_author_spec.add_argument("--endpoint", default=None)
    p_author_spec.add_argument("--model", default=None)
    p_author_spec.add_argument("--request-timeout", type=float, default=1800.0,
                               help="seconds per model request (spec extraction reads the whole document)")
    p_author_spec.set_defaults(func=_cmd_author_spec)

    p_ot = sub.add_parser(
        "opentrons",
        help="convert an Opentrons protocol (.py or its folder) into a protocol for this deck",
    )
    p_ot.add_argument("protocol", type=Path, help="Opentrons protocol .py file or its folder")
    p_ot.add_argument("--profile", type=Path, required=True, help="workspace-app profile dir")
    p_ot.add_argument("--output", "-o", type=Path, required=True, help="directory for spec, draft, checks")
    p_ot.add_argument("--fc-check", action="store_true", help="also check the draft in FluentControl")
    p_ot.add_argument("--opentrons-python", type=Path, default=None,
                      help="python with the opentrons package (default: .venv-opentrons in the repo)")
    p_ot.set_defaults(func=_cmd_opentrons)

    p_req = sub.add_parser(
        "requirements",
        help="turn instructions into a checklist beside a protocol (<name>.requirements.json) and check it",
    )
    p_req.add_argument("draft", type=Path, help="fluentvibe Python protocol")
    p_req.add_argument("--request", default=None, help="the instructions (a model turns them into a checklist)")
    p_req.add_argument("--document", type=Path, default=None, help="protocol document the instructions refer to")
    p_req.add_argument("--profile", type=Path, default=None, help="workspace-app profile dir")
    p_req.set_defaults(func=_cmd_requirements)

    p_replay = sub.add_parser(
        "replay",
        help="write a standalone HTML replay of a protocol: the deck and every well after each step",
    )
    p_replay.add_argument("draft", type=Path, help="fluentvibe Python protocol")
    p_replay.add_argument("--output", "-o", type=Path, default=None, help="HTML file (default <draft>.replay.html)")
    p_replay.add_argument("--profile", type=Path, default=None, help="workspace-app profile dir")
    p_replay.set_defaults(func=_cmd_replay)

    p_fc_open = sub.add_parser(
        "fc-open",
        help="compile a draft and open it in FluentControl (shell script) for checking and editing",
    )
    p_fc_open.add_argument("draft", type=Path, help="fluentvibe Python protocol")
    p_fc_open.add_argument("--profile", type=Path, default=None, help="workspace-app profile dir (deck rules)")
    p_fc_open.add_argument("--json", action="store_true", help="print the InfoPad findings as JSON (for editors)")
    p_fc_open.set_defaults(func=_cmd_fc_open)

    p_fc_pull = sub.add_parser(
        "fc-pull",
        help="list what was changed in FluentControl since fc-open, with the Python lines to change",
    )
    p_fc_pull.add_argument("draft", type=Path, help="the draft passed to fc-open")
    p_fc_pull.add_argument("--base", type=Path, default=None, help="compiled script (default <draft>.fc-base.xscr)")
    p_fc_pull.add_argument("--edited", type=Path, default=None, help="script saved in FluentControl (default: the shell)")
    p_fc_pull.add_argument("--profile", type=Path, default=None)
    p_fc_pull.set_defaults(func=_cmd_fc_pull)

    p_chat = sub.add_parser("chat", help="start an interactive protocol-authoring chat")
    p_chat.add_argument("--output-dir", type=Path, default=Path("build") / "chat_authoring")
    p_chat.add_argument("--retry-budget", type=int, default=8)
    p_chat.add_argument("--workspace", default=None)
    p_chat.add_argument("--workspace-guid", default=None)
    p_chat.add_argument("--profile", type=Path, default=None,
                        help="workspace-app profile dir (build/workspaces/<name>); "
                             "drives the workspace, grounding snapshot, deck skill, "
                             "and labware/liquid whitelist for this run")
    p_chat.add_argument(
        "--fc-gate", action="store_true",
        help="after authoring success, run FluentControl-shell validation and "
             "deploy the .xscr into the FC datastore (requires FC closed)",
    )
    p_chat.add_argument(
        "--datastore-dir", type=Path, default=None,
        help="override the FluentControl UserSpecific directory for --fc-gate deploy",
    )
    p_chat.add_argument(
        "--allow-fc-running", action="store_true",
        help="skip the SystemSW.exe guard during --fc-gate deploy (test only)",
    )
    p_chat.add_argument(
        "--model", default=None,
        help="LM Studio model name (default: DEFAULT_LM_STUDIO_MODEL from lm_client.py)",
    )
    p_chat.add_argument("--request-timeout", type=float, default=None,
                        help="maximum seconds for one model response (default: 240 or "
                             "FLUENTVIBE_LM_TIMEOUT_S)")
    p_chat.add_argument("--model-trace", action="store_true",
                        help="write model request traces under <output-dir>/model_traces")
    p_chat.add_argument("--model-trace-live", action="store_true",
                        help="also print compact model trace events to stderr")
    p_chat.add_argument("--lab-scope", choices=["off", "cheatsheet", "enforce", "skills"],
                        default=None,
                        help="narrowed-scope experiment: inject curated lab "
                             "cheatsheet (cheatsheet), restrict labware tools "
                             "to the whitelist (enforce), or inject an "
                             "LM-selected subset of granular skill files "
                             "(skills); default off = baseline "
                             "(env FLUENTVIBE_LAB_SCOPE)")
    p_chat.set_defaults(func=_cmd_chat)

    p_workspace_app = sub.add_parser(
        "workspace-app",
        help="start the local workspace setup helper web UI",
    )
    p_workspace_app.add_argument("--host", default="127.0.0.1")
    p_workspace_app.add_argument("--port", type=int, default=8765)
    p_workspace_app.set_defaults(func=_cmd_workspace_app)

    p_deploy = sub.add_parser(
        "deploy",
        help="copy a compiled .xscr into the FluentControl UserSpecific datastore",
    )
    p_deploy.add_argument("xscr", type=Path)
    p_deploy.add_argument("--datastore-dir", type=Path, default=None,
                          help="override UserSpecific directory (default: production path)")
    p_deploy.add_argument("--object-name", default=None,
                          help="rewrite <ObjectName> before re-checksumming")
    p_deploy.add_argument("--keep-delta-id", action="store_true",
                          help="preserve the source's VxWorkspaceDelta identifier "
                               "(default: regenerate so old + new can coexist)")
    p_deploy.add_argument("--allow-fc-running", action="store_true",
                          help="skip the SystemSW.exe guard (use with care)")
    p_deploy.add_argument("--json", dest="as_json", action="store_true")
    p_deploy.set_defaults(func=_cmd_deploy)

    p_cat = sub.add_parser("catalog", help="catalog index management")
    cat_sub = p_cat.add_subparsers(dest="cat_cmd", required=True)

    p_refresh = cat_sub.add_parser("refresh", help="rebuild the SQL catalog index")
    p_refresh.add_argument("--install", type=Path, default=None,
                           help="FluentControl install path (defaults to env or built-in)")
    p_refresh.add_argument("--db", type=Path, default=None,
                           help="output index path (defaults to package's install_index.db)")
    p_refresh.set_defaults(func=_cmd_catalog_refresh)

    p_info = cat_sub.add_parser("info", help="print install path, fingerprint, category counts")
    p_info.set_defaults(func=_cmd_catalog_info)

    p_find = cat_sub.add_parser("find", help="substring search components by name")
    p_find.add_argument("pattern", help="substring to match (case-insensitive)")
    p_find.add_argument("--category", help="filter by category")
    p_find.set_defaults(func=_cmd_catalog_find)

    args = parser.parse_args(argv)
    from .authoring.lab_scope import LabScopeSetupError

    try:
        return args.func(args)
    except LabScopeSetupError as exc:
        print(f"Workspace not set up: {exc}", file=sys.stderr)
        return 3


# ── compile / simulate ─────────────────────────────────────────────


def _cmd_compile(args) -> int:
    wt = _load_protocol(args.input)
    output = args.output or args.input.with_suffix(".xscr")
    wt.compile(output)
    print(f"Compiled {wt.name} -> {output}")
    return 0


def _cmd_simulate(args) -> int:
    wt = _load_protocol(args.input)
    try:
        wt.simulate(
            fail_on_opaque=args.fail_on_opaque,
            min_coverage=args.min_coverage,
            strict=args.strict,
        )
    except Exception as exc:
        report = getattr(wt, "simulation_report", None)
        if args.as_json and report is not None:
            print(json.dumps(report.to_dict(), indent=2))
        if report is not None and report.failure is not None:
            print(
                f"Simulation failed [{report.failure.category}]: "
                f"{report.failure.message}",
                file=sys.stderr,
            )
        else:
            print(f"Simulation failed: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        report = wt.simulation_report
        out = report.to_dict() if report is not None else {}
        out["snapshots"] = [
            {
                "step_index": s.step_index,
                "step_type": type(s.step).__name__,
                "labware": [lw.label for stack in s.slot_map.values() for lw in stack],
                "mca_adapter": s.mca_adapter_label,
                "mca_tip_box": s.mca_tip_box_label,
                "mca_tip_volume_total_ul": sum(t.volume_ul for t in s.mca_tips),
                "liha_tip_volume_total_ul": sum(
                    t.volume_ul for t in s.liha_tips if t is not None
                ),
            }
            for s in wt.snapshots
        ]
        print(json.dumps(out, indent=2))
    else:
        for s in wt.snapshots:
            print(f"  step {s.step_index:3d} {type(s.step).__name__:24s}"
                  f"  labware={sum(len(st) for st in s.slot_map.values()):2d}"
                  f"  tips={len(s.mca_tips):3d}"
                  f"  tip_vol={sum(t.volume_ul for t in s.mca_tips):.1f} µL")
        if args.coverage and wt.simulation_report is not None:
            report = wt.simulation_report
            print(
                "\nCoverage:"
                f"\n  executed: {report.total_executed_steps}"
                f"\n  fully simulated: {report.fully_simulated_steps}"
                f"\n  validation-only: {report.validation_only_steps}"
                f"\n  opaque/no-op: {report.opaque_noop_steps}"
                f"\n  raw XML / GenericStep: {report.raw_xml_generic_steps}"
                f"\n  modeled coverage: {report.modeled_coverage:.3f}"
            )
            if report.unsupported_command_ids:
                unsupported = ", ".join(
                    f"{name}={count}" for name, count in report.unsupported_command_ids.items()
                )
                print(f"  unsupported: {unsupported}")
            for warning in report.warnings:
                print(f"  warning: {warning}")
    return 0


def _cmd_check(args) -> int:
    from .copilot import analyze_file

    diagnostics = analyze_file(args.input)
    document_adherence = None
    if getattr(args, "source_doc", None):
        from .authoring.document_adherence import (
            document_adherence_report,
            read_source_document,
        )

        source_doc = read_source_document(args.source_doc)
        document_adherence = document_adherence_report(
            source_text=str(source_doc.get("text") or ""),
            protocol_source=Path(args.input).read_text(encoding="utf-8"),
            source_name=str(source_doc.get("path") or args.source_doc),
        )
        document_adherence["extraction"] = {
            "method": source_doc.get("extraction_method"),
            "page_count": source_doc.get("page_count"),
            "warnings": source_doc.get("warnings") or [],
        }

    explanations: dict[int, str] = {}
    if getattr(args, "explain", False) and diagnostics:
        explanations = _explain_diagnostics(args, diagnostics)

    if args.as_json:
        out = []
        for i, d in enumerate(diagnostics):
            item = d.to_dict()
            if i in explanations:
                item["explanation"] = explanations[i]
            out.append(item)
        if document_adherence is None:
            print(json.dumps(out, indent=2))
        else:
            print(json.dumps({
                "diagnostics": out,
                "document_adherence": document_adherence,
            }, indent=2))
    else:
        for i, d in enumerate(diagnostics):
            location = f"{args.input}:{d.line}" + (f":{d.col}" if d.col else "")
            print(f"{location}: [{d.severity}] {d.message}", file=sys.stderr)
            if d.hint:
                print(f"    hint: {d.hint}", file=sys.stderr)
            for fix in d.fixes:
                print(f"    fix: {fix.title}", file=sys.stderr)
            if i in explanations:
                print(f"    explain: {explanations[i]}", file=sys.stderr)
        if document_adherence is not None:
            issues = document_adherence.get("issues") or []
            print(
                f"{args.source_doc}: document adherence "
                f"{len(issues)} issue(s)"
            )
            for issue in issues:
                print(
                    f"    [{issue.get('severity')}] {issue.get('code')}: "
                    f"{issue.get('message')}"
                )
        if not diagnostics:
            print(f"{args.input}: no problems found")
    doc_errors = []
    if document_adherence is not None:
        doc_errors = [
            issue for issue in (document_adherence.get("issues") or [])
            if issue.get("severity") == "error"
        ]
    return 1 if any(d.severity == "error" for d in diagnostics) or doc_errors else 0


def _explain_diagnostics(args, diagnostics) -> dict[int, str]:
    """Attach LLM explanations to diagnostics, degrading gracefully on failure."""
    from .copilot import explain_diagnostic

    client = None
    if getattr(args, "endpoint", None) or getattr(args, "model", None):
        from .authoring.lm_client import (
            DEFAULT_LM_STUDIO_ENDPOINT,
            DEFAULT_LM_STUDIO_MODEL,
            LMStudioChatClient,
        )
        client = LMStudioChatClient(
            endpoint=args.endpoint or DEFAULT_LM_STUDIO_ENDPOINT,
            model=args.model or DEFAULT_LM_STUDIO_MODEL,
        )
    source = Path(args.input).read_text(encoding="utf-8")
    out: dict[int, str] = {}
    for i, d in enumerate(diagnostics):
        try:
            out[i] = explain_diagnostic(d.to_dict(), source, client=client)
        except Exception as exc:  # noqa: BLE001 - explanation is best-effort
            out[i] = f"(explanation unavailable: {exc})"
    return out


def _cmd_complete(args) -> int:
    from .copilot import complete_at

    source = Path(args.input).read_text(encoding="utf-8")
    completions = complete_at(source, args.line - 1, args.col - 1)
    if args.as_json:
        print(json.dumps([c.to_dict() for c in completions], indent=2))
    else:
        for c in completions:
            detail = f"  ({c.detail})" if c.detail else ""
            print(f"  {c.label}{detail}")
        if not completions:
            print("(no completions)")
    return 0


def _cmd_edit(args) -> int:
    from .copilot import edit_region

    client = None
    if args.endpoint or args.model:
        from .authoring.lm_client import (
            DEFAULT_LM_STUDIO_ENDPOINT,
            DEFAULT_LM_STUDIO_MODEL,
            LMStudioChatClient,
        )
        client = LMStudioChatClient(
            endpoint=args.endpoint or DEFAULT_LM_STUDIO_ENDPOINT,
            model=args.model or DEFAULT_LM_STUDIO_MODEL,
        )

    source = Path(args.input).read_text(encoding="utf-8")
    try:
        result = edit_region(
            source, args.start, args.end, args.instruction,
            client=client, path=str(args.input), revalidate=args.validate,
        )
    except Exception as exc:
        print(f"Edit failed: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(result.new_text)
        if result.diagnostics:
            print(
                f"\n# re-validation: {len(result.diagnostics)} diagnostic(s)"
                + (" — INTRODUCES ERRORS" if result.introduces_errors else ""),
                file=sys.stderr,
            )
            for d in result.diagnostics:
                print(f"#   {args.input}:{d['line']}: [{d['severity']}] {d['message']}",
                      file=sys.stderr)

    if args.apply and result.new_text:
        lines = source.splitlines()
        start = max(1, args.start)
        end = min(len(lines), max(start, args.end))
        edited = "\n".join([*lines[:start - 1], *result.new_text.splitlines(), *lines[end:]])
        if source.endswith("\n"):
            edited += "\n"
        Path(args.input).write_text(edited, encoding="utf-8")
        print(f"Applied edit to {args.input}", file=sys.stderr)
    return 0


def _cmd_lsp(args) -> int:
    try:
        from .lsp import main as lsp_main
    except ImportError:
        print(
            "The language server needs the optional 'lsp' extra. Install it with:\n"
            "  python -m pip install -e \".[lsp]\"",
            file=sys.stderr,
        )
        return 1
    lsp_main()
    return 0


def _cmd_decompile(args) -> int:
    from .decompiler import emit_python, parse_xscr
    from .ir.schema import GenericStep

    proto = parse_xscr(args.input)
    output = args.output or args.input.with_suffix(".py")
    src = emit_python(proto, source_xscr=str(args.input))
    output.write_text(src, encoding="utf-8")

    def _walk(steps):
        for step in steps:
            yield step
            nested = getattr(step, "steps", None)
            if nested:
                yield from _walk(nested)
            for branch in ("then_steps", "else_steps"):
                child_steps = getattr(step, branch, None)
                if child_steps:
                    yield from _walk(child_steps)

    all_steps = [s for g in proto.groups for s in _walk(g.steps)]
    n_steps = len(all_steps)
    generic_steps = [s for s in all_steps if isinstance(s, GenericStep)]
    n_generic = len(generic_steps)

    print(f"Decompiled {args.input} -> {output}")
    print(f"  groups: {len(proto.groups)}, steps: {n_steps}")
    if n_generic:
        names = sorted({s.name for s in generic_steps})
        print(f"  unrecognised steps: {n_generic} ({', '.join(names)})")
        if args.strict:
            return 1
    return 0


def _activate_profile(args):
    """Activate a ``--profile`` dir for this run, if given.

    Sets ``FLUENTVIBE_PROFILE_DIR`` (so ``load_lab_scope`` swaps in the
    profile's deck skill + whitelist) and the grounding-snapshot env, and
    defaults ``--workspace``/``--workspace-guid`` from the profile. Works for
    both the session (chat) and service (author) paths, which both read these.
    Returns the resolved profile, or ``None`` when ``--profile`` was omitted.
    """
    profile_dir = getattr(args, "profile", None)
    if profile_dir is None:
        return None
    from .authoring.grounding import CURRENT_WORKTABLE_ENV
    from .authoring.profile import PROFILE_DIR_ENV, resolve_profile

    rp = resolve_profile(profile_dir)
    os.environ[PROFILE_DIR_ENV] = str(rp.root)
    os.environ.setdefault(CURRENT_WORKTABLE_ENV, str(rp.current_worktable))
    if not args.workspace:
        args.workspace = rp.workspace_name
    if not args.workspace_guid:
        args.workspace_guid = rp.workspace_guid
    print(f"Profile: {rp.workspace_name} ({rp.root})", file=sys.stderr)
    return rp


def _author_checklist(args, result, python_path, pending, seconds: float) -> None:
    """Check the instruction/document checklist on the authored protocol and
    write requirements.md + result.json (same shape as author-spec)."""
    import json as _json

    from .authoring.eval_rubric import build_worktable_from_source
    from .authoring.requirements import requirements_markdown, verify_all

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"stage": result.status.value, "error": None, "fc_ok": None, "todo_steps": 0,
                               "draft": str(python_path) if python_path else None,
                               "timings": {"total_s": round(seconds, 1)}}
    try:
        reqs, dispositions = pending.result(timeout=1800)
    except Exception as exc:  # noqa: BLE001
        summary["error"] = f"checklist: {type(exc).__name__}: {exc}"[:300]
        reqs, dispositions = [], []
    verdicts = []
    if python_path and Path(python_path).exists() and reqs:
        try:
            wt = build_worktable_from_source(Path(python_path).read_text(encoding="utf-8"), str(python_path))
            wt.simulate()
            verdicts = verify_all(wt, reqs)
        except Exception as exc:  # noqa: BLE001
            summary["error"] = f"check: {type(exc).__name__}: {exc}"[:300]
    if verdicts:
        (out / "requirements.md").write_text(requirements_markdown(reqs, verdicts, dispositions), encoding="utf-8")
    fc_file = out / "fluentcontrol_check.json"
    if fc_file.exists():
        try:
            summary["fc_ok"] = _json.loads(fc_file.read_text(encoding="utf-8")).get("ok")
        except ValueError:
            pass
    if python_path and Path(python_path).exists():
        summary["todo_steps"] = Path(python_path).read_text(encoding="utf-8").count('wt.add_comment("TODO')
    statuses = [v.status for v in verdicts]
    summary["instructions"] = {"total": len(statuses), "verified": statuses.count("pass"),
                               "failed": statuses.count("fail"), "unverified": statuses.count("unknown")}
    (out / "result.json").write_text(_json.dumps(summary, indent=2), encoding="utf-8")


def _cmd_author(args) -> int:
    from .authoring import PromptAuthoringService

    _activate_profile(args)
    prompt = " ".join(args.prompt).strip()
    request = prompt
    document_text = None
    if getattr(args, "document", None) is not None:
        from .authoring.attachments import extract_file_text

        document_text, method, pages, warnings = extract_file_text(args.document)
        prompt = "\n".join([
            prompt, "", "Attached file context:", "", f"--- Attached file: {args.document.name} ---",
            f"Extraction method: {method}", *([f"Page count: {pages}"] if pages is not None else []),
            *[f"Extraction warning: {w}" for w in warnings], "Extracted text follows:", document_text,
            f"--- End attached file: {args.document.name} ---",
        ])
    pending_checklist = None
    if getattr(args, "check_instructions", False):
        from concurrent.futures import ThreadPoolExecutor

        from .authoring.lm_client import LMStudioChatClient

        def _checklist():
            from .authoring.bench_spec import extract_bench_spec
            from .authoring.requirements import extract_requirements, requirements_from_spec

            client = LMStudioChatClient(request_timeout_s=1800)
            reqs, dispositions = extract_requirements(client, request, document_text)
            if document_text:
                spec, _problems, _raw = extract_bench_spec(client, document_text)
                if spec is not None:
                    reqs = reqs + requirements_from_spec(spec)
            return reqs, dispositions

        pool = ThreadPoolExecutor(max_workers=1)
        pending_checklist = pool.submit(_checklist)
        pool.shutdown(wait=False)
    if getattr(args, "spec", None) is not None:
        import json as _json

        from .authoring.bench_spec import spec_context_block, validate_bench_spec

        spec, problems = validate_bench_spec(_json.loads(args.spec.read_text(encoding="utf-8")))
        if spec is None:
            for problem in problems:
                print(f"error: {args.spec}: {problem.where}: {problem.message}", file=sys.stderr)
            return 1
        prompt = f"{prompt}\n\n{spec_context_block(spec)}"
    # Only forward overrides when given; the service constructor already falls
    # back to the env-driven FLUENTVIBE_LM_ENDPOINT / FLUENTVIBE_LM_MODEL defaults.
    service_kwargs: dict[str, Any] = {}
    if args.endpoint:
        service_kwargs["endpoint"] = args.endpoint
    if args.model:
        service_kwargs["model"] = args.model
    if args.request_timeout is not None:
        service_kwargs["request_timeout_s"] = args.request_timeout
    service = PromptAuthoringService(**service_kwargs)
    kwargs: dict[str, Any] = dict(
        output_dir=args.output_dir,
        retry_budget=args.retry_budget,
        workspace_name=args.workspace,
        workspace_guid=args.workspace_guid,
    )
    if args.run_timeout is not None:
        kwargs["run_timeout_s"] = args.run_timeout
    trace_config = _trace_config_for_cli(args.output_dir, args)
    if trace_config is not None:
        kwargs["trace_config"] = trace_config
        print(f"Model trace: {trace_config.output_dir / 'model_traces'}", file=sys.stderr)
    if args.lab_scope is not None:
        kwargs["lab_scope"] = args.lab_scope
    from .authoring.lab_scope import resolve_lab_scope_mode
    _scope_mode = resolve_lab_scope_mode(args.lab_scope)
    if _scope_mode != "off":
        print(f"Lab scope: {_scope_mode}", file=sys.stderr)
    started = __import__("time").monotonic()
    result = service.author(
        prompt,
        **kwargs,
    )
    python_path = result.validation.python_path if result.validation else None
    if pending_checklist is not None:
        _author_checklist(args, result, python_path, pending_checklist,
                          __import__("time").monotonic() - started)
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    elif result.status.value == "success":
        protocol_name = result.spec.protocol_name if result.spec else "LM-authored protocol"
        print(f"Authored {protocol_name}")
        if result.validation and result.validation.python_path:
            print(f"  Python: {result.validation.python_path}")
        if result.compiled_xscr:
            print(f"  XSCR:   {result.compiled_xscr}")
        print(f"  Attempts: {result.attempts}")
    elif result.status.value == "clarification_required":
        print("Clarification required:")
        for question in result.clarification_questions:
            print(f"  - {question.question}")
    elif result.status.value == "approval_required":
        _print_authoring_approval(result)
    else:
        category = result.failure_category.value if result.failure_category else "unknown"
        print(f"Authoring failed [{category}]: {result.failure_message}", file=sys.stderr)
        if result.validation and result.validation.python_path:
            print(f"Best draft: {result.validation.python_path}", file=sys.stderr)
    if result.status.value == "success":
        return 0
    if result.status.value == "clarification_required":
        return 2
    if result.status.value == "approval_required":
        return 2
    return 1


def _cmd_lookup_eval(args) -> int:
    from .authoring.lm_client import (
        DEFAULT_LM_STUDIO_ENDPOINT,
        DEFAULT_LM_STUDIO_MODEL,
        make_chat_client,
    )
    from .authoring.lookup_eval import run_lookup_eval, write_lookup_eval_report

    client = None
    if args.live:
        client = make_chat_client(
            model=args.model or DEFAULT_LM_STUDIO_MODEL,
            endpoint=args.endpoint or DEFAULT_LM_STUDIO_ENDPOINT,
        )
    report = run_lookup_eval(output_dir=args.output.parent, live_client=client)
    path = write_lookup_eval_report(report, args.output)
    summary = report["summary"]
    print(f"Wrote {path}")
    print(
        "Lookup eval: "
        f"{summary['tool_call_count']} tool call(s), "
        f"cache hit rate {summary['cache_hit_rate']:.2f}, "
        f"tool p50/p95 {summary['tool_ms_p50']:.1f}/{summary['tool_ms_p95']:.1f} ms"
    )
    return 0


def _cmd_render_trace(args) -> int:
    from .authoring.trace import render_model_trace_file

    path = render_model_trace_file(args.input, args.output)
    print(f"Rendered {path}")
    return 0


def _cmd_spec(args) -> int:
    import json as _json

    from .authoring.attachments import extract_file_text
    from .authoring.bench_spec import extract_bench_spec, spec_to_markdown
    from .authoring.lm_client import LMStudioChatClient

    text, _method, _pages, warnings = extract_file_text(args.document)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    kwargs: dict[str, Any] = {}
    if args.endpoint:
        kwargs["endpoint"] = args.endpoint
    if args.model:
        kwargs["model"] = args.model
    if args.request_timeout is not None:
        kwargs["request_timeout_s"] = args.request_timeout
    context = None
    if args.examples:
        from .authoring.spec_retrieval import retrieval_context

        context = retrieval_context(text)
    spec, problems, raw = extract_bench_spec(LMStudioChatClient(**kwargs), text, extra_context=context)
    if spec is None:
        for problem in problems:
            print(f"error: {problem.where}: {problem.message}", file=sys.stderr)
        return 1
    table = spec_to_markdown(spec, problems)
    if args.output is not None:
        args.output.write_text(_json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        args.output.with_suffix(".md").write_text(table, encoding="utf-8")
        print(f"Wrote {args.output} and {args.output.with_suffix('.md')}")
    print(table)
    return 0 if not problems else 2


def _fc_draft_worktable(draft: Path, profile: Path | None):
    import os

    from .authoring.eval_rubric import build_worktable_from_source
    from .authoring.profile import PROFILE_DIR_ENV

    if profile is not None:
        os.environ[PROFILE_DIR_ENV] = str(profile)
    source = draft.read_text(encoding="utf-8")
    return source, build_worktable_from_source(source, str(draft.resolve()))


def _cmd_fc_open(args) -> int:
    from .authoring.fc_feedback import explain_infopad
    from .authoring.fluentcontrol_shell import (
        open_shell_and_read_infopad,
        patch_shell_xscr_from_generated,
    )

    _source, wt = _fc_draft_worktable(args.draft, args.profile)
    base = args.draft.with_suffix(".fc-base.xscr")
    wt.compile(base)
    patch_shell_xscr_from_generated(base)
    ui = open_shell_and_read_infopad(close_before_open=True, close_after_read=False)
    findings = explain_infopad(list(ui.error_lines or []), wt)
    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps({
            "opened": bool(getattr(ui, "opened", True)) and not getattr(ui, "load_failed", False),
            "load_error": getattr(ui, "load_error_text", "") or "",
            "compiled": str(base),
            "findings": [f.to_dict() for f in findings],
        }))
        return 0
    print(f"Opened {args.draft.name} in FluentControl (script 'shell'); compiled copy: {base}")
    if findings:
        print(f"InfoPad: {len(findings)} finding(s):")
        for f in findings:
            d = f.to_dict()
            print(f"  [{d['kind']}] {d['message']} (lines {d['python_lines']}) -> {d['hint']}")
    else:
        print("InfoPad: no errors.")
    print(f"Edit and save in FluentControl, then: fluentvibe fc-pull {args.draft}")
    return 0


def _cmd_fc_pull(args) -> int:
    from .authoring.fc_roundtrip import (
        diff_scripts,
        locate_variables,
        roundtrip_message,
        write_report,
    )
    from .authoring.fluentcontrol_shell import DEFAULT_SHELL_XSCR

    source, wt = _fc_draft_worktable(args.draft, args.profile)
    base = args.base or args.draft.with_suffix(".fc-base.xscr")
    if not base.exists():
        print(f"error: {base} not found; run fluentvibe fc-open {args.draft} first", file=sys.stderr)
        return 1
    edited = args.edited or DEFAULT_SHELL_XSCR
    changes = locate_variables(diff_scripts(base, edited, wt=wt), source)
    report = args.draft.with_suffix(".fc-changes.json")
    write_report(changes, report)
    print(roundtrip_message(changes))
    print(f"\nWrote {report}")
    return 0


def _cmd_opentrons(args) -> int:
    """Opentrons protocol -> Bench Spec (Opentrons simulator) -> skeleton -> checks. No model."""
    import json as _json
    import os
    import subprocess
    import time

    from .authoring.bench_spec import spec_to_markdown, validate_bench_spec
    from .authoring.profile import PROFILE_DIR_ENV
    from .authoring.skeleton import DeckMismatch, OpenValues, build_skeleton, load_deck

    def progress(message: str) -> None:
        print(f"progress: {message}", flush=True)

    repo = Path(__file__).resolve().parent.parent
    python = args.opentrons_python or repo / ".venv-opentrons" / "Scripts" / "python.exe"
    if not Path(python).exists():
        print(f"error: no Python with the opentrons package at {python} (see scripts/opentrons_to_spec.py)",
              file=sys.stderr)
        return 1
    os.environ[PROFILE_DIR_ENV] = str(args.profile)
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    summary: dict[str, Any] = {"stage": "convert", "error": None}

    def finish(code: int) -> int:
        summary["total_s"] = round(time.monotonic() - started, 1)
        (args.output / "result.json").write_text(_json.dumps(summary, indent=2), encoding="utf-8")
        print(_json.dumps(summary, indent=2))
        return code

    progress("running the protocol in the Opentrons simulator and reading its steps")
    done = subprocess.run(
        [str(python), str(repo / "scripts" / "opentrons_to_spec.py"), str(args.protocol),
         "--out", str(args.output), "--single"],
        capture_output=True, text=True, timeout=600,
    )
    rows = [line for line in done.stdout.splitlines() if line.startswith("{")]
    row = _json.loads(rows[-1]) if rows else {"status": "error", "error": done.stderr.strip()[-300:]}
    if row.get("status") != "ok":
        summary["error"] = row.get("error")
        return finish(1)
    name = args.protocol.stem if args.protocol.is_file() else args.protocol.name
    raw = _json.loads((args.output / f"{name}.json").read_text(encoding="utf-8"))
    raw.pop("_source", None)
    (args.output / "spec.json").write_text(_json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    spec, problems = validate_bench_spec(raw)
    if spec is None:
        summary.update(stage="spec", error="; ".join(p.message for p in problems))
        return finish(1)
    (args.output / "spec.md").write_text(spec_to_markdown(spec, problems), encoding="utf-8")
    summary["steps"] = [st.op for st in spec.steps]
    progress(f"spec: {len(spec.steps)} steps; building the protocol for this deck")
    try:
        source = build_skeleton(spec, load_deck(args.profile))
    except (OpenValues, DeckMismatch, ValueError) as exc:
        summary.update(stage="skeleton", error=str(exc))
        return finish(1)
    draft = args.output / "draft.py"
    draft.write_text(source, encoding="utf-8")
    from .authoring.lab_scope import load_lab_scope
    from .authoring.tools import AuthoringToolRegistry

    progress("compiling and simulating")
    registry = AuthoringToolRegistry(output_dir=args.output)
    registry.lab_scope = load_lab_scope("skills")
    gate = registry.compile_and_simulate(source)
    summary["gate"] = bool(gate.get("success"))
    summary["todo_steps"] = source.count('wt.add_comment("TODO')
    if not summary["gate"]:
        summary.update(stage="gate", error=" ".join(str(gate.get("failure_message", "")).split())[:600])
        return finish(1)
    if args.fc_check:
        from .authoring.eval_rubric import build_worktable_from_source
        from .authoring.fc_feedback import check_in_fluentcontrol

        progress("checking in FluentControl (InfoPad)")
        xscr = args.output / "draft.xscr"
        build_worktable_from_source(source, str(draft)).compile(xscr)
        fc = check_in_fluentcontrol(xscr, source=source, source_file=str(draft))
        summary["fc_ok"] = fc.get("ok")
        summary["fc_findings"] = [f"{f['kind']}: {f['message'][:100]}" for f in fc.get("findings", [])]
    summary["stage"] = "done"
    return finish(0 if summary.get("fc_ok") is not False else 1)


def _cmd_requirements(args) -> int:
    """Write (with --request) and check the protocol's instruction checklist; prints JSON."""
    import json as _json
    import os

    from .authoring.eval_rubric import build_worktable_from_source
    from .authoring.profile import PROFILE_DIR_ENV
    from .authoring.requirements import (
        extract_requirements, load_requirements, save_requirements, sidecar_path, verify_all,
    )

    if args.profile is not None:
        os.environ[PROFILE_DIR_ENV] = str(args.profile)
    sidecar = sidecar_path(args.draft)
    if args.request:
        from .authoring.lm_client import LMStudioChatClient

        document = None
        if args.document is not None:
            from .authoring.attachments import extract_file_text

            document = extract_file_text(args.document)[0]
        client = LMStudioChatClient(request_timeout_s=1800)
        requirements, _dispositions = extract_requirements(client, args.request, document)
        if document:
            # The document's own steps, in order (a separate spec extraction).
            from .authoring.bench_spec import extract_bench_spec
            from .authoring.requirements import requirements_from_spec

            spec, _problems, _raw = extract_bench_spec(client, document)
            if spec is not None:
                requirements += requirements_from_spec(spec)
        save_requirements(sidecar, requirements)
    if not sidecar.exists():
        print(f"error: no checklist {sidecar}; give --request", file=sys.stderr)
        return 1
    requirements = load_requirements(sidecar)
    wt = build_worktable_from_source(args.draft.read_text(encoding="utf-8"), str(args.draft))
    verdicts = verify_all(wt, requirements)
    texts = {r.id: r.text for r in requirements}
    print(_json.dumps({"checklist": str(sidecar), "verdicts": [
        {"id": v.id, "text": texts.get(v.id, ""), "status": v.status, "evidence": v.evidence, "line": v.line}
        for v in verdicts]}, indent=2, ensure_ascii=False))
    return 0 if all(v.status == "pass" for v in verdicts) else 1


def _cmd_replay(args) -> int:
    import os

    from .authoring.eval_rubric import build_worktable_from_source
    from .authoring.profile import PROFILE_DIR_ENV
    from .replay import replay_frames, replay_html

    if args.profile is not None:
        os.environ[PROFILE_DIR_ENV] = str(args.profile)
    wt = build_worktable_from_source(args.draft.read_text(encoding="utf-8"), str(args.draft))
    frames = replay_frames(wt, title=getattr(wt, "name", None) or args.draft.stem)
    out = args.output or args.draft.with_name(args.draft.stem + ".replay.html")
    out.write_text(replay_html(frames), encoding="utf-8")
    print(out)
    return 0


def _cmd_author_spec(args) -> int:
    import json as _json
    import os

    from .authoring.attachments import extract_file_text
    from .authoring.bench_spec import spec_to_markdown
    from .authoring.lm_client import LMStudioChatClient
    from .authoring.profile import PROFILE_DIR_ENV
    from .authoring.spec_path import author_from_document, choose_yourself

    os.environ[PROFILE_DIR_ENV] = str(args.profile)
    text, _method, _pages, warnings = extract_file_text(args.document)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    kwargs: dict[str, Any] = {}
    if args.endpoint:
        kwargs["endpoint"] = args.endpoint
    if args.model:
        kwargs["model"] = args.model
    kwargs["request_timeout_s"] = args.request_timeout
    # Model traces in <output>/model_traces, so a slow or stuck request can be read back.
    from .authoring.trace import ModelTraceConfig, ModelTraceRecorder

    args.output.mkdir(parents=True, exist_ok=True)
    kwargs["trace_recorder"] = ModelTraceRecorder(ModelTraceConfig.from_env(output_dir=args.output, enabled=True))

    def ask_terminal(questions: list[str]) -> str | None:
        print("\nThe spec leaves these open:")
        for q in questions:
            print(f"  - {q}")
        print("Answer (empty to stop): ", end="", flush=True)
        answer = sys.stdin.readline().strip()
        return answer or None

    args.output.mkdir(parents=True, exist_ok=True)
    result = author_from_document(
        LMStudioChatClient(**kwargs), text, args.profile, args.output, request=args.request,
        ask=choose_yourself if args.choose else ask_terminal, fluentcontrol=args.fc_check,
        progress=lambda message: print(f"progress: {message}", flush=True),
        check_requirements=args.check_instructions,
    )
    if result.spec_raw is not None:
        (args.output / "spec.json").write_text(_json.dumps(result.spec_raw, indent=2, ensure_ascii=False), encoding="utf-8")
    if result.spec is not None:
        (args.output / "spec.md").write_text(spec_to_markdown(result.spec, result.problems), encoding="utf-8")
    if result.source is not None:
        (args.output / "draft.py").write_text(result.source, encoding="utf-8")
    if result.requirements_markdown:
        (args.output / "requirements.md").write_text(result.requirements_markdown, encoding="utf-8")
    (args.output / "result.json").write_text(_json.dumps(result.summary(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(_json.dumps(result.summary(), indent=2, ensure_ascii=False))
    summary = result.summary()
    return 0 if result.stage == "done" and summary["fc_ok"] is not False and not summary["todo_steps"] else 1


def _cmd_skeleton(args) -> int:
    import json as _json

    from .authoring.bench_spec import validate_bench_spec
    from .authoring.skeleton import build_skeleton, load_deck

    spec, problems = validate_bench_spec(_json.loads(args.spec.read_text(encoding="utf-8")))
    if spec is None:
        for problem in problems:
            print(f"error: {args.spec}: {problem.where}: {problem.message}", file=sys.stderr)
        return 1
    try:
        source = build_skeleton(spec, load_deck(args.profile))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output is not None:
        args.output.write_text(source, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(source)
    return 0


def _cmd_chat(args) -> int:
    from .authoring import PromptAuthoringSession
    _activate_profile(args)
    trace_config = _trace_config_for_cli(args.output_dir, args)

    def new_session() -> PromptAuthoringSession:
        kwargs: dict[str, Any] = dict(
            output_dir=args.output_dir,
            retry_budget=args.retry_budget,
            workspace_name=args.workspace,
            workspace_guid=args.workspace_guid,
        )
        if args.model:
            kwargs["model"] = args.model
        if args.request_timeout is not None:
            kwargs["request_timeout_s"] = args.request_timeout
        if trace_config is not None:
            kwargs["trace_config"] = trace_config
        if args.lab_scope is not None:
            kwargs["lab_scope"] = args.lab_scope
        return PromptAuthoringSession(**kwargs)

    session = new_session()
    printed_tool_calls = 0
    print("fluentvibe authoring chat")
    if trace_config is not None:
        print(f"Model trace: {trace_config.output_dir / 'model_traces'}")
    from .authoring.lab_scope import resolve_lab_scope_mode
    _scope_mode = resolve_lab_scope_mode(args.lab_scope)
    if _scope_mode != "off":
        print(f"Lab scope: {_scope_mode}")
    print("Type a protocol request, /help for commands, or /exit to quit.")
    if args.fc_gate:
        print("FC gate: enabled — successful authoring will be FC-shell-validated and deployed.")
    while True:
        try:
            line = input("fluentvibe> ")
        except EOFError:
            print()
            return 0
        text = line.strip()
        if not text:
            continue
        if text in {"/exit", "/quit"}:
            return 0
        if text == "/help":
            print("Commands:")
            print("  /help                 show this help")
            print("  /reset                clear this chat session")
            print("  /validate-fc <xscr>   load an existing .xscr through the FluentControl shell")
            print("  /exit                 quit")
            continue
        if text == "/reset":
            session = new_session()
            printed_tool_calls = 0
            print("Session reset.")
            continue
        if text.startswith("/validate-fc "):
            xscr = text[len("/validate-fc "):].strip().strip('"')
            result = session.validate_fluentcontrol_shell(xscr)
            print(json.dumps(result, indent=2))
            continue
        if text.startswith("/"):
            print(f"Unknown command: {text}")
            continue

        result = session.send(text)
        calls = result.tool_calls
        for call in calls[printed_tool_calls:]:
            _print_authoring_tool_summary(call)
        printed_tool_calls = len(calls)
        _print_authoring_result(result)
        if args.fc_gate and result.status.value == "success" and result.compiled_xscr:
            gate_rc = _run_fc_gate(
                session,
                result.compiled_xscr,
                datastore_dir=args.datastore_dir,
                allow_fc_running=args.allow_fc_running,
            )
            if gate_rc != 0:
                return gate_rc


def _run_fc_gate(session, xscr_path: Path, *, datastore_dir: Optional[Path],
                 allow_fc_running: bool) -> int:
    from .deployer import DEFAULT_DATASTORE_DIR, DeploymentError, deploy_xscr

    print("FC gate: validating via FluentControl shell…")
    fc_result = session.validate_fluentcontrol_shell(str(xscr_path))
    if not fc_result.get("ok"):
        print("FC gate: shell validation FAILED — not deploying.", file=sys.stderr)
        for err in fc_result.get("errors") or []:
            print(f"  - {err}", file=sys.stderr)
        if fc_result.get("load_error_text"):
            print(f"  load_error: {fc_result['load_error_text']}", file=sys.stderr)
        return 1
    print(f"FC gate: shell validation passed ({fc_result.get('error_count', 0)} errors).")
    print("FC gate: deploying into UserSpecific datastore…")
    try:
        deploy = deploy_xscr(
            xscr_path,
            datastore_dir=datastore_dir or DEFAULT_DATASTORE_DIR,
            require_fc_closed=not allow_fc_running,
        )
    except DeploymentError as exc:
        print(f"FC gate: deploy FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"FC gate: deployed to {deploy.deployed_path}")
    print(f"  ObjectName: {deploy.object_name}")
    print(f"  Checksum:   {deploy.checksum}")
    return 0


def _cmd_workspace_app(args) -> int:
    from .workspace_app import serve_workspace_app

    serve_workspace_app(host=args.host, port=args.port)
    return 0


def _trace_config_for_cli(output_dir: Path, args) -> Any | None:
    enabled = bool(
        getattr(args, "model_trace", False)
        or getattr(args, "model_trace_live", False)
        or _truthy_env("FLUENTVIBE_MODEL_TRACE")
        or _truthy_env("FLUENTVIBE_MODEL_TRACE_LIVE")
    )
    if not enabled:
        return None
    from .authoring.trace import ModelTraceConfig

    live = bool(
        getattr(args, "model_trace_live", False)
        or _truthy_env("FLUENTVIBE_MODEL_TRACE_LIVE")
    )
    return ModelTraceConfig.from_env(
        output_dir=output_dir,
        enabled=True,
        live=live,
    )


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _cmd_deploy(args) -> int:
    from .deployer import DEFAULT_DATASTORE_DIR, DeploymentError, deploy_xscr

    try:
        result = deploy_xscr(
            args.xscr,
            datastore_dir=args.datastore_dir or DEFAULT_DATASTORE_DIR,
            new_object_name=args.object_name,
            new_workspace_delta_id=not args.keep_delta_id,
            require_fc_closed=not args.allow_fc_running,
        )
    except DeploymentError as exc:
        print(f"Deploy failed: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Deployed: {result.deployed_path}")
        print(f"  ObjectName:        {result.object_name}")
        print(f"  WorkspaceDeltaId:  {result.workspace_delta_id}")
        print(f"  Checksum:          {result.checksum}")
        print(f"  is_valid:          {result.inspect.get('is_valid')}")
    return 0


def _print_authoring_tool_summary(call: dict) -> None:
    name = call.get("name") or "tool"
    result = call.get("result") or {}
    ok = result.get("ok")
    status = "ok" if ok is True else "failed" if ok is False else "done"
    stage = result.get("stage") or result.get("category") or result.get("failure_category")
    suffix = f" ({stage})" if stage else ""
    print(f"  tool {name}: {status}{suffix}")
    if ok is False and result.get("message"):
        print(f"    {result['message']}")


def _print_authoring_result(result) -> None:
    if result.status.value == "clarification_required":
        print("Clarification required:")
        for question in result.clarification_questions:
            print(f"  {question.question}")
        return
    if result.status.value == "approval_required":
        _print_authoring_approval(result)
        return
    if result.status.value == "success":
        print("Authoring succeeded.")
        if result.validation and result.validation.python_path:
            print(f"  Python: {result.validation.python_path}")
        if result.compiled_xscr:
            print(f"  XSCR:   {result.compiled_xscr}")
        print("  Strict simulation: passed")
        print(f"  Tool calls: {len(result.tool_calls)}")
        return
    category = result.failure_category.value if result.failure_category else "unknown"
    print(f"Authoring failed [{category}]: {result.failure_message}", file=sys.stderr)
    if result.validation and result.validation.python_path:
        print(f"Best draft: {result.validation.python_path}", file=sys.stderr)


def _print_authoring_approval(result) -> None:
    request = result.approval_request
    print("Approval required:")
    if request is None:
        print("  Approve this checkpoint, or reply with changes.")
        return
    print(f"  {request.title}")
    if request.summary:
        print(f"  {request.summary}")
    payload = request.payload or {}
    workspace = payload.get("workspace")
    if isinstance(workspace, dict) and workspace:
        name = workspace.get("name") or workspace.get("workspace_name")
        guid = workspace.get("guid") or workspace.get("workspace_guid")
        label = " / ".join(str(part) for part in (name, guid) if part)
        if label:
            print(f"  Worktable: {label}")
    for section in ("variables", "reagents", "liquid_classes", "labware", "groups"):
        items = payload.get(section)
        if isinstance(items, list) and items:
            print(f"  {section.replace('_', ' ').title()}:")
            for item in items[:12]:
                print(f"    - {_summarize_payload_item(item)}")
            if len(items) > 12:
                print(f"    - ... {len(items) - 12} more")
    print(f"  {request.question}")


def _summarize_payload_item(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    parts = []
    for key in (
        "name",
        "label",
        "role",
        "python_class",
        "class",
        "catalog",
        "catalog_name",
        "location",
        "position",
        "objective",
    ):
        value = item.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return ", ".join(parts) if parts else json.dumps(item, default=str)


def _load_protocol(input_path: Path):
    """Load a `.py` protocol script and return its `Worktable`.

    The script is expected to define a top-level function `build_worktable()`
    that returns a `Worktable`, OR to leave a module-level `wt: Worktable`.
    """
    spec = importlib.util.spec_from_file_location(input_path.stem, input_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {input_path}")
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original

    if hasattr(module, "build_worktable"):
        return module.build_worktable()
    if hasattr(module, "wt"):
        return module.wt
    raise ValueError(
        f"{input_path}: expected `build_worktable()` or top-level `wt`"
    )


# ── catalog subcommands ────────────────────────────────────────────


def _cmd_catalog_refresh(args) -> int:
    from .catalog.indexer import build_index
    counts = build_index(install_path=args.install, db_path=args.db)
    print("Catalog index rebuilt:")
    for k, v in counts.items():
        print(f"  {k:14s} {v}")
    return 0


def _cmd_catalog_info(args) -> int:
    from .catalog.catalog import category_counts, index_exists, install_info
    if not index_exists():
        print("Catalog index is empty. Run `fluentvibe catalog refresh`.")
        return 1
    info = install_info() or {}
    print("Install path :", info.get("install_path"))
    print("Built at     :", info.get("built_at"))
    print("Fingerprint  :", info.get("fingerprint"))
    print("Component categories:")
    for cat, n in category_counts().items():
        print(f"  {cat:14s} {n}")
    return 0


def _cmd_catalog_find(args) -> int:
    from .catalog.catalog import find_components
    rows = find_components(args.pattern)
    if args.category:
        rows = [r for r in rows if r.category == args.category]
    if not rows:
        print(f"No components match {args.pattern!r}.")
        return 1
    for r in rows:
        print(f"  [{r.category:13s}] {r.name}")
    print(f"\n{len(rows)} match(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
