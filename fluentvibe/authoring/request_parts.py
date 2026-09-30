"""Conservative provenance for document excerpts pasted into a request.

Only whole clauses that also occur in the supplied document are reclassified.
An edited clause (including a user override) stays a user instruction. Unknown
text is never discarded or declared satisfied. This is routing, not a chemistry
validator, and does not claim to identify paraphrases or unattached documents.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def _words(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    # PDF line wrapping, typography and copied list markers are immaterial;
    # numbers, units, negation and other words must still agree exactly.
    text = re.sub(r"(?m)^\s*\d+[.)]\s+", "", text)
    return " ".join(re.findall(r"\w+", text))


@dataclass(frozen=True)
class RequestParts:
    instructions: str
    document_excerpts: tuple[str, ...]


def split_request_document(request: str, document: str | None) -> RequestParts:
    """Separate verbatim source clauses without guessing at user intent.

    No document, no reclassification. A short common phrase is insufficient
    evidence: require eight words, or six for an explicitly listed step.
    Keep the original request byte-for-byte if nothing was recognized.
    """
    if not document:
        return RequestParts(request, ())
    source = f" {_words(document)} "
    instructions, excerpts = [], []
    # Soft line wraps remain inside their sentence. Do not split decimal
    # numbers or interpret a colon as introducing a source block.
    clauses = re.split(r"(?<!\d\.)(?<!\bmax\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<=[.!?])\s+"
                       r"|\n\s*(?=\d+[.)]\s)|\n\s*\n", request, flags=re.IGNORECASE)
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        words = _words(clause)
        minimum = 6 if re.match(r"^(?:\d+[.)]|[•*-])\s", clause) else 8
        if len(words.split()) >= minimum and f" {words} " in source:
            excerpts.append(clause)
        else:
            instructions.append(clause)
    return RequestParts("\n".join(instructions) if excerpts else request, tuple(excerpts))


def is_source_clause(text: str, excerpts: tuple[str, ...]) -> bool:
    """Exact clause identity, not a fuzzy semantic or reagent-name match."""
    words = _words(text)
    return bool(words) and any(words == _words(excerpt) for excerpt in excerpts)
