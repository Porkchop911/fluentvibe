// pi extension for the fluentvibe A/B tests (docs/pi-incremental-test.md).
//
//   pi -e scripts/pi_protocol_guard.ts ...   with PI_TARGET=build/eval/<name>.py
//                                            (PI_DOCUMENT=<pdf>, PI_GUARD_LOG=<jsonl>)
//
// 1. No existing protocols: the model may read the core DSL source
//    (fluentvibe/, not fluentvibe/authoring or _assets), the deck folder
//    (not its current_worktable.py), the skill, the protocol PDF and its own
//    target file. Everything else (examples/, build/, tests/, docs/,
//    scripts/, README ...) is blocked, so it cannot copy a finished answer.
// 2. The simulator (simulate/check/compile/fc-open) runs only on the target,
//    and no probe scripts that build or simulate a protocol; no files are
//    written except the target.
// Every blocked call is appended to $PI_GUARD_LOG (JSON lines) for the score.

import * as fs from "fs";

const ROOT = "d:/python/fluentvibe";
const norm = (p: string) => p.replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
const TARGET = norm(process.env.PI_TARGET || "");
const TARGET_STEM = TARGET.replace(/\.py$/, "");
const LOG = process.env.PI_GUARD_LOG || "";
const PDF = norm(process.env.PI_DOCUMENT || "");
const PDF_NAME = PDF.split("/").pop() || "\u0000";

const DECK = "build/workspaces/1080_dev";
const ALLOWED_FILES = [".agents/skills/fluentvibe-incremental.md", ".agents/skills/fluentvibe/skill.md"];
const DENIED_PREFIXES = ["fluentvibe/authoring", "fluentvibe/_assets", `${DECK}/current_worktable.py`];

// Repo-relative path ("" = the root) or null when outside the repo.
function rel(raw: string, cwd = ""): string | null {
  let p = norm(raw.trim().replace(/^["']|["']$/g, ""));
  p = p.replace(/^\/([a-z])\//, "$1:/").replace(/^\/mnt\/([a-z])\//, "$1:/");
  if (p === ROOT || p === ROOT + "/") return "";
  if (p.startsWith(ROOT + "/")) return p.slice(ROOT.length + 1).replace(/\/$/, "");
  if (/^[a-z]:\//.test(p) || p.startsWith("/") || p.startsWith("~")) return null;
  const parts = (cwd ? cwd.split("/") : []).concat(p.split("/"));
  const out: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      if (!out.length) return null;
      out.pop();
    } else out.push(part);
  }
  return out.join("/");
}

function allowedRel(r: string | null): boolean {
  if (r === null) return false;
  if (r === "" || r === "fluentvibe" || r === DECK || r === "build/eval") return r !== "build/eval";
  if (TARGET && (r === TARGET || r.startsWith(TARGET_STEM + "."))) return true;
  if (DENIED_PREFIXES.some((d) => r === d || r.startsWith(d + "/") || r.startsWith(d))) return false;
  return ALLOWED_FILES.includes(r) || r.startsWith("fluentvibe/") || r.startsWith(DECK + "/");
}

function allowedPath(raw: string, cwd = ""): boolean {
  const lower = norm(raw.replace(/^["']|["']$/g, ""));
  if (PDF && (lower === PDF || lower.endsWith("/" + PDF_NAME) || lower === PDF_NAME)) return true;
  if (lower === "/dev/null") return true;
  if (/[*?]/.test(lower)) {
    // A glob: its folder must be allowed and it must not reach a denied file.
    const dir = lower.includes("/") ? lower.slice(0, lower.lastIndexOf("/")) : ".";
    const pattern = new RegExp("^" + lower.slice(lower.lastIndexOf("/") + 1)
      .replace(/[.+^${}()|[\]]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
    const d = rel(dir, cwd);
    if (d === null || d === "" || d === "build/eval" || !allowedRel(d === "" ? "" : d)) return false;
    return !DENIED_PREFIXES.some((x) => x.startsWith(d + "/") && pattern.test(x.slice(d.length + 1)));
  }
  return allowedRel(rel(raw, cwd));
}

function log(entry: object): void {
  if (!LOG) return;
  try {
    fs.appendFileSync(LOG, JSON.stringify({ at: new Date().toISOString(), ...entry }) + "\n");
  } catch {
    /* logging must never break the run */
  }
}

function block(tool: string, what: string, why: string) {
  log({ tool, what: what.slice(0, 300), why });
  return {
    block: true,
    reason:
      `Not allowed in this test: ${why}. You may read the core fluentvibe/ source (not fluentvibe/authoring), ` +
      `the deck folder build/workspaces/1080_Dev (not current_worktable.py), your skill, the protocol PDF and ` +
      `your own file ${TARGET}; no other protocols. Use 'python -m fluentvibe.cli api <object>' for the API. ` +
      `Run the simulator only on ${TARGET}, and only to confirm something you believe is done.`,
  };
}

// Path-like words of one command segment (grep patterns and code are not paths).
function pathTokens(segment: string): string[] {
  const out: string[] = [];
  const words = segment.match(/"[^"]*"|'[^']*'|[^\s;&|<>()]+/g) || [];
  for (const word of words) {
    const quoted = /^["']/.test(word);
    const w = word.replace(/^["']|["']$/g, "").replace(/^[A-Za-z_][A-Za-z0-9_]*=/, ""); // VAR=value
    if (quoted && /\s/.test(w)) {
      // Code or a sentence in quotes (python -c "..."): only file-like strings inside it count.
      for (const m of w.match(/[A-Za-z]:[\\/][^'"\s;),]+|[\w.\-]+(?:\/[\w.\-*]+)+/g) || []) out.push(m);
      continue;
    }
    if (/\\\||\||^\^|\(\?|\\[bdswBDSW]/.test(w)) continue; // a regex pattern
    out.push(w);
  }
  return out
    .filter((w) => /\//.test(w) || /^[A-Za-z]:\\/.test(w) || /\.(py|md|json|txt|xscr|yaml|yml|csv)$/i.test(w)
      || w === "fluentvibe" || w === "examples" || w === "build" || w === "tests" || w === "docs" || w === "scripts")
    .filter((w) => !/^-/.test(w) && !/^https?:/.test(w));
}

const SIM = /fluentvibe\.cli\s+(simulate|check|compile|fc-open|replay)\s+(\S+)/i;
const PROBE = /(from_workspace\(|build_worktable\(|\.simulate\(|\.compile\(|\bwt\.place\(|\bfluentvibe\.blocks\b)/;
const unquoted = (s: string) => s.replace(/"(?:\\.|[^"\\])*"|'[^']*'/g, '""');

export default function (pi: any) {
  pi.on("tool_call", async (event: any) => {
    const input = event.input || {};
    if (event.toolName === "read") {
      if (!allowedPath(String(input.path || ""))) return block("read", String(input.path), "reading this file");
    }
    if (event.toolName === "write" || event.toolName === "edit") {
      if (!TARGET || rel(String(input.path || "")) !== TARGET) {
        return block(event.toolName, String(input.path), `writing any file but ${TARGET}`);
      }
    }
    if (event.toolName !== "bash") return undefined;
    const cmd = String(input.command || "");
    if (/(python3?|py)(\s+\S+)*\s+(-c|-\s*<<)|<<\s*['"]?\w+/.test(cmd) && PROBE.test(cmd)) {
      return block("bash", cmd, "probe scripts that build or simulate a protocol");
    }
    const outside = unquoted(cmd).replace(/\d?>&\d|\d?>\s*\/dev\/null/g, "");
    if (/(^|[\s;&|])(tee|cp|mv|rm)\s/.test(outside) || />/.test(outside.replace(/->|=>|>=/g, ""))) {
      return block("bash", cmd, "writing files from the shell (write only your own file)");
    }
    let cwd = "";
    // Split into commands outside quotes only (quoted code has its own ; and newlines).
    const quotes: string[] = [];
    const skeleton = cmd.replace(/"(?:\\.|[^"\\])*"|'[^']*'/g, (q) => `\u0001${quotes.push(q) - 1}\u0001`);
    const segments = skeleton.split(/&&|\|\||;|\n|\|/).map((s) => s.replace(/\u0001(\d+)\u0001/g, (_m, i) => quotes[+i]));
    for (const segment of segments) {
      const cd = segment.match(/^\s*cd\s+(\S+)/);
      if (cd) {
        const next = rel(cd[1], cwd);
        if (next === null || !(next === "" || allowedRel(next))) return block("bash", cmd, `cd into ${cd[1]}`);
        cwd = next;
        continue;
      }
      const sim = segment.match(SIM);
      if (sim && rel(sim[2], cwd) !== TARGET) {
        return block("bash", cmd, `running the simulator on anything but ${TARGET}`);
      }
      const tokens = pathTokens(segment);
      if (/\b(grep|rg|find|tree)\b/.test(segment) && /\bfind\b|\btree\b|\brg\b|\s-[a-zA-Z]*[rR]|--recursive/.test(segment)) {
        const targets = tokens.filter((t) => rel(t, cwd) !== "");
        if ((!targets.length && cwd === "") || targets.some((t) => !allowedPath(t, cwd))) {
          return block("bash", cmd, "recursive searches outside the allowed files");
        }
      }
      const bad = tokens.filter((t) => !allowedPath(t, cwd));
      if (bad.length) return block("bash", cmd, `touching ${bad.slice(0, 3).join(", ")}`);
    }
    return undefined;
  });
}
