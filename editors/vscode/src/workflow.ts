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

// Model settings become the FLUENTVIBE_LM_* variables the CLI reads.
function cliEnv(): NodeJS.ProcessEnv {
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
  set("FLUENTVIBE_LM_MAX_TOKENS", s.get<number>("model.maxTokens"));
  return env;
}

function runCli(
  args: string[],
  onLine: (line: string) => void,
  onPrompt?: (child: cp.ChildProcess, buffered: string) => void
): Promise<{ code: number; stdout: string }> {
  const python = settings().get<string>("pythonPath", "python");
  return new Promise((resolve) => {
    const child = cp.spawn(python, ["-m", "fluentvibe.cli", ...args], { cwd: workspaceRoot(), env: cliEnv() });
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
      if (onPrompt && pending.startsWith("Answer (empty to stop):")) {
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
      resolve({ code: code ?? 1, stdout });
    });
  });
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
  const args = ["author-spec", doc, "--profile", resolveInRoot(settings().get<string>("profile", "")), "-o", outDir];
  if (request.trim()) {
    args.push("--request", request.trim());
    if (settings().get<boolean>("checkInstructions", true)) {
      args.push("--check-instructions");
    }
  }
  if (settings().get<boolean>("fluentControlCheck", true)) {
    args.push("--fc-check");
  }
  if (settings().get<boolean>("chooseOpenValues", false)) {
    args.push("--choose");
  }
  output.clear();
  output.show(true);
  output.appendLine(`Document: ${doc}`);
  const started = Date.now();
  const questions: string[] = [];

  const result = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe", cancellable: false },
    (progress) =>
      runCli(
        args,
        (line) => {
          output.appendLine(line);
          const m = /^progress: (.*)$/.exec(line);
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
          const answer = await vscode.window.showInputBox({
            title: "The spec leaves this open",
            prompt: questions.join("  |  ") || "Open question",
            ignoreFocusOut: true,
          });
          questions.length = 0;
          child.stdin?.write(`${answer ?? ""}\n`);
        }
      )
  );

  const secs = Math.round((Date.now() - started) / 1000);
  const resultPath = path.join(outDir, "result.json");
  if (!fs.existsSync(resultPath)) {
    vscode.window.showErrorMessage(`fluentvibe: generation failed after ${secs} s — see the fluentvibe output.`);
    return;
  }
  const summary = JSON.parse(fs.readFileSync(resultPath, "utf8"));
  const specMd = path.join(outDir, "spec.md");
  const reqMd = path.join(outDir, "requirements.md");
  const draft = path.join(outDir, "draft.py");
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
    summary.fc_ok === false ? `FluentControl: ${summary.fc_findings.length} finding(s)` : "not checked in FluentControl";
  const todo = summary.todo_steps ? `, ${summary.todo_steps} step(s) left to author` : "";
  const ins = summary.instructions;
  const insText = ins && ins.total
    ? ` — your instructions: ${ins.verified}/${ins.total} verified` +
      (ins.failed ? `, ${ins.failed} NOT met` : "") + (ins.unverified ? `, ${ins.unverified} not checkable` : "")
    : "";
  const text = summary.stage === "done"
    ? `fluentvibe: protocol generated in ${secs} s — ${fc}${todo}${insText}.`
    : `fluentvibe: stopped at ${summary.stage} after ${secs} s: ${summary.error ?? ""}`;
  const ok = summary.stage === "done" && summary.fc_ok !== false && !(ins && ins.failed);
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
  const file = editor.document.uri.fsPath;
  const args = ["fc-open", file, "--json"];
  const profile = settings().get<string>("profile", "");
  if (profile) {
    args.push("--profile", resolveInRoot(profile));
  }
  const started = Date.now();
  const { code, stdout } = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: opening in FluentControl…" },
    () => runCli(args, (line) => output.appendLine(line))
  );
  const secs = Math.round((Date.now() - started) / 1000);
  const jsonLine = stdout.split(/\r?\n/).reverse().find((l) => l.trim().startsWith("{"));
  if (code !== 0 || !jsonLine) {
    vscode.window.showErrorMessage("fluentvibe: FluentControl check failed — see the fluentvibe output.");
    output.show(true);
    return;
  }
  const result = JSON.parse(jsonLine) as { opened: boolean; load_error: string; findings: FcFinding[] };
  if (!result.opened) {
    vscode.window.showErrorMessage(`FluentControl could not load the script: ${result.load_error}`);
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
  const args = ["fc-pull", editor.document.uri.fsPath];
  const profile = settings().get<string>("profile", "");
  if (profile) {
    args.push("--profile", resolveInRoot(profile));
  }
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
  const args = ["opentrons", file, "--profile", resolveInRoot(settings().get<string>("profile", "")), "-o", outDir];
  if (settings().get<boolean>("fluentControlCheck", true)) {
    args.push("--fc-check");
  }
  output.clear();
  output.show(true);
  output.appendLine(`Opentrons protocol: ${file}`);
  const started = Date.now();
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: Opentrons → FluentControl" },
    (progress) =>
      runCli(args, (line) => {
        output.appendLine(line);
        const m = /^progress: (.*)$/.exec(line);
        if (m) {
          progress.report({ message: `${m[1]} (${Math.round((Date.now() - started) / 1000)} s)` });
        }
      })
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
    summary.fc_ok === false ? `FluentControl: ${summary.fc_findings.length} finding(s)` : "compiled and simulated";
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
  const args = ["requirements", editor.document.uri.fsPath, "--request", request];
  const profile = settings().get<string>("profile", "");
  if (profile) {
    args.push("--profile", resolveInRoot(profile));
  }
  const { stdout } = await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: turning your instructions into a checklist…" },
    () => runCli(args, (line) => output.appendLine(line))
  );
  const start = stdout.indexOf("{");
  if (start < 0) {
    vscode.window.showErrorMessage("fluentvibe: could not build the checklist — see the fluentvibe output.");
    return;
  }
  const result = JSON.parse(stdout.slice(start)) as { verdicts: { status: string }[] };
  const passed = result.verdicts.filter((v) => v.status === "pass").length;
  // Saving again makes the language server re-check the file with the new checklist.
  await editor.document.save();
  vscode.window.showInformationMessage(
    `fluentvibe: ${result.verdicts.length} instruction(s) saved beside the protocol; ${passed} met now. ` +
      "Unmet ones show as errors on save."
  );
}
