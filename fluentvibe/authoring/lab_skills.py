"""Skill catalog + LM pre-pass selection for ``--lab-scope skills``.

Where ``enforce`` injects the whole ``lab_scope.md`` + ``lab_scope_reference.md``
monolith, ``skills`` mode decomposes that content into discrete frontmatter-
tagged skill files (``_assets/config/skills/*.md``) split along three axes —
``api``, ``deck``, ``family`` — and injects only the subset relevant to the
request, plus a small ``always_on`` core.

Selection is an LM pre-pass: one tool-free ``.invoke`` returns the skill names
to load. Parse/transport failures load all skills; explicit head requirements
and a missing family are also supplemented deterministically. Loading all
skills preserves capability coverage, but may add irrelevant context.

This module owns discovery, selection, and assembly. ``lab_scope.load_lab_scope``
calls :func:`discover_skills` to populate the catalog; ``service``/``session``
call :func:`build_initial_scope_message` once the prompt is known.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from .lab_scope import LabScope, context_header
from .workspace_modules import render_workspace_module_context

# api → deck → family. Stable ordering for the assembled context block so the
# injected message is deterministic regardless of selection order.
_AXIS_ORDER = {"api": 0, "deck": 1, "family": 2}
_VALID_AXES = frozenset(_AXIS_ORDER)
_BODY_SEP = "\n\n---\n\n"
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*?\]", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    """One curated instruction unit parsed from a ``skills/*.md`` file."""

    name: str
    axis: str
    description: str
    always_on: bool
    body: str
    path: Path
    # Deck skills only: the workspace this deck targets, so deck selection can
    # match it to the active workspace instead of relying on a hardwired
    # always-on default. Parsed from a ``workspace: {name:, guid:}`` frontmatter
    # mapping; ``None`` for non-deck skills (and decks that omit it).
    workspace_name: str | None = None
    workspace_guid: str | None = None
    # Optional deterministic selection triggers: prompt substrings (matched
    # case-insensitively, on word boundaries) that force this skill to be
    # injected regardless of the LM pre-pass. Parsed from a ``select_when:``
    # frontmatter list. Empty for skills that rely on LM selection alone.
    select_when: tuple[str, ...] = ()

    def matches_workspace(self, name: str | None, guid: str | None) -> bool:
        """True when this deck targets the given workspace (guid wins, else name)."""
        if self.workspace_guid and guid:
            return self.workspace_guid == guid
        if self.workspace_name and name:
            return self.workspace_name == name
        return False


def _parse_skill(path: Path) -> Skill | None:
    """Parse one skill file. Returns ``None`` (and is skipped) on any problem."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        return None
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    name = str(meta.get("name") or "").strip()
    axis = str(meta.get("axis") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name or axis not in _VALID_AXES or not description:
        return None
    # ``requires_env: NAME`` — the skill exists only while that variable is
    # set to a true value (experimental features, off by default).
    gate = str(meta.get("requires_env") or "").strip()
    if gate:
        import os

        if os.environ.get(gate, "").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
    ws = meta.get("workspace") if isinstance(meta.get("workspace"), dict) else {}
    ws_name = str(ws.get("name") or "").strip() or None
    ws_guid = str(ws.get("guid") or "").strip() or None
    raw_triggers = meta.get("select_when")
    select_when = (
        tuple(str(t).strip() for t in raw_triggers if str(t).strip())
        if isinstance(raw_triggers, list)
        else ()
    )
    return Skill(
        name=name,
        axis=axis,
        description=description,
        always_on=bool(meta.get("always_on", False)),
        body=match.group(2).strip(),
        path=path,
        workspace_name=ws_name,
        workspace_guid=ws_guid,
        select_when=select_when,
    )


def discover_skills(skills_dir: Path) -> tuple[Skill, ...]:
    """Load every well-formed ``*.md`` skill under ``skills_dir``.

    Recurses into subfolders (skills are organized into ``api/``, ``deck/``,
    ``family/`` category folders, but the authoritative axis is the frontmatter
    ``axis:`` field, not the folder). Malformed files are skipped, never fatal.
    Result is sorted by (axis rank, name) so the catalog order is deterministic.
    """
    if not skills_dir.is_dir():
        return ()
    skills = [
        s for s in (_parse_skill(p) for p in sorted(skills_dir.rglob("*.md")))
        if s is not None
    ]
    skills.sort(key=lambda s: (_AXIS_ORDER[s.axis], s.name))
    return tuple(skills)


def _order_names(names: set[str], catalog: tuple[Skill, ...]) -> list[str]:
    """Order a set of skill names by the catalog's (axis, name) order."""
    return [s.name for s in catalog if s.name in names]


def _without_decks(catalog: tuple[Skill, ...]) -> tuple[Skill, ...]:
    """``catalog`` with every ``axis == "deck"`` skill removed."""
    return tuple(s for s in catalog if s.axis != "deck")


def _with_active_deck(
    catalog: tuple[Skill, ...], deck: Skill
) -> tuple[Skill, ...]:
    """Drop shipped decks and splice ``deck`` in, forced ``always_on``.

    The active deck is selected from the workspace, so it must always be
    injected (never left to the optional LM pre-pass) — mark it ``always_on``.
    """
    rebuilt = _without_decks(catalog) + (replace(deck, always_on=True),)
    return tuple(sorted(rebuilt, key=lambda s: (_AXIS_ORDER[s.axis], s.name)))


def apply_profile_deck(
    catalog: tuple[Skill, ...], deck_skill_path: Path
) -> tuple[Skill, ...]:
    """Return ``catalog`` with its deck skill(s) replaced by a profile's deck.

    Drops every shipped ``axis == "deck"`` skill and splices in the profile's
    deck skill (parsed from ``deck_skill_path``), so a saved workspace profile
    becomes the authoritative deck for ``--lab-scope skills`` authoring. The
    profile deck carries the ``from_workspace`` binding for its own workspace;
    the always-on api skills are deck-agnostic, so no other rewriting is needed.

    Returns ``catalog`` unchanged if the deck skill is missing or malformed
    (degrade rather than drop context).
    """
    deck = _parse_skill(deck_skill_path)
    if deck is None:
        return catalog
    return _with_active_deck(catalog, deck)


def select_deck_for_workspace(
    catalog: tuple[Skill, ...],
    workspace_name: str | None,
    workspace_guid: str | None,
) -> tuple[Skill, ...] | None:
    """Keep only the shipped deck matching the active workspace, forced on.

    Returns the rebuilt catalog (non-deck skills + the matched deck) when a
    shipped ``axis == "deck"`` skill targets ``workspace_name``/``guid``, or
    ``None`` when no deck matches — the caller treats that as "no deck for this
    workspace", a fail-loud signal rather than a silent default.
    """
    for skill in catalog:
        if skill.axis == "deck" and skill.matches_workspace(workspace_name, workspace_guid):
            return _with_active_deck(catalog, skill)
    return None


_SELECTION_SYSTEM = (
    "You select which curated lab skills are relevant to a protocol request. "
    "You are given a list of optional skills (name, axis, description) and the "
    "user's request. Return ONLY a JSON array of the skill names that are "
    "relevant — no prose, no markdown fence. Pick every head/API skill the "
    "protocol will actually use, and every protocol-family skill it needs — a "
    "multi-stage protocol often needs several (e.g. a sequencing library prep "
    "that includes a magnetic-bead cleanup needs BOTH the library-prep family "
    "and the bead-cleanup family; a prep that pools samples also needs the "
    "pooling family). If unsure, include the skill. Example: "
    "[\"head-liha\", \"family-ngs-library-prep\", \"family-bead-cleanup-spri\"]."
)


def _select_when_hits(optional: list[Skill], prompt: str) -> set[str]:
    """Optional skill names whose ``select_when`` triggers occur in ``prompt``.

    Deterministic, LM-independent: a configured domain trigger (e.g. the
    bead-cleanup family's "ampure"/"spri") force-includes its skill so a
    high-signal request never depends on the LM pre-pass picking it.
    """
    low = (prompt or "").lower()
    hits: set[str] = set()
    for skill in optional:
        for term in skill.select_when:
            if re.search(rf"\b{re.escape(term.lower())}\b", low):
                hits.add(skill.name)
                break
    return hits


def _expand_cross_references(
    names: set[str], catalog: tuple[Skill, ...], *, max_hops: int = 2
) -> set[str]:
    """Pull in skills that selected skills reference by name in their bodies.

    A family skill that says "reuses ``family-bead-cleanup-spri``" must
    actually deliver that skill's body, not just a dangling pointer. Bounded
    transitive closure over verbatim skill-name mentions; never expands into
    ``deck`` skills (those are workspace-matched, not content-referenced).
    """
    bodies = {s.name: s.body for s in catalog}
    expandable = [s.name for s in catalog if s.axis != "deck"]
    result = set(names)
    frontier = set(names)
    for _ in range(max_hops):
        added: set[str] = set()
        for current in frontier:
            body = bodies.get(current, "")
            for other in expandable:
                if other in result or other == current:
                    continue
                if other in body:
                    added.add(other)
        if not added:
            break
        result |= added
        frontier = added
    return result


def select_skills(prompt: str, catalog: tuple[Skill, ...], client) -> list[str]:
    """Return the ordered skill names to inject for ``prompt``.

    ``always_on`` skills are always included. The remaining (optional) skills
    are chosen by an LM pre-pass over ``client.invoke``, then augmented
    deterministically: ``select_when`` domain triggers force-include
    high-signal families, and cross-reference expansion pulls in skills that
    selected skills name in their bodies. On any LM failure (transport error,
    unparseable reply, no valid names) the optional set falls back to ALL
    optional skills, so ``skills`` mode degrades to the ``enforce`` monolith
    rather than dropping context.
    """
    always = {s.name for s in catalog if s.always_on}
    optional = [s for s in catalog if not s.always_on]
    if not optional:
        return _order_names(always, catalog)

    optional_names = {s.name for s in optional}
    chosen: set[str] | None = None

    # An approved Bench Spec says exactly which stages the protocol has, so the
    # skills follow from it deterministically — no model pre-pass.
    from_spec = _skills_from_spec(prompt, optional_names)
    if from_spec is not None:
        selected = _expand_cross_references(
            always | from_spec | _select_when_hits(optional, prompt), catalog
        )
        return _order_names(selected, catalog)
    # Selection wants a plain JSON answer. With the authoring tools attached,
    # models answered with a tool call instead and selection fell back to
    # loading every skill.
    without_tools = getattr(client, "without_tools", None)
    if callable(without_tools):
        client = without_tools()
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        listing = "\n".join(
            f"- {s.name} [{s.axis}]: {s.description}" for s in optional
        )
        invoke = getattr(client, "invoke_without_tools", client.invoke)
        response = invoke(
            [
                SystemMessage(content=_SELECTION_SYSTEM),
                HumanMessage(
                    content=f"OPTIONAL SKILLS:\n{listing}\n\nREQUEST:\n{prompt}"
                ),
            ]
        )
        content = getattr(response, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        array_match = _JSON_ARRAY_RE.search(content)
        if array_match is not None:
            parsed = json.loads(array_match.group(0))
            picked = {
                str(x).strip() for x in parsed if str(x).strip() in optional_names
            }
            if picked:
                chosen = picked
    except Exception:
        chosen = None

    if chosen is None:
        # Safe fallback: load everything. Triggers/cross-refs are redundant.
        return _order_names(always | optional_names, catalog)

    # Deterministic augmentation on top of the LM's picks: configured triggers,
    # a head or worklist the request names, and a family if none was picked.
    chosen |= _select_when_hits(optional, prompt) | (_named_capabilities(prompt) & optional_names)
    if not any(s.axis == "family" and s.name in chosen for s in optional):
        chosen |= {"family-simple-transfer"} & optional_names
    selected = _expand_cross_references(always | chosen, catalog)
    return _order_names(selected, catalog)



def _named_capabilities(prompt: str) -> set[str]:
    """Skills for a head or worklist the request names explicitly."""
    low = (prompt or "").lower()
    names = set()
    if re.search(r"mca(?:96|384)?", low):
        names.add("head-mca96")
    if re.search(r"(?:liha|fca)", low):
        names.add("head-liha")
    if "worklist" in low:
        names.add("api-worklists")
    return names


_OP_SKILLS: dict[str, tuple[str, ...]] = {
    "bead_cleanup": ("family-bead-cleanup-spri", "api-magnetization-model", "head-mca96", "head-gripper"),
    "pool": ("family-pooling", "head-liha"),
    "add": ("head-mca96", "head-liha"),
    "transfer": ("head-mca96", "head-liha"),
    "mix": ("head-mca96", "head-liha"),
}
_FAMILY_WORDS: tuple[tuple[str, str], ...] = (
    (r"librar|barcod|sequenc|tagment|adapter|ngs", "family-ngs-library-prep"),
    (r"\bpcr\b|master ?mix|qpcr|amplif", "family-pcr-setup"),
    (r"elisa|immunoassay", "family-elisa"),
    (r"normali[sz]", "family-normalize-to-target"),
    (r"serial dilution|dilution series", "family-serial-dilution"),
    (r"cherry|pick ?list", "family-cherrypicking"),
    (r"bca|bradford|protein assay", "family-protein-assay"),
    (r"reformat|consolidat|384", "family-plate-reformatting"),
    (r"thermal|thermocycl|odtc|°c|\bc for\b", "device-odtc"),
)


def _skills_from_spec(prompt: str, optional_names: set[str]) -> set[str] | None:
    """Optional skills implied by an approved Bench Spec in ``prompt``, or None."""
    from .reagent_budget import spec_from_prompt

    spec = spec_from_prompt(prompt)
    if spec is None:
        return None
    names: set[str] = set()
    for step in spec.steps:
        if step.location == "deck":
            names.update(_OP_SKILLS.get(step.op, ()))
    text = " ".join([spec.title, *(step.text for step in spec.steps)]).lower()
    for pattern, skill in _FAMILY_WORDS:
        if re.search(pattern, text):
            names.add(skill)
    if any(len(step.temp_c) for step in spec.steps):
        names.add("device-odtc")
    return names & optional_names


def assemble_context(scope: LabScope, names: list[str]) -> str | None:
    """Concatenate the selected skills' bodies under the scope header."""
    by_name = {s.name: s for s in scope.skill_catalog}
    bodies = [by_name[n].body for n in names if n in by_name]
    if not bodies:
        return None
    profile_table = _profile_labware_class_table(scope)
    module_context = render_workspace_module_context(tuple(scope.workspace_modules))
    pieces = (
        ([profile_table] if profile_table else [])
        + ([module_context] if module_context else [])
        + bodies
    )
    # assemble_context only runs for ``skills`` mode (see build_initial_scope_message),
    # which declares the workflow once and then drafts the whole protocol in one
    # pass (per-group staging is shelved — see graph._should_stage) — use the
    # skills header.
    header = context_header(scope.enforces, skills=True)
    if "task-opentrons-conversion" in names:
        header += _OPENTRONS_HEADER
    return header + _BODY_SEP.join(pieces)


# Added to the skills header when an Opentrons protocol is converted (graph.py
# binds these tools only for such requests).
_OPENTRONS_HEADER = (
    "For this Opentrons protocol you also have `opentrons_search`, `opentrons_convert`, `read_draft` and "
    "`opentrons_check_fidelity`. Call `opentrons_convert` first, before `declare_protocol_workflow`: the "
    "Opentrons protocol already defines its volumes, layout and steps - read them from the conversion, never "
    "ask the user for them." + "\n" * 2
)


def _profile_labware_class_table(scope: LabScope) -> str | None:
    if not scope.labware_classes:
        return None
    rows = "\n".join(
        f"| `{catalog}` | `{python_class}` |"
        for catalog, python_class in sorted(scope.labware_classes.items())
    )
    return (
        "## Profile labware class contract\n\n"
        "Use the exact `python_class` shown for each profile catalog. A protocol "
        "that uses a listed catalog with any other class is invalid.\n\n"
        "| Catalog | Required python_class |\n|---|---|\n"
        f"{rows}"
    )


def additional_skills_message(scope: LabScope, names: list[str]) -> str | None:
    """Bodies of skills added after the first turn (a correction needs a skill
    the first selection left out). Sent with the user's message, so the
    context already sent stays unchanged."""
    by_name = {s.name: s for s in scope.skill_catalog}
    bodies = [by_name[n].body for n in names if n in by_name]
    if not bodies:
        return None
    return "Additional lab skills for this request:\n\n" + _BODY_SEP.join(bodies)


def build_initial_scope_message(scope: LabScope, prompt: str, client) -> str | None:
    """The context system-message text for this run.

    For off/cheatsheet/enforce this is the static cheatsheet. For ``skills`` it
    is the LM-selected subset assembled at runtime (the prompt and client are
    only available here, not at config load).
    """
    if scope.mode != "skills":
        return scope.as_context_message()
    if not scope.skill_catalog:
        return None
    names = select_skills(prompt, scope.skill_catalog, client)
    raw_client = getattr(client, "_legacy", client)
    trace = getattr(raw_client, "trace_recorder", None)
    if trace is not None:
        trace.record("skill_selection", scope=scope.mode, selected_skills=names)
    return assemble_context(scope, names)
