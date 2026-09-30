import * as vscode from "vscode";

// A proposed replacement of some lines, checked by the language server
// (fluentvibe/copilot/edit.py: propose_region / edit_region).
export interface Proposal {
  new_text: string;
  start_line: number;
  end_line: number;
  introduces_errors?: boolean;
  diagnostics?: { line: number; severity: string; message: string }[];
  import_line?: number;
  import_text?: string;
  proposed_source?: string;
}

// The proposed file, shown read-only next to the current one.
const proposals = new Map<string, string>();
const proposalScheme = "fluentvibe-proposal";
const proposalChanged = new vscode.EventEmitter<vscode.Uri>();

export function registerProposalProvider(): vscode.Disposable {
  return vscode.workspace.registerTextDocumentContentProvider(proposalScheme, {
    onDidChange: proposalChanged.event,
    provideTextDocumentContent: (uri) => proposals.get(uri.toString()) ?? "",
  });
}

/** Show the proposal as a diff, say whether it simulates, and apply it only on "Apply"
 * and only if the file has not changed since `version`. */
export async function previewAndApply(
  doc: vscode.TextDocument,
  version: number,
  result: Proposal,
  title: string
): Promise<boolean> {
  const proposalUri = vscode.Uri.parse(`${proposalScheme}:${doc.uri.path}.proposed.py?${Date.now()}`);
  proposals.set(proposalUri.toString(), result.proposed_source ?? "");
  proposalChanged.fire(proposalUri);
  await vscode.commands.executeCommand("vscode.diff", doc.uri, proposalUri, `fluentvibe: ${title}`, { preview: true });
  const problems = (result.diagnostics ?? []).filter((d) => d.severity === "error");
  const verdict = problems.length
    ? `The edited file has ${problems.length} problem(s), e.g. line ${problems[0].line}: ${problems[0].message.slice(0, 160)}`
    : "The edited file builds and simulates without problems.";
  const choice = await vscode.window.showInformationMessage(`fluentvibe: ${verdict} Apply the edit?`, "Apply", "Discard");
  proposals.delete(proposalUri.toString());
  await closeTabsFor(proposalUri);
  if (choice !== "Apply") {
    return false;
  }
  if (doc.version !== version) {
    vscode.window.showWarningMessage("fluentvibe: the file changed while the edit was prepared; nothing applied.");
    return false;
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
  return vscode.workspace.applyEdit(edit);
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
