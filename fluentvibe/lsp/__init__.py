"""Language-server bridge for the fluentvibe copilot.

A thin LSP layer over the headless analyzer (``fluentvibe/copilot``). The server
runs analysis in an isolated subprocess (reusing ``fluentvibe check --json``) so a
malformed or non-terminating protocol buffer can never hang or compromise the
editor. Requires the optional ``[lsp]`` extra (``pip install -e ".[lsp]"``).
"""

from __future__ import annotations

__all__ = ["create_server", "main"]


def __getattr__(name: str):  # pragma: no cover - thin lazy import
    # Lazily import so `import fluentvibe.lsp` doesn't hard-require pygls until
    # the server is actually used.
    if name in __all__:
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
