"""Narrowed-scope experiment: a curated lab subset injected into model context.

Forum advice (luis/Stefan): the FluentControl install is general-purpose, the
lab is not. Enumerate the finite subset actually used and give the model a
tutorial-style cheatsheet instead of relying on open-ended catalog search.

This module is the loader/gate. ``skills`` is the **default generation mode**;
set ``--lab-scope off`` / ``FLUENTVIBE_LAB_SCOPE=off`` to reproduce the exact
pre-experiment (7fa6e88) baseline.

Modes:
- ``off``        — nothing loaded, baseline behavior (opt-in via off)
- ``cheatsheet`` — inject ``lab_scope.md`` into model context (Lever A)
- ``enforce``    — cheatsheet + restrict labware/liquid-class tool results to
                    the curated whitelist (Lever A + B)
- ``skills``     — like ``enforce`` (same tool restriction + whitelist), but
                    the context is assembled from a relevant subset of granular
                    skill files chosen by an LM pre-pass (see ``lab_skills``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .grounding import load_generation_config

if TYPE_CHECKING:
    from .profile import ResolvedProfile

_VALID_MODES = ("off", "cheatsheet", "enforce", "skills")
_ENV_VAR = "FLUENTVIBE_LAB_SCOPE"
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "_assets" / "config"


class LabScopeSetupError(RuntimeError):
    """Raised when ``skills`` mode is active but no deck matches the workspace.

    The deck is workspace-specific and must come from a set-up workspace — a
    ``--profile`` / ``FLUENTVIBE_PROFILE_DIR`` profile, or a shipped deck skill
    whose ``workspace:`` frontmatter matches the configured ``worktable`` in
    ``generation.yaml``. There is intentionally no hardwired default deck, so a
    misconfigured run fails loud with actionable guidance instead of silently
    authoring against the wrong deck.
    """


def _configured_worktable() -> tuple[str | None, str | None]:
    """The workspace (name, guid) bound in ``generation.yaml`` ``worktable``."""
    wt = (load_generation_config() or {}).get("worktable") or {}
    name = str(wt.get("name") or "").strip() or None
    guid = str(wt.get("guid") or "").strip() or None
    return name, guid

# Sentinel: distinguishes "caller passed profile=None (opt out of env default)"
# from "caller didn't specify, resolve from FLUENTVIBE_PROFILE_DIR".
_UNSET: "ResolvedProfile | None" = object()  # type: ignore[assignment]


_DEFAULT_MODE = "skills"


def resolve_lab_scope_mode(cli_value: str | None = None) -> str:
    """Resolve the active mode. Precedence: explicit CLI arg > env > ``skills``.

    ``skills`` is the default generation mode. Any unrecognized value
    normalizes to the default (rather than silently disabling the scope);
    pass an explicit ``off`` to reproduce the pre-experiment baseline.
    """
    raw = cli_value if cli_value is not None else os.environ.get(_ENV_VAR)
    if raw is None:
        return _DEFAULT_MODE
    value = str(raw).strip().lower()
    return value if value in _VALID_MODES else _DEFAULT_MODE


@dataclass(frozen=True)
class LabScope:
    """Resolved curated scope. ``mode == "off"`` ⇒ everything below is inert."""

    mode: str = "off"
    cheatsheet_text: str | None = None
    labware: frozenset[str] = field(default_factory=frozenset)
    labware_classes: dict[str, str] = field(default_factory=dict)
    liquid_classes: frozenset[str] = field(default_factory=frozenset)
    # Populated only in ``skills`` mode (loaded from ``_assets/config/skills``).
    skill_catalog: tuple = ()

    @property
    def is_active(self) -> bool:
        """True when curated context should be injected."""
        return self.mode in ("cheatsheet", "enforce", "skills")

    @property
    def enforces(self) -> bool:
        """True when tool surface + labware/liquid-class results are restricted.

        ``skills`` shares enforce's entire runtime posture (autogrounding, the
        labware/liquid whitelist, the two-tool surface); it differs only in how
        the context message is built (a selected subset vs the whole monolith).
        """
        return self.mode in ("enforce", "skills")

    def allows_labware(self, name: str | None) -> bool:
        """Whitelist check (no-op pass-through unless ``enforces``)."""
        if not self.enforces:
            return True
        return name in self.labware if name is not None else False

    def allows_liquid_class(self, name: str | None) -> bool:
        if not self.enforces:
            return True
        return name in self.liquid_classes if name is not None else False

    # Catalog *discovery* / fan-out tools. In ``enforce`` the curated
    # whitelist IS the catalog, so these are removed from the model's tool
    # surface entirely (the whitelist is treated as already-grounded — see
    # AuthoringToolRegistry._confirmed_catalog_names). ``ground_in_parallel``
    # is included so the orchestrator cannot spawn catalog-search subagents.
    _SEARCH_TOOLS = frozenset(
        {"search_labware", "get_labware", "ground_in_parallel"}
    )

    # In ``enforce`` the cheatsheet + reference doc carry every fact the
    # grounding/planning tools used to fetch at runtime, so the model needs
    # no tool but the two that judge the draft: simulate the Python and
    # compile it to .xscr. Everything else is withheld (see
    # ``allowed_tools``) and all pre-simulation gates are disabled.
    _ENFORCE_ALLOWED_TOOLS = frozenset({"simulate_python_draft", "compile_and_simulate"})
    # ``skills`` shares enforce's two-tool judging surface but additionally
    # exposes ``declare_protocol_workflow`` so it can stage a multi-stage
    # protocol group-by-group (enforce stays strictly one-pass).
    _SKILLS_ALLOWED_TOOLS = _ENFORCE_ALLOWED_TOOLS | {"declare_protocol_workflow"}

    def allowed_tools(self) -> frozenset[str] | None:
        """Tool names the LLM may call. ``None`` ⇒ no allow-list (all permitted).

        ``enforce`` restricts to the two judging tools; ``skills`` adds
        ``declare_protocol_workflow`` for staged drafting; off/cheatsheet return
        ``None`` so their tool exposure is byte-identical to baseline.
        """
        if self.mode == "skills":
            return self._SKILLS_ALLOWED_TOOLS
        return self._ENFORCE_ALLOWED_TOOLS if self.enforces else None

    def denied_tools(self) -> frozenset[str]:
        """Tool names to withhold from the LLM for this mode."""
        return self._SEARCH_TOOLS if self.enforces else frozenset()

    def as_context_message(self) -> str | None:
        """The cheatsheet wrapped as a standalone system-context block.

        Injected as a SEPARATE message after SYSTEM_PROMPT — never woven into
        it (SYSTEM_PROMPT is guarded against domain vocabulary at
        ``service.assert_no_domain_vocabulary_in_prompt``; this block is the
        sanctioned place for lab/assay-specific terms).
        """
        if not self.is_active or not self.cheatsheet_text:
            return None
        return context_header(self.enforces) + self.cheatsheet_text


# Header text prepended to the injected context block. Factored out of
# ``as_context_message`` so ``lab_skills.assemble_context`` can reuse the
# enforce header verbatim for ``skills`` mode (which shares enforce's posture).
_ENFORCE_HEADER = (
    "LAB SCOPE (authoritative — this IS the catalog for this run). "
    "Grounding, planning, and approval tools are intentionally "
    "unavailable: the ONLY tools you can call are "
    "`simulate_python_draft` and `compile_and_simulate`. Everything "
    "the removed tools used to look up (the head/object API, valid "
    "deck positions, the workspace GUID, liquid classes, and the "
    "authoring rules) is provided below and in the reference section "
    "— do not ask for it and do not attempt catalog search.\n\n"
    "WORKFLOW: write the COMPLETE protocol as a single "
    "`build_worktable()` in one pass — all variables, labware, and "
    "every functional group — then call `simulate_python_draft` with "
    "the full source. Do NOT stage group-by-group and do NOT wait for "
    "any approval; there are no object-draft or functional-group "
    "gates. Fix any simulator error and re-call `simulate_python_draft`; "
    "once it passes, call `compile_and_simulate` on the same source. "
    "Use the exact `catalog=` names and `python_class` values listed "
    "below directly. If the request needs labware not in this list, "
    "say so explicitly and stop rather than substituting.\n\n"
)

_CHEATSHEET_HEADER = (
    "LAB SCOPE (authoritative for this lab). Prefer the curated "
    "deck, labware, liquid classes, and workflow shapes below over "
    "open-ended catalog search. Still ground exact names through "
    "tools, but pick from this set unless the request genuinely "
    "needs something else.\n\n"
)


_SKILLS_HEADER = (
    "LAB SCOPE (authoritative — this IS the catalog for this run). "
    "Grounding and approval tools are intentionally unavailable: the only "
    "tools you can call are `declare_protocol_workflow`, "
    "`simulate_python_draft`, and `compile_and_simulate`. Everything the "
    "removed tools used to look up (the head/object API, valid deck "
    "positions, the workspace GUID, liquid classes, and the authoring "
    "rules) is provided below and in the selected skills — do not ask for "
    "it and do not attempt catalog search.\n\n"
    "WORKFLOW: FIRST call `declare_protocol_workflow` with the protocol "
    "name, a one-line summary, the planned variables and labware, and the "
    "ORDERED functional groups. The first two groups must be exactly "
    "`Variables` then `Labware Placement`; after them, name EVERY stage the "
    "request describes as its own group (e.g. a bead/SPRI cleanup, ethanol "
    "washes, elution, barcoding) — do not collapse or omit a stage. For a "
    "multi-stage protocol you will then be guided to draft and "
    "`simulate_python_draft` ONE group at a time, keeping prior accepted "
    "code and extending it, until every group passes; only then call "
    "`compile_and_simulate` on the full source. Fix any simulator error and "
    "re-call `simulate_python_draft`. Use the exact `catalog=` names and "
    "`python_class` values listed below. If the request needs labware not "
    "in this list, say so explicitly and stop rather than substituting.\n\n"
)


def context_header(enforces: bool, *, staged: bool = False) -> str:
    """The header block for an injected lab-scope context message.

    ``staged`` selects the skills group-by-group workflow header (only skills
    mode passes it); otherwise enforce's one-pass header or the cheatsheet
    header is used.
    """
    if staged:
        return _SKILLS_HEADER
    return _ENFORCE_HEADER if enforces else _CHEATSHEET_HEADER


_OFF = LabScope()


def load_lab_scope(
    cli_value: str | None = None,
    *,
    profile: "ResolvedProfile | None" = _UNSET,
) -> LabScope:
    """Build the active :class:`LabScope` for this run.

    Returns the inert singleton when mode is ``off`` or the ``lab_scope``
    config block is absent/disabled, so callers can unconditionally consult
    the result without branching on configuration.

    When a workspace-app ``profile`` is active (passed explicitly, or resolved
    from ``FLUENTVIBE_PROFILE_DIR`` by default), ``skills`` mode swaps the
    profile's deck skill in for the shipped deck and sources the labware/liquid
    whitelist from the profile — so a saved profile fully drives authoring.
    Pass ``profile=None`` to opt out of the env default.
    """
    mode = resolve_lab_scope_mode(cli_value)
    if mode == "off":
        return _OFF

    if profile is _UNSET:
        from .profile import profile_from_env  # lazy: avoids import cycle

        profile = profile_from_env()

    block = (load_generation_config() or {}).get("lab_scope") or {}
    if not block.get("enabled", False):
        return _OFF

    labware = frozenset(
        str(x).strip() for x in (block.get("labware") or []) if str(x).strip()
    )
    liquid_classes = frozenset(
        str(x).strip() for x in (block.get("liquid_classes") or []) if str(x).strip()
    )
    if profile is not None and profile.labware:
        labware = profile.labware
    labware_classes = dict(profile.labware_classes) if profile is not None else {}
    if profile is not None and profile.liquid_classes:
        liquid_classes = profile.liquid_classes

    # ``skills`` mode assembles context at runtime from a selected subset of
    # granular skill files instead of injecting the monolith. It reuses the
    # same whitelist (shared with enforce). Inert if the skills block is absent
    # or disabled, so a misconfigured run can never silently change behavior.
    if mode == "skills":
        skills_block = block.get("skills") or {}
        if not skills_block.get("enabled", False):
            return _OFF
        from .lab_skills import (  # lazy: avoids import cycle
            apply_profile_deck,
            discover_skills,
            select_deck_for_workspace,
        )

        skills_dir = _CONFIG_DIR / (skills_block.get("dir") or "skills")
        catalog = discover_skills(skills_dir)
        if not catalog:
            return _OFF

        # The deck must come from a set-up workspace, never a hardwired default.
        # A profile (explicit/env) supplies its own deck directly; otherwise the
        # configured worktable selects the shipped deck whose workspace matches.
        if profile is not None and profile.deck_skill is not None:
            catalog = apply_profile_deck(catalog, profile.deck_skill)
        else:
            ws_name, ws_guid = (
                (profile.workspace_name, profile.workspace_guid)
                if profile is not None
                else _configured_worktable()
            )
            matched = select_deck_for_workspace(catalog, ws_name, ws_guid)
            if matched is None:
                raise LabScopeSetupError(
                    f"No deck skill matches the configured workspace "
                    f"{ws_name or '(unset)'!r}. Set up a workspace with "
                    f"`fluentvibe workspace-app` and pass it via `--profile "
                    f"build/workspaces/<name>` (or FLUENTVIBE_PROFILE_DIR), or "
                    f"add a deck skill whose `workspace:` frontmatter matches."
                )
            catalog = matched
        return LabScope(
            mode=mode,
            cheatsheet_text=None,
            labware=labware,
            labware_classes=labware_classes,
            liquid_classes=liquid_classes,
            skill_catalog=catalog,
        )

    cheatsheet_name = block.get("cheatsheet") or "lab_scope.md"
    cheatsheet_path = _CONFIG_DIR / cheatsheet_name
    try:
        text: str | None = cheatsheet_path.read_text(encoding="utf-8")
    except OSError:
        text = None

    # In enforce mode the grounding/planning tools are removed, so the facts
    # they used to fetch live in a reference doc that is appended to the
    # cheatsheet. Only appended for enforce so cheatsheet mode stays
    # byte-identical to baseline.
    if mode == "enforce" and text is not None:
        reference_name = block.get("reference") or "lab_scope_reference.md"
        try:
            reference_text = (_CONFIG_DIR / reference_name).read_text(encoding="utf-8")
        except OSError:
            reference_text = None
        if reference_text:
            text = f"{text}\n\n---\n\n{reference_text}"

    return LabScope(
        mode=mode,
        cheatsheet_text=text,
        labware=labware,
        labware_classes=labware_classes,
        liquid_classes=liquid_classes,
    )
