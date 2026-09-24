"""Similar-protocol retrieval for Bench Spec extraction.

``spec_corpus.json`` (built by ``scripts/build_spec_corpus.py`` from the
Opentrons corpus drafts) holds, per converted protocol: its family, title,
description and the step outline the simulator recorded. Before a model
extracts a spec from a new document, :func:`retrieval_context` finds the most
similar corpus protocols by word overlap and renders their outlines as
reference structure: which stages such a protocol usually has, and typical
clean-up volumes. The model is told to take numbers from the document only.

Deterministic and model-free.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

CORPUS_PATH = Path(__file__).with_name("spec_corpus.json")

_WORD = re.compile(r"[a-z][a-z0-9]{2,}")
_STOP = frozenset(
    "the and for with from into this that are was were will then each all per use using used step steps "
    "protocol plate well wells sample samples volume add mix min minutes sec seconds room temperature "
    "after before until into onto not can may should must your you our its".split()
)


def _tokens(text: str) -> Counter:
    return Counter(w for w in _WORD.findall(text.lower()) if w not in _STOP)


@lru_cache(maxsize=4)
def load_corpus(path: str | None = None) -> tuple[dict[str, Any], ...]:
    target = Path(path) if path else CORPUS_PATH
    if not target.exists():
        return ()
    return tuple(json.loads(target.read_text(encoding="utf-8")))


def _entry_text(entry: dict[str, Any]) -> str:
    parts = [entry.get("title", ""), entry.get("description", ""), entry.get("family", "").replace("-", " ")]
    parts += [step.get("text", "") for step in entry.get("steps", [])]
    return " ".join(parts)


def similar_specs(text: str, k: int = 3, *, corpus: tuple[dict[str, Any], ...] | None = None) -> list[tuple[float, dict[str, Any]]]:
    """The ``k`` corpus entries most similar to ``text`` (TF-IDF cosine), best first."""
    entries = load_corpus() if corpus is None else corpus
    if not entries:
        return []
    docs = [_tokens(_entry_text(e)) for e in entries]
    df = Counter(w for d in docs for w in d)
    n = len(docs)

    def weigh(counts: Counter) -> dict[str, float]:
        return {w: (1 + math.log(c)) * math.log((n + 1) / (df.get(w, 0) + 1)) for w, c in counts.items()}

    query = weigh(_tokens(text[:20000]))
    qnorm = math.sqrt(sum(v * v for v in query.values())) or 1.0
    scored = []
    for entry, doc in zip(entries, docs):
        vec = weigh(doc)
        dot = sum(query.get(w, 0.0) * v for w, v in vec.items())
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        score = dot / (qnorm * norm)
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda item: -item[0])
    return scored[:k]


def _step_line(step: dict[str, Any]) -> str:
    bits = [step.get("op", "?")]
    for key, label, unit in (("volume_ul", "beads" if step.get("op") == "bead_cleanup" else "vol", " µl"),
                             ("washes", "washes", ""), ("wash_ul", "wash", " µl"),
                             ("elute_ul", "elute", " µl"), ("minutes", "min", "")):
        value = step.get(key)
        if value not in (None, [], ""):
            if isinstance(value, list):
                value = "/".join(f"{v:g}" for v in value)
            elif isinstance(value, float):
                value = f"{value:g}"
            bits.append(f"{label} {value}{unit}")
    text = (step.get("text") or "")[:70]
    return f"  - {', '.join(bits)} ({step.get('location', '?')}): {text}"


def retrieval_context(text: str, k: int = 3, *, corpus: tuple[dict[str, Any], ...] | None = None) -> str | None:
    """A prompt block with the outlines of the ``k`` most similar corpus protocols, or None."""
    hits = similar_specs(text, k, corpus=corpus)
    if not hits:
        return None
    lines = [
        "REFERENCE OUTLINES — similar protocols from a robot protocol corpus, converted automatically.",
        "Use them only to recognise the stages this kind of protocol usually has (clean-ups, washes,",
        "off-deck incubations). They are NOT this protocol: take every number, reagent and step from",
        "the document below, never from these outlines.",
    ]
    for score, entry in hits:
        lines.append("")
        lines.append(f"* {entry.get('title') or entry.get('name')} — family {entry.get('family', '?')}, "
                     f"{entry.get('sample_count') or '?'} samples (similarity {score:.2f})")
        for step in entry.get("steps", [])[:14]:
            lines.append(_step_line(step))
    return "\n".join(lines)
