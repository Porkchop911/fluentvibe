"""Compiler — renders Protocol IR to FluentControl XML."""

from .renderer import Renderer, RenderError


def render_protocol(protocol) -> str:
    """Render a Protocol IR object to a `.xscr` XML string."""
    return Renderer().render(protocol)


__all__ = ["Renderer", "RenderError", "render_protocol"]
