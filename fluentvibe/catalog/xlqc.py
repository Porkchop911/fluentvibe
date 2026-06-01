"""Parse a FluentControl ``.xlqc`` (liquid class definition) file.

Each `.xlqc` file is named after a GUID; that filename GUID is the
identifier the renderer references in the `.xscr`'s top-level
``<Reference TypeId="LiquidClass">``. The XML body carries a display
name, pipetting device type references (Fca / Mca96 / Mca384 / AirFca),
and the actual pipetting micro-script; only compact metadata is needed
for the SQL catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

from .xcmp import _find, _local, _text


@dataclass(frozen=True)
class XlqcLiquidClass:
    """Parsed metadata for one ``.xlqc`` file.

    ``guid`` is taken from the filename (what the renderer references);
    the inner ``<UniqueId>`` is a different identifier we ignore.
    """

    guid: str
    name: str
    head: Optional[str]
    file_path: Path
    supported_heads: tuple[str, ...] = ()


def load_xlqc(path: Path | str) -> XlqcLiquidClass:
    """Parse a `.xlqc` file. Filename GUID becomes ``guid`` field."""
    return _load_xlqc_cached(str(path))


@lru_cache(maxsize=2048)
def section_names(path: Path | str) -> tuple[str, ...]:
    """Return the micro-script section names of a `.xlqc` (e.g. ``Aspirate``,
    ``Dispense``, ``Mix``).

    Each ``<MicroScriptSection>`` carries a child ``<Name>`` whose text is the
    section label FluentControl checks at load time — a Mix step against a
    liquid class with no ``Mix`` section trips
    ``Liquid subclass section "Mix" is missing``.
    """
    root = ET.parse(str(path)).getroot()
    names: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str) or _local(el.tag) != "MicroScriptSection":
            continue
        # The section's own name is a direct <Name> child; fall back to the
        # first descendant <Name> if the structure ever nests it.
        name_el = next(
            (c for c in list(el) if isinstance(c.tag, str) and _local(c.tag) == "Name"),
            None,
        )
        if name_el is None:
            name_el = next(
                (c for c in el.iter() if isinstance(c.tag, str) and _local(c.tag) == "Name"),
                None,
            )
        txt = (name_el.text or "").strip() if name_el is not None else ""
        if txt:
            names.append(txt)
    return tuple(names)


@lru_cache(maxsize=2048)
def _load_xlqc_cached(path_str: str) -> XlqcLiquidClass:
    path = Path(path_str)
    tree = ET.parse(path_str)
    root = tree.getroot()

    payload = _find(root, "Payload")
    name = _text(_find(payload, "ObjectName")) or path.stem

    # Look for every <PipettingDeviceType> under PayloadData. Some liquid
    # classes contain multiple scripts and can be valid for more than one head.
    head: Optional[str] = None
    supported_heads: list[str] = []
    payload_data = _find(payload, "PayloadData") if payload is not None else None
    if payload_data is not None:
        for elem in payload_data.iter():
            if not isinstance(elem.tag, str):
                continue
            if _local(elem.tag) == "PipettingDeviceType":
                head_text = (elem.text or "").strip()
                if head_text:
                    if head is None:
                        head = head_text
                    if head_text not in supported_heads:
                        supported_heads.append(head_text)

    # The filename GUID is what the .xscr's <Reference> uses.
    guid = path.stem

    return XlqcLiquidClass(
        guid=guid,
        name=name,
        head=head,
        file_path=path,
        supported_heads=tuple(supported_heads),
    )
