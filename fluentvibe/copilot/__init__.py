"""Editor-agnostic protocol analysis for the fluentvibe copilot.

Turns a fluentvibe protocol ``.py`` into a list of structured ``Diagnostic``s
(build errors + simulation failures), each with a source line and a repair hint.
This is the headless core that the CLI ``fluentvibe check`` and, later, a VS Code
language server both call. See ``docs/copilot-design.md``.
"""

from __future__ import annotations

from .analyzer import Diagnostic, analyze_file, analyze_source

__all__ = ["Diagnostic", "analyze_file", "analyze_source"]
