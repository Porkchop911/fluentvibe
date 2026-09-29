"""The fluentvibe API as it is in the code, for agents and people.

Generated from the classes and functions themselves (signature + the first
paragraph of the docstring), so it cannot fall behind the code the way a
hand-written list does (the authoring tool's list once showed 12 of the 41
Worktable methods, and a model concluded ``wt.volume`` did not exist).

``python -m fluentvibe.cli api`` lists the objects; ``api Worktable`` (or
``wt.liha``, ``blocks``, ``Plate96``, ...) prints one.
"""

from __future__ import annotations

import inspect
from typing import Any


def _objects() -> dict[str, Any]:
    import fluentvibe
    import fluentvibe.blocks as blocks
    from fluentvibe.gripper import Gripper
    from fluentvibe.heads.liha import LiHa
    from fluentvibe.heads.mca96 import MCA96Head
    from fluentvibe.worktable import Worktable

    objects: dict[str, Any] = {
        "Worktable": Worktable,
        "wt.liha": LiHa,          # the FCA: the 8-channel arm
        "wt.mca96": MCA96Head,    # the MCA: the 96-channel head
        "wt.gripper": Gripper,    # the RGA: moves labware
        "blocks": blocks,
    }
    for name in ("Reagent", "Labware", "Well", "Plate96", "Plate96Deep", "Plate384", "Trough25mL", "Trough100mL",
                 "MCA200Box", "MCA100Box", "MCA500Box", "FCA200Box", "FCA50Box", "FCA1000Box", "MagnetRack",
                 "TubeRack", "Adapter"):
        if hasattr(fluentvibe, name):
            objects[name] = getattr(fluentvibe, name)
    return objects


def _summary(obj: Any) -> str:
    doc = inspect.getdoc(obj) or ""
    return " ".join(doc.split("\n\n", 1)[0].split())


def _signature(name: str, fn: Any) -> str:
    try:
        return f"{name}{inspect.signature(fn)}"
    except (TypeError, ValueError):
        return f"{name}(...)"


def members(obj: Any) -> list[dict[str, str]]:
    """Public callables of a class or module (module: its ``__all__``)."""
    out = []
    if inspect.ismodule(obj):
        names = list(getattr(obj, "__all__", None) or [n for n in dir(obj) if not n.startswith("_")])
        for name in names:
            fn = getattr(obj, name, None)
            if callable(fn) and not inspect.isclass(fn):
                out.append({"name": name, "signature": _signature(name, fn), "doc": _summary(fn)})
        return out
    for name, fn in inspect.getmembers(obj):
        if name.startswith("_") and name != "__init__":
            continue
        if isinstance(inspect.getattr_static(obj, name, None), property):
            out.append({"name": name, "signature": f"{name} (property)", "doc": _summary(fn)})
        elif callable(fn):
            label = obj.__name__ if name == "__init__" else name
            out.append({"name": name, "signature": _signature(label, fn).replace("(self, ", "(").replace("(self)", "()"),
                        "doc": _summary(fn)})
    return out


def reference(name: str | None = None) -> dict[str, Any]:
    objects = _objects()
    if not name:
        return {"objects": {key: _summary(obj) for key, obj in objects.items()}}
    key = next((k for k in objects if k.lower() == name.lower() or k.split(".")[-1].lower() == name.lower()), None)
    if key is None:
        return {"error": f"unknown object {name!r}", "objects": sorted(objects)}
    return {"object": key, "doc": _summary(objects[key]), "members": members(objects[key])}


def format_text(ref: dict[str, Any]) -> str:
    if "error" in ref:
        return f"{ref['error']}; known: {', '.join(ref['objects'])}"
    if "objects" in ref:
        return "\n".join(f"{key:<12} {doc[:110]}" for key, doc in ref["objects"].items())
    lines = [f"{ref['object']}: {ref['doc']}", ""]
    for m in ref["members"]:
        lines.append(m["signature"])
        if m["doc"]:
            lines.append(f"    {m['doc'][:300]}")
    return "\n".join(lines)
