"""Decompiler — turn an .xscr back into a fluentvibe Python protocol.

The reverse of the renderer: parse FluentControl XML into a Pydantic
``Protocol`` IR (xscr_parser), then emit a self-contained Python source
file with a ``build_worktable()`` factory that, when executed, re-emits
the same .xscr (codegen).
"""

from .xscr_parser import parse_xscr
from .codegen import emit_python
from .corpus import CorpusResult, run_decompiled_corpus, summarize_corpus_results

__all__ = [
    "parse_xscr",
    "emit_python",
    "CorpusResult",
    "run_decompiled_corpus",
    "summarize_corpus_results",
]
