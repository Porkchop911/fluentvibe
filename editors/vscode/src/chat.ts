import * as vscode from "vscode";
import { ExecuteCommandRequest, LanguageClient } from "vscode-languageclient/node";

import { Proposal, previewAndApply } from "./preview";

// The fluentvibe chat: the language server supplies the grounding (open file,
// selection, problems, simulated deck state, blocks reference); the answer is
// streamed straight from the model so words appear as they are generated.

interface Turn {
  role: "user" | "assistant";
  content: string;
}

interface Target {
  doc: vscode.TextDocument;
  version: number;
  start: number; // 1-based, inclusive
  end: number;
  hasSelection: boolean;
}

export class ChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "fluentvibe.chat";
  private view?: vscode.WebviewView;
  private history: Turn[] = [];
  private abort?: AbortController;
  private target?: Target;
  private lastEditor?: vscode.TextEditor;

  constructor(private readonly client: () => LanguageClient | undefined) {
    this.lastEditor = isProtocolEditor(vscode.window.activeTextEditor) ? vscode.window.activeTextEditor : undefined;
    vscode.window.onDidChangeActiveTextEditor((e) => {
      if (isProtocolEditor(e)) {
        this.lastEditor = e;
      }
    });
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = chatHtml(nonce());
    view.webview.onDidReceiveMessage((m) => {
      if (m.type === "send") {
        void this.send(String(m.text || ""));
      } else if (m.type === "stop") {
        this.abort?.abort();
      } else if (m.type === "clear") {
        this.abort?.abort();
        this.history = [];
      } else if (m.type === "apply") {
        void this.apply(String(m.code || ""));
      }
    });
  }

  private post(message: object): void {
    void this.view?.webview.postMessage(message);
  }

  private editor(): vscode.TextEditor | undefined {
    // Only real .py files: a diff/preview tab (virtual document) is never what the chat is about.
    const active = vscode.window.activeTextEditor;
    return isProtocolEditor(active) ? active : this.lastEditor;
  }

  private async send(question: string): Promise<void> {
    if (!question.trim()) {
      return;
    }
    const client = this.client();
    const editor = this.editor();
    if (!client || !editor) {
      this.post({ type: "error", text: client ? "Open a protocol .py first." : "The fluentvibe language server is off." });
      return;
    }
    const sel = editor.selection;
    const hasSelection = !sel.isEmpty;
    const end = hasSelection && sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;
    this.target = { doc: editor.document, version: editor.document.version, start: sel.start.line + 1, end, hasSelection };
    this.post({ type: "start", question, where: hasSelection ? `lines ${sel.start.line + 1}-${end}` : `line ${sel.start.line + 1}` });

    this.abort = new AbortController();
    const started = Date.now();
    let answer = "";
    try {
      const ctx = (await client.sendRequest(ExecuteCommandRequest.type, {
        command: "fluentvibe.lsp.chatContext",
        arguments: [{ uri: editor.document.uri.toString(), start_line: this.target.start, end_line: end, has_selection: hasSelection }],
      })) as { system: string; context: string };
      if (this.abort.signal.aborted) {
        throw new Error("stopped");
      }
      // Earlier turns without their context blocks; the newest question carries the current context.
      const messages = [
        { role: "system", content: ctx.system },
        ...this.history,
        { role: "user", content: `${ctx.context}\n\nQuestion: ${question}` },
      ];
      answer = await this.stream(messages, this.abort.signal, started);
      this.history.push({ role: "user", content: question }, { role: "assistant", content: answer });
      this.post({ type: "done", secs: Math.round((Date.now() - started) / 1000) });
    } catch (err) {
      const stopped = this.abort.signal.aborted;
      if (stopped && answer) {
        this.history.push({ role: "user", content: question }, { role: "assistant", content: answer });
      }
      this.post({ type: stopped ? "stopped" : "error", text: stopped ? "Stopped." : String((err as Error).message || err) });
    } finally {
      this.abort = undefined;
    }
  }

  private async stream(messages: object[], signal: AbortSignal, started: number): Promise<string> {
    const s = vscode.workspace.getConfiguration("fluentvibe");
    const endpoint = chatCompletionsUrl(
      s.get<string>("model.endpoint") || process.env.FLUENTVIBE_LM_ENDPOINT || "http://127.0.0.1:8080/v1/chat/completions"
    );
    const model = s.get<string>("model.name") || process.env.FLUENTVIBE_LM_MODEL || "strata";
    const key = s.get<string>("model.apiKey") || process.env.FLUENTVIBE_LM_API_KEY || "";
    const body = {
      model,
      messages,
      stream: true,
      temperature: s.get<number>("model.temperature", 0.8),
      top_p: 0.95,
      top_k: 20,
      reasoning_effort: s.get<string>("model.chatEffort", "low"),
    };
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(key ? { Authorization: `Bearer ${key}` } : {}) },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok || !response.body) {
      throw new Error(`model request failed: HTTP ${response.status} ${(await response.text()).slice(0, 300)}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffered = "";
    let answer = "";
    let thinking = 0;
    let lastTick = 0;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffered += decoder.decode(value, { stream: true });
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) {
        const data = line.trim().replace(/^data:\s*/, "");
        if (!data || data === "[DONE]" || !line.trim().startsWith("data:")) {
          continue;
        }
        let delta: { content?: string; reasoning_content?: string; reasoning?: string } = {};
        try {
          delta = JSON.parse(data).choices?.[0]?.delta ?? {};
        } catch {
          continue;
        }
        const reasoning = delta.reasoning_content ?? delta.reasoning;
        if (reasoning) {
          thinking += reasoning.length;
          const now = Date.now();
          if (now - lastTick > 250) {
            lastTick = now;
            this.post({ type: "thinking", secs: Math.round((now - started) / 1000), tail: reasoning.slice(-80) });
          }
        }
        if (delta.content) {
          answer += delta.content;
          this.post({ type: "delta", text: delta.content });
        }
      }
    }
    return answer;
  }

  // Apply a code block: the server places it (line markers, matching statements, or the
  // selection), checks the result, and the diff preview writes the whole proposed file.
  private async apply(code: string): Promise<void> {
    const client = this.client();
    const t = this.target;
    if (!client || !t) {
      return;
    }
    const result = (await client.sendRequest(ExecuteCommandRequest.type, {
      command: "fluentvibe.lsp.checkProposal",
      arguments: [{ uri: t.doc.uri.toString(), start_line: t.start, end_line: t.end, has_selection: t.hasSelection,
                    new_text: code.replace(/\n$/, "") }],
    })) as FilePatch;
    if (!result.placed?.length) {
      vscode.window.showWarningMessage(
        "fluentvibe: could not tell where this code goes in the file. Select the lines it should replace and press Apply again."
      );
      return;
    }
    if (result.unplaced?.length) {
      const go = await vscode.window.showWarningMessage(
        `fluentvibe: ${result.unplaced.length} part(s) could not be placed and are left out: ${result.unplaced.join(" | ").slice(0, 200)}`,
        "Preview the rest",
        "Cancel"
      );
      if (go !== "Preview the rest") {
        return;
      }
    }
    const where = result.placed.map((p) => (p.start === p.end ? `line ${p.start}` : `lines ${p.start}-${p.end}`)).join(", ");
    if (await applyInPlace(t.doc, t.version, result, where)) {
      this.post({ type: "applied", where });
      // Later edits in this conversation refer to the file as it is now.
      this.target = { ...t, version: t.doc.version };
    }
  }
}

function isProtocolEditor(e: vscode.TextEditor | undefined): e is vscode.TextEditor {
  return !!e && e.document.uri.scheme === "file" && e.document.languageId === "python";
}

// Apply in the open document: only the changed span is replaced (one undo step),
// the result is selected and checked; "Undo" reverts it.
async function applyInPlace(doc: vscode.TextDocument, version: number, result: FilePatch, where: string): Promise<boolean> {
  if (doc.version !== version) {
    vscode.window.showWarningMessage("fluentvibe: the file changed since the answer was written; ask again.");
    return false;
  }
  const oldLines = doc.getText().split(/\r?\n/);
  const newLines = (result.proposed_source ?? "").replace(/\n$/, "").split("\n");
  let top = 0;
  while (top < oldLines.length && top < newLines.length && oldLines[top] === newLines[top]) {
    top++;
  }
  let tail = 0;
  while (tail < oldLines.length - top && tail < newLines.length - top &&
         oldLines[oldLines.length - 1 - tail] === newLines[newLines.length - 1 - tail]) {
    tail++;
  }
  if (top === oldLines.length && top === newLines.length) {
    vscode.window.showInformationMessage("fluentvibe: nothing to change; the file already has this code.");
    return false;
  }
  const oldEnd = oldLines.length - tail; // exclusive
  const replacement = newLines.slice(top, newLines.length - tail);
  const editor = await vscode.window.showTextDocument(doc, { preserveFocus: false, preview: false });
  const range = top < oldEnd
    ? new vscode.Range(top, 0, oldEnd - 1, oldLines[oldEnd - 1].length)
    : new vscode.Range(top, 0, top, 0);
  const text = replacement.join(doc.eol === vscode.EndOfLine.CRLF ? "\r\n" : "\n") + (top < oldEnd ? "" : doc.eol === vscode.EndOfLine.CRLF ? "\r\n" : "\n");
  const ok = await editor.edit((b) => b.replace(range, text));
  if (!ok) {
    return false;
  }
  const lastLine = Math.max(top, top + replacement.length - 1);
  editor.selection = new vscode.Selection(top, 0, lastLine, doc.lineAt(Math.min(lastLine, doc.lineCount - 1)).text.length);
  editor.revealRange(editor.selection, vscode.TextEditorRevealType.InCenterIfOutsideViewport);
  const problems = (result.diagnostics ?? []).filter((d) => d.severity === "error");
  const verdict = problems.length
    ? `${problems.length} problem(s) now, e.g. line ${problems[0].line}: ${problems[0].message.slice(0, 140)}`
    : "the file builds and simulates without problems";
  const choice = await (problems.length ? vscode.window.showWarningMessage : vscode.window.showInformationMessage)(
    `fluentvibe: applied to ${where}; ${verdict}.`,
    "Undo"
  );
  if (choice === "Undo") {
    await vscode.window.showTextDocument(doc);
    await vscode.commands.executeCommand("undo");
  }
  return true;
}

interface FilePatch extends Proposal {
  placed?: { start: number; end: number; how: string }[];
  unplaced?: string[];
}

// A base URL ("http://host:8080/v1" or "http://host:8080") gets the chat path; a full URL stays.
// (Same rule as fluentvibe/authoring/lm_client.py: chat_completions_url.)
export function chatCompletionsUrl(endpoint: string): string {
  const url = endpoint.trim().replace(/\/+$/, "");
  if (!url || url.endsWith("/chat/completions")) {
    return url;
  }
  if (url.endsWith("/v1")) {
    return url + "/chat/completions";
  }
  return (url.match(/\//g) || []).length <= 2 ? url + "/v1/chat/completions" : url;
}

function nonce(): string {
  let text = "";
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  for (let i = 0; i < 32; i++) {
    text += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return text;
}

export function chatHtml(n: string): string {
  return `<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${n}';">
<style>
  body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--vscode-foreground); padding: 0 6px; }
  #log { display: flex; flex-direction: column; gap: 10px; padding-bottom: 150px; }
  .q { background: var(--vscode-input-background); border-radius: 4px; padding: 6px 8px; white-space: pre-wrap; }
  .where { color: var(--vscode-descriptionForeground); font-size: 0.85em; }
  .a p { margin: 4px 0; } .a ul { margin: 4px 0; padding-left: 18px; }
  .a pre { background: var(--vscode-textCodeBlock-background); padding: 6px; overflow-x: auto; margin: 4px 0 2px; }
  .a code { font-family: var(--vscode-editor-font-family); }
  .think { color: var(--vscode-descriptionForeground); font-style: italic; font-size: 0.9em; }
  .err { color: var(--vscode-errorForeground); }
  button { background: var(--vscode-button-background); color: var(--vscode-button-foreground); border: none; padding: 3px 10px; cursor: pointer; }
  button.secondary { background: var(--vscode-button-secondaryBackground); color: var(--vscode-button-secondaryForeground); }
  #box { position: fixed; left: 0; right: 0; bottom: 0; padding: 6px; background: var(--vscode-sideBar-background); border-top: 1px solid var(--vscode-panel-border); }
  textarea { width: 100%; box-sizing: border-box; min-height: 60px; resize: vertical; background: var(--vscode-input-background); color: var(--vscode-input-foreground); border: 1px solid var(--vscode-input-border, transparent); font-family: inherit; }
  #row { display: flex; gap: 6px; margin-top: 4px; align-items: center; } #hint { flex: 1; color: var(--vscode-descriptionForeground); font-size: 0.85em; }
</style></head>
<body>
<div id="log"><div class="where">Ask about the open protocol. Select lines to ask about them or to get code that replaces them. Ctrl+Enter sends.</div></div>
<div id="box">
  <textarea id="input" placeholder="e.g. what is in the work plate after line 60?  /  why does this fail?  /  wash twice instead of three times"></textarea>
  <div id="row"><span id="hint">Ctrl+Enter to send</span>
    <button id="clear" class="secondary">Clear</button><button id="stop" class="secondary" disabled>Stop</button><button id="send">Send</button></div>
</div>
<script nonce="${n}">
const vscode = acquireVsCodeApi();
const log = document.getElementById("log"), input = document.getElementById("input");
const send = document.getElementById("send"), stop = document.getElementById("stop");
let answerEl = null, raw = "", thinkEl = null, busy = false;
function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
function inline(s) { return esc(s).replace(/\\x60([^\\x60]+)\\x60/g, "<code>$1</code>").replace(/\\*\\*([^*]+)\\*\\*/g, "<b>$1</b>"); }
function render(text, final) {
  const parts = text.split(/\\x60\\x60\\x60/);
  const blocks = [];
  let html = "";
  parts.forEach((part, i) => {
    if (i % 2 === 1) {
      const nl = part.indexOf("\\n"); const lang = nl >= 0 ? part.slice(0, nl).trim() : ""; const code = nl >= 0 ? part.slice(nl + 1) : part;
      html += "<pre><code>" + esc(code) + "</code></pre>";
      if (final && (lang === "python" || lang === "py" || lang === "") && i < parts.length - 1) {
        html += '<button class="apply" data-code="' + encodeURIComponent(code) + '">Apply</button>';
        blocks.push(code);
      }
    } else {
      const lines = part.split("\\n"); let inList = false;
      for (const l of lines) {
        const m = l.match(/^\\s*[-*]\\s+(.*)/);
        if (m) { if (!inList) { html += "<ul>"; inList = true; } html += "<li>" + inline(m[1]) + "</li>"; continue; }
        if (inList) { html += "</ul>"; inList = false; }
        if (l.trim()) html += "<p>" + inline(l) + "</p>";
      }
      if (inList) html += "</ul>";
    }
  });
  if (blocks.length > 1) {
    // Every block of the answer in one go (each carries its own "# lines" marker, or is
    // placed by its statement; "..." keeps unmarked blocks apart).
    html += '<button class="apply" data-code="' + encodeURIComponent(blocks.join("\\n...\\n")) + '">Apply all ' + blocks.length + ' changes</button>';
  }
  return html;
}
function setBusy(b) { busy = b; send.disabled = b; stop.disabled = !b; }
function submit() {
  const text = input.value.trim(); if (!text || busy) return;
  vscode.postMessage({ type: "send", text }); input.value = "";
}
send.onclick = submit;
stop.onclick = () => vscode.postMessage({ type: "stop" });
document.getElementById("clear").onclick = () => { vscode.postMessage({ type: "clear" }); log.innerHTML = ""; setBusy(false); };
input.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } });
log.addEventListener("click", (e) => { const b = e.target.closest(".apply"); if (b) vscode.postMessage({ type: "apply", code: decodeURIComponent(b.dataset.code) }); });
window.addEventListener("message", (ev) => {
  const m = ev.data;
  if (m.type === "start") {
    setBusy(true); raw = "";
    const q = document.createElement("div"); q.className = "q"; q.textContent = m.question; log.appendChild(q);
    const w = document.createElement("div"); w.className = "where"; w.textContent = "about " + m.where; log.appendChild(w);
    thinkEl = document.createElement("div"); thinkEl.className = "think"; thinkEl.textContent = "reading the protocol and simulating…"; log.appendChild(thinkEl);
    answerEl = document.createElement("div"); answerEl.className = "a"; log.appendChild(answerEl);
  } else if (m.type === "thinking" && thinkEl) {
    thinkEl.textContent = "thinking… " + m.secs + " s  " + (m.tail || "").replace(/\\s+/g, " ").slice(-90);
  } else if (m.type === "delta" && answerEl) {
    if (thinkEl) { thinkEl.remove(); thinkEl = null; }
    raw += m.text; answerEl.innerHTML = render(raw, false);
  } else if (m.type === "done") {
    if (thinkEl) { thinkEl.remove(); thinkEl = null; }
    if (answerEl) answerEl.innerHTML = render(raw, true);
    const w = document.createElement("div"); w.className = "where"; w.textContent = m.secs + " s"; log.appendChild(w);
    setBusy(false);
  } else if (m.type === "stopped" || m.type === "error") {
    if (thinkEl) { thinkEl.remove(); thinkEl = null; }
    if (answerEl && raw) answerEl.innerHTML = render(raw, true);
    const e = document.createElement("div"); e.className = m.type === "error" ? "err" : "where"; e.textContent = m.text; log.appendChild(e);
    setBusy(false);
  } else if (m.type === "applied") {
    const w = document.createElement("div"); w.className = "where"; w.textContent = "applied to " + (m.where || "the file") + "."; log.appendChild(w);
  }
  window.scrollTo(0, document.body.scrollHeight);
});
</script></body></html>`;
}
