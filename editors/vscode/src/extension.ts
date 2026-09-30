import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
  ExecuteCommandRequest,
} from "vscode-languageclient/node";

import {
  clearFluentControlDiagnostics,
  cliEnv,
  convertOpentrons,
  profileDir,
  setInstructions,
  showReplay,
  generateFromDocument,
  openInFluentControl,
  pullFluentControlEdits,
} from "./workflow";

let client: LanguageClient | undefined;

interface InlineEditResult {
  new_text: string;
  start_line: number;
  end_line: number;
  introduces_errors?: boolean;
  diagnostics?: { line: number; severity: string; message: string }[];
  import_line?: number;
  import_text?: string;
  proposed_source?: string;
}

// The proposed file for the Ctrl+I preview (a read-only virtual document).
const proposals = new Map<string, string>();
const proposalScheme = "fluentvibe-proposal";
const proposalChanged = new vscode.EventEmitter<vscode.Uri>();

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("fluentvibe.generateFromDocument", generateFromDocument),
    vscode.commands.registerCommand("fluentvibe.openInFluentControl", openInFluentControl),
    vscode.commands.registerCommand("fluentvibe.pullFluentControlEdits", pullFluentControlEdits),
    vscode.commands.registerCommand("fluentvibe.convertOpentrons", convertOpentrons),
    vscode.commands.registerCommand("fluentvibe.setInstructions", setInstructions),
    vscode.commands.registerCommand("fluentvibe.showReplay", showReplay),
    // Always registered: Ctrl+I must say why it does nothing, not "command not found".
    vscode.commands.registerCommand("fluentvibe.inlineEdit", inlineEdit),
    vscode.commands.registerCommand("fluentvibe.explainProblem", explainProblem),
    vscode.commands.registerCommand("fluentvibe.explainSelection", explainSelection),
    // InfoPad findings describe the file as it was checked; an edit invalidates them.
    vscode.workspace.onDidChangeTextDocument((e) => clearFluentControlDiagnostics(e.document)),
    vscode.workspace.registerTextDocumentContentProvider(proposalScheme, {
      onDidChange: proposalChanged.event,
      provideTextDocumentContent: (uri) => proposals.get(uri.toString()) ?? "",
    })
  );
  const config = vscode.workspace.getConfiguration("fluentvibe");
  if (!config.get<boolean>("enable", true)) {
    return;
  }
  const pythonPath = config.get<string>("pythonPath", "python");

  // The server is `python -m fluentvibe lsp`, speaking LSP over stdio. Never
  // rebuild the catalog index on startup — that would block the LSP handshake.
  // It gets the same model settings as the commands (Ctrl+I calls the model)
  // and the deck profile (wt.add() resolves against it; without it every
  // protocol that uses it shows a false error).
  const env: NodeJS.ProcessEnv = { ...cliEnv(), FLUENTVIBE_NO_AUTO_REBUILD: "1" };
  const profile = profileDir();
  if (profile) {
    env.FLUENTVIBE_PROFILE_DIR = profile;
  }
  const run = {
    command: pythonPath,
    args: ["-m", "fluentvibe", "lsp"],
    transport: TransportKind.stdio,
    options: { env },
  };
  const serverOptions: ServerOptions = { run, debug: run };

  // Only Python documents; the server itself ignores non-protocol files.
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "python" },
      { scheme: "untitled", language: "python" },
    ],
  };

  client = new LanguageClient("fluentvibe", "fluentvibe", serverOptions, clientOptions);
  context.subscriptions.push({ dispose: () => client?.stop() });
  // The server reads the settings once, at start.
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("fluentvibe")) {
        vscode.window.showInformationMessage("fluentvibe: settings changed; reload the window to apply them.",
          "Reload").then((choice) => {
          if (choice === "Reload") {
            vscode.commands.executeCommand("workbench.action.reloadWindow");
          }
        });
      }
    })
  );
  client.start().catch((err) => {
    vscode.window.showErrorMessage(`fluentvibe language server failed to start: ${err}`);
  });
}

async function inlineEdit(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return;
  }
  if (!client) {
    vscode.window.showWarningMessage("fluentvibe: the language server is off (setting fluentvibe.enable).");
    return;
  }
  const instruction = await vscode.window.showInputBox({
    prompt: "Describe the change to the selected lines",
    placeHolder: "e.g. add a return_tips step, use 200 uL tips",
  });
  if (!instruction) {
    return;
  }
  const sel = editor.selection;
  const startLine = sel.start.line + 1; // server is 1-based, inclusive
  // A selection that ends at column 0 of the next line (triple-click,
  // Shift+Down) does not include that line.
  const endLine = sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;

  const doc = editor.document;
  const version = doc.version;
  // Stop discards the answer (the model may still finish in the background).
  const result = (await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: editing…", cancellable: true },
    (_progress, cancel) =>
      Promise.race([
        client!.sendRequest(ExecuteCommandRequest.type, {
          command: "fluentvibe.applyInlineEdit",
          arguments: [{ uri: doc.uri.toString(), start_line: startLine, end_line: endLine, instruction }],
        }),
        new Promise<null>((resolve) => cancel.onCancellationRequested(() => resolve(null))),
      ])
  )) as InlineEditResult | null;

  if (!result || !result.new_text) {
    if (result) {
      vscode.window.showWarningMessage("fluentvibe: no edit was produced.");
    }
    return;
  }

  // Preview: the whole file with the edit, side by side with the current one.
  const proposalUri = vscode.Uri.parse(`${proposalScheme}:${doc.uri.path}.proposed.py?${Date.now()}`);
  proposals.set(proposalUri.toString(), result.proposed_source ?? "");
  proposalChanged.fire(proposalUri);
  await vscode.commands.executeCommand("vscode.diff", doc.uri, proposalUri, `fluentvibe edit: ${instruction}`, {
    preview: true,
  });
  const problems = (result.diagnostics ?? []).filter((d) => d.severity === "error");
  const verdict = problems.length
    ? `The edited file has ${problems.length} problem(s), e.g. line ${problems[0].line}: ${problems[0].message.slice(0, 160)}`
    : "The edited file builds and simulates without problems.";
  const choice = await vscode.window.showInformationMessage(`fluentvibe: ${verdict} Apply the edit?`, "Apply", "Discard");
  proposals.delete(proposalUri.toString());
  await closeTabsFor(proposalUri);
  if (choice !== "Apply") {
    return;
  }
  if (doc.version !== version) {
    vscode.window.showWarningMessage("fluentvibe: the file changed while the edit was prepared; nothing applied.");
    return;
  }
  const edit = new vscode.WorkspaceEdit();
  edit.replace(
    doc.uri,
    new vscode.Range(new vscode.Position(result.start_line - 1, 0), doc.lineAt(result.end_line - 1).range.end),
    result.new_text
  );
  if (result.import_text && result.import_line) {
    edit.insert(doc.uri, new vscode.Position(result.import_line - 1, 0), result.import_text + "\n");
  }
  await vscode.workspace.applyEdit(edit);
}

// Explanations run at the explain effort (setting fluentvibe.model.explainEffort, default low).
async function explainWith(title: string, command: string, args: object): Promise<void> {
  if (!client) {
    vscode.window.showWarningMessage("fluentvibe: the language server is off (setting fluentvibe.enable).");
    return;
  }
  const started = Date.now();
  const result = (await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `fluentvibe: ${title}…`, cancellable: true },
    (_progress, cancel) =>
      Promise.race([
        client!.sendRequest(ExecuteCommandRequest.type, { command, arguments: [args] }),
        new Promise<null>((resolve) => cancel.onCancellationRequested(() => resolve(null))),
      ])
  )) as { text?: string } | null;
  if (!result) {
    return;
  }
  const secs = Math.round((Date.now() - started) / 1000);
  await vscode.window.showInformationMessage(`fluentvibe: ${title} (${secs} s)`, {
    modal: true,
    detail: result.text || "(no answer)",
  });
}

async function explainProblem(args: { uri: string; diagnostic: object }): Promise<void> {
  await explainWith("explaining the problem", "fluentvibe.lsp.explainDiagnostic", args);
}

async function explainSelection(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return;
  }
  const sel = editor.selection;
  const endLine = sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;
  await editor.document.save();
  await explainWith("explaining the selection", "fluentvibe.lsp.explainSelection", {
    uri: editor.document.uri.toString(),
    start_line: sel.start.line + 1,
    end_line: endLine,
  });
}

async function closeTabsFor(uri: vscode.Uri): Promise<void> {
  for (const group of vscode.window.tabGroups.all) {
    for (const tab of group.tabs) {
      const input = tab.input as { modified?: vscode.Uri } | undefined;
      if (input?.modified?.toString() === uri.toString()) {
        await vscode.window.tabGroups.close(tab);
      }
    }
  }
}

export function deactivate(): Thenable<void> | undefined {
  return client?.stop();
}
