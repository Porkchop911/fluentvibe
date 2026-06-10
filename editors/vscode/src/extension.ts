import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
  ExecuteCommandRequest,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

interface InlineEditResult {
  new_text: string;
  start_line: number;
  end_line: number;
  introduces_errors?: boolean;
  diagnostics?: { line: number; severity: string; message: string }[];
}

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("fluentvibe");
  if (!config.get<boolean>("enable", true)) {
    return;
  }
  const pythonPath = config.get<string>("pythonPath", "python");

  // The server is `python -m fluentvibe lsp`, speaking LSP over stdio. Never
  // rebuild the catalog index on startup — that would block the LSP handshake.
  const env = { ...process.env, FLUENTVIBE_NO_AUTO_REBUILD: "1" };
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
  context.subscriptions.push(
    vscode.commands.registerCommand("fluentvibe.inlineEdit", inlineEdit)
  );
  context.subscriptions.push({ dispose: () => client?.stop() });
  client.start().catch((err) => {
    vscode.window.showErrorMessage(`fluentvibe language server failed to start: ${err}`);
  });
}

async function inlineEdit(): Promise<void> {
  const editor = vscode.window.activeTextEditor;
  if (!editor || !client) {
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
  const endLine = sel.end.line + 1;

  const result = (await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "fluentvibe: editing…" },
    () =>
      client!.sendRequest(ExecuteCommandRequest.type, {
        command: "fluentvibe.applyInlineEdit",
        arguments: [
          { uri: editor.document.uri.toString(), start_line: startLine, end_line: endLine, instruction },
        ],
      })
  )) as InlineEditResult | null;

  if (!result || !result.new_text) {
    vscode.window.showWarningMessage("fluentvibe: no edit was produced.");
    return;
  }

  const range = new vscode.Range(
    new vscode.Position(result.start_line - 1, 0),
    editor.document.lineAt(result.end_line - 1).range.end
  );
  await editor.edit((b) => b.replace(range, result.new_text));

  if (result.introduces_errors) {
    vscode.window.showWarningMessage(
      "fluentvibe: the edit was applied but introduces simulator/build errors — check the diagnostics."
    );
  }
}

export function deactivate(): Thenable<void> | undefined {
  return client?.stop();
}
