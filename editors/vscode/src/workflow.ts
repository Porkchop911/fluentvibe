// Document -> protocol and FluentControl commands. They run the fluentvibe CLI
// (`python -m fluentvibe.cli ...`) in the workspace folder and stream its output;
// all the logic lives in Python.
import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

const output = vscode.window.createOutputChannel("fluentvibe");
const fcDiagnostics = vscode.languages.createDiagnosticCollection("fluentcontrol");

function settings() {
  return vscode.workspace.getConfiguration("fluentvibe");
}

function workspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function resolveInRoot(p: string): string {
  const root = workspaceRoot() ?? "";
  return path.isAbsolute(p) ? p : path.join(root, p);
}

// The deck profile setting as an absolute path, or undefined when not set
// (an empty setting must not become the workspace root).
export function profileDir(): string | undefined {
  const profile = (settings().get<string>("profile", "") ?? "").trim();
  return profile ? resolveInRoot(profile) : undefined;
}

function profileArgs(): string[] {
  const dir = profileDir();
  return dir ? ["--profile", dir] : [];
}

// Model settings become the FLUENTVIBE_LM_* variables the CLI reads.
export function cliEnv(): NodeJS.ProcessEnv {
  const s = settings();
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" };
  const set = (name: string, value: string | number | undefined) => {
    if (value !== undefined && value !== "") {
      env[name] = String(value);
    }
  };
  set("FLUENTVIBE_LM_ENDPOINT", s.get<string>("model.endpoint"));
  set("FLUENTVIBE_LM_MODEL", s.get<string>("model.name"));
  set("FLUENTVIBE_LM_API_KEY", s.get<string>("model.apiKey"));
  set("FLUENTVIBE_LM_REASONING_EFFORT", s.get<string>("model.reasoningEffort"));
  set("FLUENTVIBE_LM_TEMPERATURE", s.get<number>("model.temperature"));
  // 0 (the default): no limit. A limit as large as the model's context leaves no room for the prompt.
  const maxTokens = s.get<number>("model.maxTokens");
  set("FLUENTVIBE_LM_MAX_TOKENS", maxTokens && maxTokens > 0 ? maxTokens : undefined);
  set("FLUENTVIBE_EXPLAIN_EFFORT", s.get<string>("model.explainEffort"));
  return env;
}

function runCli(
  args: string[],
  onLine: (line: string) => void,
  onPrompt?: (child: cp.ChildProcess, buffered: string) => void,
  extraEnv: NodeJS.ProcessEnv = {},
  cancel?: vscode.CancellationToken
): Promise<{ code: number; stdout: string }> {
  const python = settings().get<string>("pythonPath", "python");
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: { code: number; stdout: string }) => {
      if (!settled) {
        settled = true;
        resolve(result);
      }
    };
    const child = cp.spawn(python, ["-m", "fluentvibe.cli", ...args], {
      cwd: workspaceRoot(),
      env: { ...cliEnv(), ...extraEnv },
    });
    // A missing interpreter never emits "close": without this the progress
    // notification would spin forever.
    child.on("error", (err) => {
      output.appendLine(`Could not start ${python}: ${err.message}`);
      vscode.window.showErrorMessage(
        `fluentvibe: could not start "${python}" (${err.message}). Set fluentvibe.pythonPath.`
      );
      finish({ code: 127, stdout });
    });
    // Cancel ends the CLI and everything it started (the FluentControl
    // check runs in a child process); its open model request closes with it.
    cancel?.onCancellationRequested(() => killTree(child));
    let stdout = "";
    let pending = "";
    const feed = (chunk: Buffer) => {
      const text = chunk.toString("utf8");
      stdout += text;
      pending += text;
      const lines = pending.split(/\r?\n/);
      pending = lines.pop() ?? "";
      lines.forEach(onLine);
      // A prompt waits without a newline: hand it over.
      if (onPrompt && pending.startsWith("Answer (")) {
        const p = pending;
        pending = "";
        onPrompt(child, p);
      }
    };
    child.stdout.on("data", feed);
    child.stderr.on("data", (chunk: Buffer) => {
      chunk
        .toString("utf8")
        .split(/\r?\n/)
        .filter((l) => l.trim() && !/pydantic|FieldInfo/i.test(l))
        .forEach((l) => output.appendLine(l));
    });
    child.on("close", (code) => {
      if (pending) {
        onLine(pending);
      }
      finish({ code: code ?? 1, stdout });
    });
  });
}

function killTree(child: cp.ChildProcess): void {
  if (process.platform === "win32" && child.pid) {
    // child.kill() ends only the python process on Windows, not its children.
    cp.spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
  } else {
    child.kill();
  }
}

// The JSON result a command prints last: the last line-start "{" that parses
// (a log line can contain braces, e.g. "[lm] ... ({...})").
function lastJson<T>(stdout: string): T | undefined {
  const starts: number[] = [];
  const re = /^\{/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(stdout)) !== null) {
    starts.push(m.index);
  }
  for (const start of starts.reverse()) {
    const end = stdout.indexOf("\n}", start);
    for (const text of [stdout.slice(start), end >= 0 ? stdout.slice(start, end + 2) : "", stdout.slice(start).split(/\r?\n/)[0]]) {
      try {
        return JSON.parse(text) as T;
      } catch {
        // not this one
      }
    }
  }
  return undefined;
}

export async function generateFromDocument(): Promise<void> {
  const picked = await vscode.window.showOpenDialog({
    canSelectMany: false,
    openLabel: "Generate protocol",
    filters: { "Protocol documents": ["pdf", "txt", "md", "docx"] },
  });
  if (!picked?.length) {
    return;
  }
  const doc = picked[0].fsPath;
  const request = await vscode.window.showInputBox({
    prompt: "What should be automated? (scope, samples, special instructions — optional)",
    placeHolder: "e.g. 24 samples of 20 ul, elute into a new plate",
    ignoreFocusOut: true,
  });
  if (request === undefined) {
    return;
  }
  const stem = path.basename(doc, path.extname(doc)).replace(/[^0-9A-Za-z_-]+/g, "_").slice(0, 40);
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const outDir = resolveInRoot(path.join(settings().get<string>("outputDir", "build/eval"), `${stem}-${stamp}`));
  const checkInstructions = request.trim() && settings().get<boolean>("checkInstructions", true);
  // Full Python only: the Fast path (document -> spec -> blocks) was removed
  // after a 16-run eval on Strata (not faster, and it missed what Full got).
  const args = ["author", "--document", doc, ...profileArgs(), "--output-dir", outDir, "--lab-scope", "skills",
                "--model-trace"];
  if (checkInstructions) {
    args.push("--check-instructions");
  }
  // The request last, after "--": it may start with "-".
  args.push("--", request.trim() || "Automate this protocol on this deck.");
  const extraEnv: NodeJS.ProcessEnv = settings().get<boolean>("fluentControlCheck", true)
    ? { FLUENTVIBE_FC_CHECK: "1" } : {};
  output.clear();
  output.show(true);
  output.appendLine(`Document: ${doc}`);
  const started = Date.now();
  const questions: string[] = [];

  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe", cancellable: true },
    (progress, cancelToken) =>
      runCli(
        args,
        (line) => {
          output.appendLine(line);
          const m = /^progress: (.*)$/.exec(line) || /^\[(?:lm|graph)\] (.{0,80})/.exec(line);
          if (m) {
            const secs = Math.round((Date.now() - started) / 1000);
            progress.report({ message: `${m[1]} (${secs} s)` });
          }
          const q = /^\s+- (.*)$/.exec(line);
          if (q) {
            questions.push(q[1]);
          }
        },
        async (child) => {
          // One box per question; empty keeps the assumption, Esc stops the run.
          const answers: string[] = [];
          let stopped = false;
          for (const [i, q] of questions.entries()) {
            const answer = await vscode.window.showInputBox({
              title: `Please check (${i + 1}/${questions.length}): empty keeps it, Esc stops`,
              prompt: q,
              ignoreFocusOut: true,
            });
            if (answer === undefined) {
              stopped = true;
              break;
            }
            if (answer.trim()) {
              answers.push(`${q} -> ${answer.trim()}`);
            }
          }
          questions.length = 0;
          child.stdin?.write(stopped ? "stop\n" : `${answers.join(" | ")}\n`);
        },
        extraEnv,
        cancelToken
      )
  );

  const secs = Math.round((Date.now() - started) / 1000);
  const resultPath = path.join(outDir, "result.json");
  if (!fs.existsSync(resultPath)) {
    vscode.window.showErrorMessage(
      result.code === 127 ? "fluentvibe: Python could not be started — see the fluentvibe output."
        : `fluentvibe: generation failed after ${secs} s — see the fluentvibe output.`);
    return;
  }
  const summary = JSON.parse(fs.readFileSync(resultPath, "utf8"));
  const specMd = path.join(outDir, "spec.md");
  const reqMd = path.join(outDir, "requirements.md");
  const draft = summary.draft ? summary.draft : path.join(outDir, "draft.py");
  if (fs.existsSync(specMd)) {
    await vscode.commands.executeCommand("markdown.showPreviewToSide", vscode.Uri.file(specMd));
  }
  if (fs.existsSync(reqMd)) {
    await vscode.commands.executeCommand("markdown.showPreviewToSide", vscode.Uri.file(reqMd));
  }
  if (fs.existsSync(draft)) {
    const opened = await vscode.workspace.openTextDocument(draft);
    await vscode.window.showTextDocument(opened, vscode.ViewColumn.One);
  }
  const fc = summary.fc_ok === true ? "FluentControl: no InfoPad errors" :
    summary.fc_ok === false ? `FluentControl: ${(summary.fc_findings ?? []).length} finding(s)` : "not checked in FluentControl";
  const todo = summary.todo_steps ? `, ${summary.todo_steps} step(s) left to author` : "";
  const ins = summary.instructions;
  const insText = ins && ins.total
    ? ` — your instructions: ${ins.verified}/${ins.total} verified` +
      (ins.failed ? `, ${ins.failed} NOT met` : "") + (ins.unverified ? `, ${ins.unverified} not checkable` : "")
    : "";
  // Fast reports "done", Full Python "success".
  const done = summary.stage === "done" || summary.stage === "success";
  if (summary.stage === "clarification_required") {
    const asked = (summary.questions ?? questions).join(" | ");
    vscode.window.showWarningMessage(
      `fluentvibe: the model needs answers before it writes the protocol: ${asked || "see the fluentvibe output"}. ` +
      "Add them to your request and run again.");
    return;
  }
  const text = done
    ? `fluentvibe: protocol generated in ${secs} s — ${fc}${todo}${insText}.`
    : `fluentvibe: stopped at ${summary.stage} after ${secs} s: ${summary.error ?? ""}`;
  const ok = done && summary.fc_ok !== false && !(ins && ins.failed);
  (ok ? vscode.window.showInformationMessage : vscode.window.showWarningMessage)(text);
}

interface FcFinding {
  kind: string;
  message: string;
  count: number;
  python_lines: number[];
  hint: string;
}

export async function openInFluentControl(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== "python") {
    vscode.window.showWarningMessage("fluentvibe: open a protocol .py first.");
    return;
  }
  await editor.document.save();
  const checkedVersion = editor.document.version;
  const file = editor.document.uri.fsPath;
  const args = ["fc-open", file, "--json", ...profileArgs()];
  const started = Date.now();
  const { code, stdout } = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: opening in FluentControl…", cancellable: true },
    (_progress, cancelToken) => runCli(args, (line) => output.appendLine(line), undefined, {}, cancelToken)
  );
  const secs = Math.round((Date.now() - started) / 1000);
  const result = lastJson<{ opened: boolean; load_error: string; findings: FcFinding[] }>(stdout);
  if (code !== 0 || !result) {
    vscode.window.showErrorMessage("fluentvibe: FluentControl check failed — see the fluentvibe output.");
    output.show(true);
    return;
  }
  if (!result.opened) {
    vscode.window.showErrorMessage(`FluentControl: ${result.load_error}`);
    return;
  }
  if (editor.document.version !== checkedVersion) {
    // Line numbers would point into a different revision: do not mark them.
    fcDiagnostics.delete(editor.document.uri);
    vscode.window.showWarningMessage(
      `FluentControl checked the version saved ${secs} s ago (${result.findings.length} finding(s)); ` +
      "the file changed since. Run Open in FluentControl again for the current version."
    );
    return;
  }
  const diagnostics: vscode.Diagnostic[] = [];
  for (const f of result.findings) {
    const lines = f.python_lines.length ? f.python_lines : [1];
    for (const n of lines) {
      const line = Math.max(0, Math.min(n - 1, editor.document.lineCount - 1));
      const d = new vscode.Diagnostic(
        editor.document.lineAt(line).range,
        `FluentControl: ${f.message} (x${f.count}) — ${f.hint}`,
        vscode.DiagnosticSeverity.Error
      );
      d.source = "FluentControl InfoPad";
      diagnostics.push(d);
    }
  }
  fcDiagnostics.set(editor.document.uri, diagnostics);
  if (result.findings.length) {
    vscode.window.showWarningMessage(`FluentControl InfoPad: ${result.findings.length} finding(s) (${secs} s) — marked in the editor.`);
  } else {
    vscode.window.showInformationMessage(`FluentControl: opened, no InfoPad errors (${secs} s).`);
  }
}

export async function pullFluentControlEdits(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return;
  }
  const args = ["fc-pull", editor.document.uri.fsPath, ...profileArgs()];
  output.show(true);
  output.appendLine("\n--- FluentControl edits ---");
  await runCli(args, (line) => output.appendLine(line));
}

export function clearFluentControlDiagnostics(doc: vscode.TextDocument): void {
  fcDiagnostics.delete(doc.uri);
}

export async function convertOpentrons(): Promise<void> {
  const picked = await vscode.window.showOpenDialog({
    canSelectMany: false,
    openLabel: "Convert to FluentControl",
    filters: { "Opentrons protocol": ["py"] },
  });
  if (!picked?.length) {
    return;
  }
  const file = picked[0].fsPath;
  const stem = path.basename(file, ".py").replace(/[^0-9A-Za-z_-]+/g, "_").slice(0, 40);
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const outDir = resolveInRoot(path.join(settings().get<string>("outputDir", "build/eval"), `ot-${stem}-${stamp}`));
  const args = ["opentrons", file, ...profileArgs(), "-o", outDir];
  if (settings().get<boolean>("fluentControlCheck", true)) {
    args.push("--fc-check");
  }
  output.clear();
  output.show(true);
  output.appendLine(`Opentrons protocol: ${file}`);
  const started = Date.now();
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: Opentrons → FluentControl", cancellable: true },
    (progress, cancelToken) =>
      runCli(args, (line) => {
        output.appendLine(line);
        const m = /^progress: (.*)$/.exec(line);
        if (m) {
          progress.report({ message: `${m[1]} (${Math.round((Date.now() - started) / 1000)} s)` });
        }
      }, undefined, {}, cancelToken)
  );
  const secs = Math.round((Date.now() - started) / 1000);
  const resultPath = path.join(outDir, "result.json");
  if (!fs.existsSync(resultPath)) {
    vscode.window.showErrorMessage(`fluentvibe: conversion failed after ${secs} s — see the fluentvibe output.`);
    return;
  }
  const summary = JSON.parse(fs.readFileSync(resultPath, "utf8"));
  const specMd = path.join(outDir, "spec.md");
  const draft = path.join(outDir, "draft.py");
  if (fs.existsSync(specMd)) {
    await vscode.commands.executeCommand("markdown.showPreviewToSide", vscode.Uri.file(specMd));
  }
  if (fs.existsSync(draft)) {
    await vscode.window.showTextDocument(await vscode.workspace.openTextDocument(draft), vscode.ViewColumn.One);
  }
  const fc = summary.fc_ok === true ? "FluentControl: no InfoPad errors" :
    summary.fc_ok === false ? `FluentControl: ${(summary.fc_findings ?? []).length} finding(s)` : "compiled and simulated";
  if (summary.stage === "done") {
    vscode.window.showInformationMessage(`fluentvibe: Opentrons protocol converted in ${secs} s — ${fc}.`);
  } else {
    vscode.window.showWarningMessage(`fluentvibe: stopped at ${summary.stage}: ${summary.error ?? ""}`);
  }
}

export async function setInstructions(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== "python") {
    vscode.window.showWarningMessage("fluentvibe: open a protocol .py first.");
    return;
  }
  const request = await vscode.window.showInputBox({
    title: "Instructions for this protocol",
    prompt: "What must this protocol do? They become a checklist, checked on every save.",
    placeHolder: "e.g. ethanol via the FCA, liquid classes as string variables, 20 ul samples, whole plate",
    ignoreFocusOut: true,
  });
  if (!request) {
    return;
  }
  await editor.document.save();
  const args = ["requirements", editor.document.uri.fsPath, `--request=${request}`, ...profileArgs()];
  const { stdout } = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: turning your instructions into a checklist…",
      cancellable: true },
    (_progress, cancelToken) => runCli(args, (line) => output.appendLine(line), undefined, {}, cancelToken)
  );
  const result = lastJson<{ verdicts: { status: string }[] }>(stdout);
  if (!result || !Array.isArray(result.verdicts)) {
    vscode.window.showErrorMessage("fluentvibe: could not build the checklist — see the fluentvibe output.");
    return;
  }
  const passed = result.verdicts.filter((v) => v.status === "pass").length;
  // Saving again makes the language server re-check the file with the new checklist.
  await editor.document.save();
  vscode.window.showInformationMessage(
    `fluentvibe: ${result.verdicts.length} instruction(s) saved beside the protocol; ${passed} met now. ` +
      "Unmet ones show as errors on save."
  );
}

export async function showReplay(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.languageId !== "python") {
    vscode.window.showWarningMessage("fluentvibe: open a protocol .py first.");
    return;
  }
  await editor.document.save();
  const file = editor.document.uri.fsPath;
  const out = file.replace(/\.py$/i, "") + ".replay.html";
  const args = ["replay", file, "-o", out, ...profileArgs()];
  const { code } = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: simulating the protocol for the replay…",
      cancellable: true },
    (_progress, cancelToken) => runCli(args, (line) => output.appendLine(line), undefined, {}, cancelToken)
  );
  if (code !== 0 || !fs.existsSync(out)) {
    vscode.window.showErrorMessage("fluentvibe: replay failed — see the fluentvibe output.");
    output.show(true);
    return;
  }
  const panel = vscode.window.createWebviewPanel(
    "fluentvibeReplay",
    `Replay: ${path.basename(file)}`,
    vscode.ViewColumn.Beside,
    { enableScripts: true }
  );
  panel.webview.html = fs.readFileSync(out, "utf8");
}
