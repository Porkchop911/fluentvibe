"""A searchable index of the Opentrons and PyLabRobot protocol collections.

The folders are named by slug or hash (``0000025894``, ``00222e``); the index
reads each protocol's own description so it can be found by what it does:

* ``library``: Opentrons Protocol Library downloads (``<slug>/metadata.json``,
  ``README.md``, the ``.py``), e.g. ``D:/Opentron_protocols``;
* ``git``: the ``Opentrons/Protocols`` repository (``protocols/<id>/README.md``
  with Categories, Description, Labware, Pipettes, Modules, Protocol Steps);
* ``sdk``: protocols inside the Opentrons SDK sources (mostly test fixtures:
  analyses snapshots, hardware tests);
* ``pylabrobot``: PyLabRobot documentation notebooks (examples for Hamilton /
  OT-2 hardware; not Opentrons protocols, not convertible).

Roots come from ``FLUENTVIBE_PROTOCOL_ROOTS`` (``;``-separated) or the
defaults below; the index is cached in ``build/protocol_index.json`` (rebuild
with ``fluentvibe protocols index``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ROOTS_ENV = "FLUENTVIBE_PROTOCOL_ROOTS"
DEFAULT_ROOTS = (Path("D:/Opentron_protocols"), Path("D:/opentron_and_pylab_git"))
_REPO = Path(__file__).resolve().parents[1]


def default_cache() -> Path:
    """``build/protocol_index.json`` of this checkout, or of the enclosing one
    (a worktree under ``.worktrees`` has no build folder of its own)."""
    for folder in (_REPO, *_REPO.parents):
        if (folder / "build").is_dir():
            return folder / "build" / "protocol_index.json"
    return _REPO / "build" / "protocol_index.json"


@dataclass
class ProtocolEntry:
    id: str                       # what `show` and `fluentvibe opentrons` accept
    source: str                   # library | git | sdk | pylabrobot
    title: str
    path: str                     # the protocol .py (or the notebook)
    folder: str
    convertible: bool             # an Opentrons protocol the converter can run
    description: str = ""
    categories: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    labware: list[str] = field(default_factory=list)
    pipettes: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    robot: str = ""               # OT-2 | Flex | ""
    api_level: str = ""

    def text(self) -> str:
        return " ".join([self.id, self.title, self.description, *self.categories, *self.steps,
                         *self.labware, *self.pipettes, *self.modules, self.robot, self.source]).lower()


# --- reading ------------------------------------------------------------------

_SECTION_RE = re.compile(r"^#{2,3}\s*(.+?)\s*$", re.M)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _sections(markdown: str) -> dict[str, str]:
    """``## Heading`` -> body, headings lower-cased."""
    out: dict[str, str] = {}
    marks = list(_SECTION_RE.finditer(markdown))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(markdown)
        out.setdefault(m.group(1).strip().lower(), markdown[m.end():end].strip())
    return out


def _bullets(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        m = re.match(r"\s*(?:[*\-]|\d+\.)\s+(.*)", line)
        if m:
            item = _LINK_RE.sub(r"\1", m.group(1)).strip()
            if item:
                items.append(item)
    return items


def _plain(text: str, limit: int = 600) -> str:
    text = _LINK_RE.sub(r"\1", text)
    text = re.sub(r"[#*`>]+", " ", text)
    return " ".join(text.split())[:limit]


def _python_facts(source: str) -> dict[str, str]:
    """protocolName / apiLevel / robotType from the metadata and requirements dicts."""
    facts = {}
    for key in ("protocolName", "apiLevel", "robotType", "description"):
        m = re.search(rf"""['"]{key}['"]\s*:\s*['"]([^'"]+)['"]""", source)
        if m:
            facts[key] = m.group(1).strip()
    return facts


def _robot(*texts: str) -> str:
    joined = " ".join(texts).lower()
    if "flex" in joined:
        return "Flex"
    if "ot-2" in joined or "ot2" in joined:
        return "OT-2"
    return ""


def _library(root: Path) -> Iterable[ProtocolEntry]:
    for meta_path in sorted(root.glob("*/metadata.json")):
        folder = meta_path.parent
        try:
            meta = json.loads(_read(meta_path))
        except ValueError:
            continue
        readme = _read(folder / "README.md")
        py = folder / str(meta.get("saved_protocol_file") or meta.get("filename") or "")
        if not py.is_file():
            py = next(iter(sorted(folder.glob("*.py"))), folder)
        body = readme.split("\n", 1)[1] if "\n" in readme else ""
        intro = re.split(r"^##\s", body, maxsplit=1, flags=re.M)[0]
        steps = _bullets(intro)
        description = _plain(re.sub(r"(?m)^\s*\d+\.\s+.*$", "", intro).split("This protocol was authored")[0])
        facts = _python_facts(_read(py)) if py.is_file() else {}
        yield ProtocolEntry(
            id=folder.name, source="library", title=str(meta.get("name") or facts.get("protocolName") or folder.name),
            path=str(py), folder=str(folder), convertible=py.is_file() and py.suffix == ".py",
            description=description, steps=steps, robot=_robot(facts.get("robotType", ""), readme),
            api_level=facts.get("apiLevel", ""),
        )


def _git(repo: Path) -> Iterable[ProtocolEntry]:
    for readme_path in sorted(repo.glob("protocols/*/README.md")):
        folder = readme_path.parent
        readme = _read(readme_path)
        sections = _sections(readme)
        title_match = re.search(r"^#\s+(.+)$", readme, re.M)
        pys = sorted(p for p in folder.glob("*.py") if "seed" not in p.name.lower())
        py = next((p for p in pys if p.name.endswith(".apiv2.py")), pys[0] if pys else None)
        facts = _python_facts(_read(py)) if py else {}
        yield ProtocolEntry(
            id=f"git:{folder.name}", source="git",
            title=(title_match.group(1).strip() if title_match else facts.get("protocolName", folder.name)),
            path=str(py or folder), folder=str(folder), convertible=py is not None,
            description=_plain(sections.get("description", "")),
            categories=_bullets(sections.get("categories", "")),
            steps=_bullets(sections.get("protocol steps", "") or sections.get("process", ""))[:20],
            labware=_bullets(sections.get("labware", "")),
            pipettes=_bullets(sections.get("pipettes", "")),
            modules=_bullets(sections.get("modules", "")),
            robot=_robot(sections.get("robot", ""), facts.get("robotType", ""), py.name if py else ""),
            api_level=facts.get("apiLevel", ""),
        )


def _sdk(root: Path) -> Iterable[ProtocolEntry]:
    for py in sorted(root.rglob("*.py")):
        source = _read(py)
        if "def run(" not in source or not re.search(r"\b(metadata|requirements)\s*=", source):
            continue
        facts = _python_facts(source)
        rel = py.relative_to(root).as_posix()
        yield ProtocolEntry(
            id=f"sdk:{rel}", source="sdk", title=facts.get("protocolName") or py.stem.replace("_", " "),
            path=str(py), folder=str(py.parent), convertible=True,
            description=_plain(facts.get("description", "") + " " + rel.split("/")[0] + " (SDK test/example)"),
            robot=_robot(facts.get("robotType", ""), rel), api_level=facts.get("apiLevel", ""),
        )


def _pylabrobot(root: Path) -> Iterable[ProtocolEntry]:
    for nb in sorted(root.rglob("*.ipynb")):
        try:
            cells = json.loads(_read(nb)).get("cells", [])
        except ValueError:
            continue
        markdown = "\n".join("".join(c.get("source", [])) for c in cells if c.get("cell_type") == "markdown")
        title_match = re.search(r"^#\s+(.+)$", markdown, re.M)
        rel = nb.relative_to(root).as_posix()
        yield ProtocolEntry(
            id=f"plr:{rel}", source="pylabrobot",
            title=title_match.group(1).strip() if title_match else nb.stem.replace("-", " "),
            path=str(nb), folder=str(nb.parent), convertible=False,
            description=_plain(markdown, 400), categories=rel.split("/")[:-1],
        )


def default_roots() -> list[Path]:
    raw = os.environ.get(ROOTS_ENV, "").strip()
    return [Path(r) for r in raw.split(";") if r.strip()] if raw else list(DEFAULT_ROOTS)


def build_index(roots: Optional[Iterable[Path]] = None) -> list[ProtocolEntry]:
    """Every protocol under ``roots``; each root may be a library folder, the
    Opentrons Protocols repo, the SDK, PyLabRobot, or a folder holding them."""
    entries: list[ProtocolEntry] = []
    for root in roots or default_roots():
        root = Path(root)
        if not root.exists():
            continue
        candidates = [root, *[p for p in root.iterdir() if p.is_dir()]]
        for folder in candidates:
            name = folder.name.lower()
            if (folder / "protocols").is_dir() and any((folder / "protocols").glob("*/README.md")):
                entries += _git(folder)
            elif any(folder.glob("*/metadata.json")):
                entries += _library(folder)
            elif name in ("opentrons_sdk", "opentrons") and (folder / "api").is_dir():
                entries += _sdk(folder)
            elif name == "pylabrobot" and (folder / "docs").is_dir():
                entries += _pylabrobot(folder / "docs")
    return entries


def load_index(cache: Optional[Path] = None, *, refresh: bool = False,
               roots: Optional[Iterable[Path]] = None) -> list[ProtocolEntry]:
    cache = Path(cache) if cache is not None else default_cache()
    if cache.exists() and not refresh:
        try:
            return [ProtocolEntry(**e) for e in json.loads(cache.read_text(encoding="utf-8"))]
        except (ValueError, TypeError):
            pass
    entries = build_index(roots)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([asdict(e) for e in entries], indent=1, ensure_ascii=False), encoding="utf-8")
    return entries


def search(entries: list[ProtocolEntry], query: str, *, source: str | None = None,
           robot: str | None = None, convertible_only: bool = False) -> list[ProtocolEntry]:
    """Entries containing every word of ``query``; title hits rank first."""
    words = [w for w in re.split(r"\s+", query.lower().strip()) if w]
    found = []
    for e in entries:
        if source and e.source != source:
            continue
        if robot and e.robot.lower() != robot.lower():
            continue
        if convertible_only and not e.convertible:
            continue
        text = e.text()
        if not all(w in text for w in words):
            continue
        title, cats = e.title.lower(), " ".join(e.categories).lower()
        score = sum(3 * (w in title) + 2 * (w in cats) + 1 for w in words)
        source_rank = {"library": 0, "git": 1, "sdk": 3, "pylabrobot": 4}.get(e.source, 5)
        found.append((-score, source_rank, e.title.lower(), e))
    return [item[-1] for item in sorted(found, key=lambda t: t[:3])]


def find(entries: list[ProtocolEntry], key: str) -> Optional[ProtocolEntry]:
    """By id (``0000025894``, ``git:00222e``, ``sdk:...``), folder or path."""
    key_norm = key.strip().strip('"').replace("\\", "/").rstrip("/").lower()
    for e in entries:
        if key_norm in (e.id.lower(), e.folder.replace("\\", "/").lower(), e.path.replace("\\", "/").lower()):
            return e
    for e in entries:  # a git id without its prefix
        if e.id.lower() == f"git:{key_norm}":
            return e
    return None
