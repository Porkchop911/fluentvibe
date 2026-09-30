"""Matched Fast/Full sampling trials, without auto-approving model proposals.

Supply a confirmed request file and optional exact-question answer map. Unknown
questions are reported, never answered 'ok'. No server lifecycle operations.
All output stays in the repo. See docs/authoring-incident-experiment.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
ORACLE = REPO / "tests/fixtures/dynabeads_m280_dna_oracle.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def repo_path(raw):
    path = (REPO / raw).resolve()
    if not path.is_relative_to(REPO):
        raise ValueError(f"Path must stay inside the repo: {path}")
    return path


def schedule(runs=3, seed=29):
    if runs < 3:
        raise ValueError("At least three trials per mode/temperature are required")
    jobs = []
    rng = random.Random(seed)
    for replicate in range(1, runs + 1):
        block = [(mode, temperature, replicate) for mode in ("fast", "full") for temperature in (0.2, 1.0)]
        rng.shuffle(block)
        jobs.extend(block)
    return jobs


def fingerprint(profile):
    files = [p for base in (REPO / "fluentvibe", REPO / "scripts", REPO / "skills", profile)
             if base.exists() for p in base.rglob("*") if p.is_file() and p.suffix in {".py", ".json", ".yaml", ".md"}
             and "__pycache__" not in p.parts]
    files.append(ORACLE)
    return {str(p.relative_to(REPO)): digest(p) for p in sorted(set(files))}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def server_state(endpoint):
    """Read-only snapshot, deliberately excluding API keys and other env vars."""
    headers = {"Authorization": f"Bearer {os.environ['FLUENTVIBE_LM_API_KEY']}"} if os.environ.get("FLUENTVIBE_LM_API_KEY") else {}
    url = endpoint.removesuffix("/chat/completions") + "/models"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
        models = json.load(response)
    state = {"models": [{k: model.get(k) for k in ("id", "root", "max_model_len")} for model in models["data"]]}
    probe = '''import pathlib,json
keys={"--max-model-len","--kv-cache-dtype","--quantization","--attention-backend","--speculative-config","--reasoning-parser","--tool-call-parser","--dtype","--max-num-seqs"}
rows=[]
for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
 try:
  argv=path.read_bytes().decode().split("\\0")
  if any(x.endswith("vllm") for x in argv) and "serve" in argv:
   rows.append({"pid":path.parent.name,"flags":{k:argv[i+1] for i,k in enumerate(argv[:-1]) if k in keys}})
 except (OSError,UnicodeError):pass
print(json.dumps(rows))'''
    state["server_processes"] = json.loads(subprocess.check_output(
        ["wsl", "-d", "Ubuntu", "--", "python3", "-B", "-c", probe], text=True, timeout=15))
    if not state["server_processes"]:
        raise RuntimeError("Could not verify active vLLM process settings; refuse an untracked serving configuration")
    return state


def trace_metrics(folder):
    metrics = {"malformed_json_calls": 0, "invalid_python_sources": 0, "reasoning_only_replies": 0,
               "length_cutoffs": 0, "retry_reasons": [], "stop_reasons": [], "token_ids_observed": 0}
    import ast

    for path in (folder / "model_traces").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event["event"] == "turn_retry":
                metrics["retry_reasons"].append(event.get("reason"))
            if event["event"] == "raw_stream_line":
                chunk = json.loads(event["raw_line"])
                for choice in chunk.get("choices") or []:
                    metrics["token_ids_observed"] += len(choice.get("token_ids") or [])
            if event["event"] != "response_final":
                continue
            metrics["stop_reasons"].append({"finish_reason": event.get("finish_reason"),
                                            "stop_reason": event.get("stop_reason")})
            metrics["length_cutoffs"] += event.get("finish_reason") == "length"
            calls = event.get("tool_calls") or []
            metrics["reasoning_only_replies"] += bool(event.get("reasoning_fields")) and not calls and not (event.get("assistant_content") or "").strip()
            for call in calls:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (ValueError, TypeError):
                    metrics["malformed_json_calls"] += 1
                    continue
                if fn.get("name") == "simulate_python_draft":
                    try:
                        ast.parse(args["source"])
                    except (SyntaxError, KeyError, TypeError):
                        metrics["invalid_python_sources"] += 1
    return metrics


def acceptance(review, oracle):
    """Human source/operation mapping, independent of model-authored claims."""
    expected = {item["id"] for item in oracle["checks"]}
    checks = review.get("checks") or {}
    if review.get("extra_steps") or review.get("extra_reagents"):
        return "fail"
    if any(item.get("status") == "fail" for item in checks.values()):
        return "fail"
    if set(checks) != expected or not review.get("reviewer"):
        return "unreviewed"
    if not all(item.get("status") == "pass" and str(item.get("evidence") or "").strip() for item in checks.values()):
        return "unreviewed"
    if review.get("extra_steps") != [] or review.get("extra_reagents") != []:
        return "unreviewed"
    return "pass"


def summarize(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    oracle = json.loads(ORACLE.read_text(encoding="utf-8"))
    if digest(ORACLE) != manifest["oracle_sha256"]:
        raise ValueError("Oracle changed since the experiment; do not silently rescore with new criteria")
    groups = {}
    for index, (mode, temperature, replicate) in enumerate(manifest["jobs"]):
        group = groups.setdefault(f"{mode}/t{temperature}", {"planned": 0, "completed": 0, "accepted": 0,
                                  "chemistry_pass": 0, "chemistry_fail": 0, "unreviewed": 0,
                                  "malformed_json_calls": 0, "reasoning_only_replies": 0,
                                  "confirmed_extra_steps": 0, "confirmed_extra_reagents": 0, "seconds": []})
        group["planned"] += 1
        trial = folder / f"{index + 1:02}-{mode}-t{temperature}-r{replicate}"
        if not (trial / "metrics.json").exists():
            continue
        metrics = json.loads((trial / "metrics.json").read_text(encoding="utf-8"))
        review = json.loads((trial / "review.json").read_text(encoding="utf-8")) if (trial / "review.json").exists() else {}
        verdict = acceptance(review, oracle)
        group["completed"] += 1
        group["chemistry_" + verdict if verdict != "unreviewed" else "unreviewed"] += 1
        group["accepted"] += verdict == "pass" and metrics["fc_ok"] is True and metrics["gate_ok"] is True
        for key in ("malformed_json_calls", "reasoning_only_replies"):
            group[key] += metrics[key]
        group["confirmed_extra_steps"] += len(review.get("extra_steps") or [])
        group["confirmed_extra_reagents"] += len(review.get("extra_reagents") or [])
        group["seconds"].append(metrics["seconds"])
    return groups


def worker(manifest_path, index):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mode, temperature, replicate = manifest["jobs"][index]
    folder = manifest_path.parent / f"{index + 1:02}-{mode}-t{temperature}-r{replicate}"
    folder.mkdir(exist_ok=False)
    os.environ.update(FLUENTVIBE_LM_REASONING_BUDGET="30000", FLUENTVIBE_LM_LOOP_GUARD="1",
                      FLUENTVIBE_PROFILE_DIR=manifest["profile"], FLUENTVIBE_FC_CHECK="1",
                      FLUENTVIBE_CURRENT_WORKTABLE_PATH=str(Path(manifest["profile"]) / "current_worktable.py"),
                      FLUENTVIBE_NO_AUTO_REBUILD="1", PYTHONDONTWRITEBYTECODE="1")
    from fluentvibe.authoring.lm_client import LMStudioChatClient
    from fluentvibe.authoring.trace import ModelTraceConfig, ModelTraceRecorder

    trace = ModelTraceRecorder(ModelTraceConfig(enabled=True, raw_stream=True, output_dir=folder))
    client = LMStudioChatClient(endpoint=manifest["endpoint"], model=manifest["model"], reasoning_effort="xhigh",
                               temperature=temperature, top_p=0.95, top_k=20, min_p=0.0,
                               presence_penalty=0.0, repetition_penalty=1.0,
                               request_timeout_s=3600, trace_recorder=trace, capture_token_ids=True)
    client.start_run_budget(manifest["trial_timeout_s"])
    answers = manifest["answers"]
    rounds = []

    def ask(questions, *, approval=False):
        # Stable question text -> explicit human answer. No fuzzy match and no
        # default acceptance of an understanding/plan that includes new chemistry.
        key = "\n".join(questions)
        answer = answers.get(key)
        if answer is None and not approval and manifest.get("clarification_facts"):
            answer = ("These are the only approved experimental facts, not acceptance of your proposed plan. "
                      "Do not infer approval of additional chemistry, steps, or values. If a question is not "
                      "settled by these facts or the document, leave it unresolved:\n" + manifest["clarification_facts"])
        rounds.append({"questions": questions, "answer": answer})
        save(folder / "questions.json", rounds)
        return answer

    started = time.monotonic()
    row = {"mode": mode, "temperature": temperature, "replicate": replicate, "fc_ok": None,
           "gate_ok": False, "stage": "error"}
    try:
        if mode == "fast":
            from fluentvibe.authoring import spec_path
            # Preserve the user's worktree patch, but do not let its unreviewed
            # auto-deletion rewrite the outcome being measured. Held fixed in
            # every cell; this is a comparison on the recorded current source,
            # not a claim to replay the historical deployment byte-for-byte.
            if hasattr(spec_path, "_settle_grounding"):
                spec_path._settle_grounding = lambda spec, *args: (spec, False)
            result = spec_path.author_from_document(client, manifest["document_text"], manifest["profile"], folder,
                                                    request=manifest["request"], ask=ask, understand=True,
                                                    fluentcontrol=True, check_requirements=True, spec_cache=None)
            save(folder / "result.json", result.summary())
            save(folder / "spec.json", result.spec_raw)
            if result.source:
                (folder / "draft.py").write_text(result.source, encoding="utf-8")
            summary = result.summary()
            row.update(stage=result.stage, fc_ok=summary.get("fc_ok"), gate_ok=bool(result.source) and result.stage == "done")
        else:
            from fluentvibe.authoring.session import PromptAuthoringSession
            session = PromptAuthoringSession(output_dir=folder, client=client, profile_dir=manifest["profile"],
                                             lab_scope="skills", retry_budget=8, trace_config=trace.config)
            text = manifest["full_prompt"]
            for _ in range(8):
                result = session.send(text)
                save(folder / "result.json", result.to_dict())
                status = result.status.value
                if status == "clarification_required":
                    text = ask([q.question for q in result.clarification_questions])
                elif status == "approval_required":
                    text = ask([json.dumps(result.approval_request.to_dict(), ensure_ascii=False, sort_keys=True)], approval=True)
                else:
                    break
                if text is None:
                    break
            row.update(stage=result.status.value, gate_ok=result.validation is not None and result.validation.success)
            source = result.generated_code or result.best_draft_code
            if source:
                (folder / "draft.py").write_text(source, encoding="utf-8")
            fc = folder / "fluentcontrol_check.json"
            if fc.exists():
                row["fc_ok"] = json.loads(fc.read_text(encoding="utf-8")).get("ok")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row.update(seconds=round(time.monotonic() - started, 2), **trace_metrics(folder))
    save(folder / "metrics.json", row)
    save(folder / "review.json", {"reviewer": None, "checks": {}, "extra_steps": None, "extra_reagents": None})
    print(json.dumps(row), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summarize", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--index", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # Standard-library and library temporary files must remain in the repo.
    import tempfile

    os.environ["TMP"] = os.environ["TEMP"] = str(REPO / "build")
    tempfile.tempdir = str(REPO / "build")
    if args.summarize:
        print(json.dumps(summarize(repo_path(args.summarize)), indent=2))
        return
    if args.worker:
        return worker(repo_path(args.worker), args.index)
    if args.config is None or args.out is None:
        parser.error("--config and --out are required")
    config = json.loads(repo_path(args.config).read_text(encoding="utf-8"))
    if config.get("request_confirmed") is not True:
        parser.error("Exact request and clarification policy must be confirmed before running")
    request_file, document_file, profile = (repo_path(config[key]) for key in ("request_file", "document_file", "profile"))
    answers = json.loads(repo_path(config["answers_file"]).read_text(encoding="utf-8")) if config.get("answers_file") else {}
    facts = repo_path(config["facts_file"]).read_text(encoding="utf-8") if config.get("facts_file") else None
    if not isinstance(answers, dict) or any(not isinstance(v, str) or not v.strip() for v in answers.values()):
        parser.error("answers_file must map exact question text to explicit nonempty human answers")
    from fluentvibe.authoring.attachments import (
        ExtractedAttachment,
        build_attachment_context,
        extract_file_text,
    )

    document, method, pages, warnings = extract_file_text(document_file)
    request = request_file.read_text(encoding="utf-8")
    attachment = ExtractedAttachment(document_file.name, "application/pdf", document_file.stat().st_size,
                                     document_file, document_file.with_suffix(document_file.suffix + ".txt"),
                                     method, pages, len(document), document, warnings)
    manifest = {"request": request, "document_text": document, "full_prompt": build_attachment_context(request, [attachment]),
                "profile": str(profile), "request_sha256": digest(request_file), "document_sha256": digest(document_file),
                "source_hashes": fingerprint(profile), "answers": answers, "clarification_facts": facts, "jobs": schedule(args.runs),
                "oracle_sha256": digest(ORACLE), "trial_timeout_s": config.get("trial_timeout_s", 7200),
                "endpoint": config.get("endpoint", "http://localhost:18020/v1/chat/completions"),
                "model": config.get("model", "qwen3.8-27b"), "grounding_policy": "bypass-uncommitted-auto-mutator",
                "python": sys.version, "lm_max_tokens": os.environ.get("FLUENTVIBE_LM_MAX_TOKENS"),
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
                "sampling": {"top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0,
                             "repetition_penalty": 1.0, "reasoning_effort": "xhigh", "reasoning_budget": 30000}}
    if args.dry_run:
        print(json.dumps({k: manifest[k] for k in ("request_sha256", "document_sha256", "jobs", "grounding_policy")}, indent=2))
        return
    out = repo_path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    manifest["server_state"] = server_state(manifest["endpoint"])
    save(out / "manifest.json", manifest)
    for index, job in enumerate(manifest["jobs"]):
        if fingerprint(profile) != manifest["source_hashes"]:
            raise RuntimeError("Source/profile changed during experiment; refusing to mix revisions")
        if server_state(manifest["endpoint"]) != manifest["server_state"]:
            raise RuntimeError("Serving process/configuration changed during experiment; refusing to mix configurations")
        print(f"Starting {index + 1}/{len(manifest['jobs'])}: {job}", flush=True)
        try:
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(out / "manifest.json"),
                            "--index", str(index)], cwd=REPO, check=True, timeout=manifest["trial_timeout_s"] + 60)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            save(out / f"worker-failure-{index + 1}.json", {"job": job, "error": str(exc)})


if __name__ == "__main__":
    main()
