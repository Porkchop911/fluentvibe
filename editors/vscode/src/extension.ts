import * as vscode from "vscode";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

let client: LanguageClient | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("fluentvibe");
  if (!config.get<boolean>("enable", true)) {
    return;
  }
  const pythonPath = config.get<string>("pythonPath", "python");

  // The server is `python -m fluentvibe lsp`, speaking LSP over stdio.
  const serverOptions: ServerOptions = {
    run: { command: pythonPath, args: ["-m", "fluentvibe", "lsp"], transport: TransportKind.stdio },
    debug: { command: pythonPath, args: ["-m", "fluentvibe", "lsp"], transport: TransportKind.stdio },
  };

  // Only Python documents; the server itself ignores non-protocol files.
  const clientOptions: LanguageClientOptions = {
    documentSelector: [
      { scheme: "file", language: "python" },
      { scheme: "untitled", language: "python" },
    ],
  };

  client = new LanguageClient(
    "fluentvibe",
    "fluentvibe",
    serverOptions,
    clientOptions
  );
  context.subscriptions.push({ dispose: () => client?.stop() });
  client.start();
}

export function deactivate(): Thenable<void> | undefined {
  return client?.stop();
}
