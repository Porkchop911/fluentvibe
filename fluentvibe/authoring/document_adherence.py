"""Source-document adherence helpers for authoring and copilot checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceDocumentIssue:
    code: str
    severity: str
    message: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


_CONCEPTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "barcoding",
        ("rapid barcode", "rapid barcodes", "barcode", "barcoding"),
        ("barcode", "barcoding", "rb01", "rb96"),
    ),
    (
        "adapter_attachment",
        ("rapid adapter", "adapter buffer", "adapter attachment"),
        ("rapid adapter", "adapter buffer", "adb", "diluted ra"),
    ),
    (
        "flow_cell_loading",
        ("flow cell", "priming", "loading beads", "sequencing buffer"),
        ("flow cell", "prime", "priming", "load", "loading"),
    ),
    (
        "quantification",
        ("qubit", "quantify", "quantification"),
        ("qubit", "quantify", "quantification", "manual"),
    ),
    (
        "thermal_incubation",
        ("30c", "30 C", "80c", "80 C", "thermal cycler", "thermocycler"),
        ("30", "80", "thermocycler", "thermal", "incubat", "wait"),
    ),
    (
        "pooling",
        ("pool", "pooled", "combine all", "pool all"),
        ("pool", "pooled", "combine"),
    ),
    (
        "magnetic_bead_cleanup",
        ("ampure", "bead", "magnet"),
        ("ampure", "bead", "magnet"),
    ),
    (
        "ethanol_wash",
        ("ethanol", "80%"),
        ("ethanol", "etoh", "wash"),
    ),
    (
        "elution",
        ("elution buffer", " elute", "eluate"),
        ("elution", "elute", "eluate", "eb"),
    ),
)


# Adherence omissions that should gate acceptance ("automate or justify"): a
# library-prep liquid-handling stage named in the source document with no
# corresponding automated step *or* justifying comment in the protocol. Stages
# that are inherently downstream/instrument steps (flow-cell loading,
# quantification) and the informational `missing_volume` codes are deliberately
# excluded — they are surfaced in the report but never block.
GATING_CODES: frozenset[str] = frozenset({
    "missing_barcoding",
    "missing_thermal_incubation",
    "missing_pooling",
    "missing_magnetic_bead_cleanup",
    "missing_ethanol_wash",
    "missing_elution",
    "missing_adapter_attachment",
    "missing_approved_source_step",
})


def coverage_gaps(report: dict[str, Any] | None) -> list[dict[str, str]]:
    """Warning-severity adherence omissions that should gate acceptance.

    Returns the subset of ``report['issues']`` whose code is in
    :data:`GATING_CODES`. Non-gating omissions (instrument/QC stages, volume
    infos) are left in the report but excluded here so the soft gate only
    pushes on library-prep stages the deck can plausibly cover or justify.
    """
    if not report:
        return []
    return [
        issue
        for issue in report.get("issues", [])
        if issue.get("severity") == "warning" and issue.get("code") in GATING_CODES
    ]


def read_source_document(path: str | Path) -> dict[str, Any]:
    """Read a text/PDF source document and return extracted text metadata."""

    from .attachments import extract_file_text

    doc_path = Path(path)
    text, method, page_count, warnings = extract_file_text(doc_path)
    return {
        "path": str(doc_path),
        "text": text,
        "extraction_method": method,
        "page_count": page_count,
        "warnings": list(warnings),
    }


def document_adherence_report(
    *,
    source_text: str,
    protocol_source: str,
    source_name: str | None = None,
    approved_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a coarse semantic adherence report.

    This is intentionally conservative. It does not prove equivalence; it catches
    obvious omissions where the source document names a critical operation and
    the generated protocol has no corresponding term, comment, or variable.
    """

    doc_norm = _norm(source_text)
    proto_norm = _norm(protocol_source)
    issues: list[SourceDocumentIssue] = []

    for code, doc_terms, proto_terms in _CONCEPTS:
        evidence = _first_present(doc_norm, doc_terms)
        if evidence and not _first_present(proto_norm, proto_terms):
            issues.append(SourceDocumentIssue(
                code=f"missing_{code}",
                severity="warning",
                message=f"Source document mentions {code.replace('_', ' ')}, but the protocol does not.",
                evidence=evidence,
            ))

    for volume in _critical_volumes(doc_norm):
        if volume not in proto_norm:
            issues.append(SourceDocumentIssue(
                code="missing_volume",
                severity="info",
                message=f"Source document mentions volume {volume}, but the protocol source does not.",
                evidence=volume,
            ))

    if approved_plan:
        missing_plan_steps = _missing_approved_plan_steps(approved_plan, proto_norm)
        issues.extend(missing_plan_steps)

    return {
        "ok": not any(issue.severity == "error" for issue in issues),
        "source_name": source_name,
        "issue_count": len(issues),
        "issues": [issue.to_dict() for issue in issues],
    }


def source_plan_summary(plan: dict[str, Any] | None) -> str:
    if not plan:
        return ""
    title = str(plan.get("protocol_title") or plan.get("title") or "Source protocol").strip()
    steps = plan.get("steps") or ()
    counts = {"automated": 0, "manual_off_deck": 0, "unsupported": 0}
    for step in steps:
        if isinstance(step, dict):
            key = str(step.get("classification") or "").strip()
            if key in counts:
                counts[key] += 1
    bits = [f"{title}: {len(steps)} source step(s)"]
    bits.extend(f"{label}={count}" for label, count in counts.items() if count)
    return ", ".join(bits)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("µ", "u").replace("μ", "u")).lower()


def _first_present(text: str, terms: tuple[str, ...]) -> str | None:
    for term in terms:
        if _norm(term) in text:
            return term
    return None


def _critical_volumes(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"\b\d+(?:\.\d+)?\s*(?:u|m)?l\b", text, flags=re.IGNORECASE):
        value = re.sub(r"\s+", " ", match.group(0).lower()).replace("µ", "u").replace("μ", "u")
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        window = text[start:end]
        if any(term in window for term in ("barcode", "adapter", "ethanol", "elution", "flow cell", "ampure")):
            if value not in found:
                found.append(value)
    return found[:12]


def _missing_approved_plan_steps(plan: dict[str, Any], protocol_text: str) -> list[SourceDocumentIssue]:
    issues: list[SourceDocumentIssue] = []
    for index, step in enumerate(plan.get("steps") or (), start=1):
        if not isinstance(step, dict):
            continue
        classification = str(step.get("classification") or "").strip()
        if classification not in {"automated", "manual_off_deck"}:
            continue
        description = str(step.get("description") or step.get("name") or "").strip()
        keywords = [
            token.lower()
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", description)
            if token.lower() not in {"with", "from", "into", "then", "step", "using"}
        ][:4]
        if keywords and not any(keyword in protocol_text for keyword in keywords):
            issues.append(SourceDocumentIssue(
                code="missing_approved_source_step",
                severity="warning",
                message=f"Approved source step {index} is not reflected in the protocol source.",
                evidence=description[:160],
            ))
    return issues
