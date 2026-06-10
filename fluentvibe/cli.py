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
    p_check.set_defaults(func=_cmd_check)

    p_lsp = sub.add_parser(
        "lsp",
        help="start the fluentvibe language server over stdio (for editors)",
    )
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
                               "env FLUENTVIBE_LM_ENDPOINT or http://localhost:1234)")
    p_author.add_argument("--model", default=None,
                          help="model name (default: env FLUENTVIBE_LM_MODEL)")
    p_author.add_argument("--json", dest="as_json", action="store_true",
                          help="emit a JSON summary of the authoring result")
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
    if args.as_json:
        print(json.dumps([d.to_dict() for d in diagnostics], indent=2))
    else:
        for d in diagnostics:
            location = f"{args.input}:{d.line}" + (f":{d.col}" if d.col else "")
            print(f"{location}: [{d.severity}] {d.message}", file=sys.stderr)
            if d.hint:
                print(f"    hint: {d.hint}", file=sys.stderr)
            for fix in d.fixes:
                print(f"    fix: {fix.title}", file=sys.stderr)
        if not diagnostics:
            print(f"{args.input}: no problems found")
    return 1 if any(d.severity == "error" for d in diagnostics) else 0


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


def _cmd_author(args) -> int:
    from .authoring import PromptAuthoringService

    _activate_profile(args)
    prompt = " ".join(args.prompt).strip()
    # Only forward overrides when given; the service constructor already falls
    # back to the env-driven FLUENTVIBE_LM_ENDPOINT / FLUENTVIBE_LM_MODEL defaults.
    service_kwargs: dict[str, Any] = {}
    if args.endpoint:
        service_kwargs["endpoint"] = args.endpoint
    if args.model:
        service_kwargs["model"] = args.model
    service = PromptAuthoringService(**service_kwargs)
    kwargs: dict[str, Any] = dict(
        output_dir=args.output_dir,
        retry_budget=args.retry_budget,
        workspace_name=args.workspace,
        workspace_guid=args.workspace_guid,
    )
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
    result = service.author(
        prompt,
        **kwargs,
    )
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
