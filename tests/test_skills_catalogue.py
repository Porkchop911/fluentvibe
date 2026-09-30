"""Consistency of the skills catalogue with the code it teaches.

Skills are prompt text, so nothing else checks them: a renamed parameter, an
invented role or a tip box the FCA rejects only shows up as a failed authoring
run. These tests read every skill and hold its examples to the real API.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import fluentvibe
import fluentvibe.blocks as blocks
from fluentvibe.authoring.lab_scope import REQUIRED_LIQUID_CLASSES
from fluentvibe.authoring.lab_skills import _expand_cross_references, discover_skills
from fluentvibe.gripper import Gripper
from fluentvibe.heads.liha import LiHa
from fluentvibe.heads.mca96 import MCA96Head
from fluentvibe.reagent import ROLES, SPEC_ROLE_ALIASES
from fluentvibe.worktable import Worktable

SKILLS = Path(fluentvibe.__file__).parent / "_assets" / "config" / "skills"
FILES = sorted(SKILLS.rglob("*.md"))
CATALOG = discover_skills(SKILLS)
CODE = re.compile(r"```(?:python|py)?\n(.*?)```", re.S)
HEADS = {"liha": LiHa, "fca": LiHa, "mca96": MCA96Head, "mca": MCA96Head, "gripper": Gripper, "rga": Gripper}
ALLOWED_LIQUID_CLASSES = {"Water Free Single"} | set(REQUIRED_LIQUID_CLASSES)
# A body that names another skill pulls it into the prompt (_expand_cross_references),
# so a family names another family only where both are always wanted together.
FAMILY_LINKS = {("family-ngs-library-prep", "family-bead-cleanup-spri")}
SAT_780 = ('"SAT_Fluent_780_Rev3"', '"291ba293-6361-4f8f-aa8d-7c2643d3f096"')


def _ids(files):
    return [f.relative_to(SKILLS).as_posix() for f in files]


def _code_blocks(path: Path) -> list[ast.Module]:
    trees = []
    for code in CODE.findall(path.read_text(encoding="utf-8")):
        try:
            trees.append(ast.parse(code))
        except SyntaxError:
            trees.append(ast.parse("def _f():\n" + "\n".join("    " + line for line in code.splitlines())))
    return trees


def _bad_keywords(fn, call: ast.Call) -> list[str]:
    sig = inspect.signature(fn)
    if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
        return []
    return [kw.arg for kw in call.keywords if kw.arg and kw.arg not in sig.parameters]


def test_every_skill_file_loads():
    loaded = {s.name for s in CATALOG}
    gated = {"api-add-resolver"}  # requires_env: FLUENTVIBE_RESOLVER
    for path in FILES:
        name = re.search(r"^name:\s*(\S+)", path.read_text(encoding="utf-8"), re.M).group(1)
        assert name in loaded or name in gated, f"{path.name} does not load"


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_examples_call_the_real_api(path):
    problems = []
    for tree in _code_blocks(path):
        heads = dict(HEADS)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) \
                    and isinstance(node.value.value, ast.Name) and node.value.value.id == "wt" \
                    and node.value.attr in HEADS:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        heads[target.id] = HEADS[node.value.attr]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn, owner = node.func, None
            if isinstance(fn, ast.Attribute):
                base = fn.value
                if isinstance(base, ast.Name) and base.id == "wt":
                    owner = Worktable
                elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) \
                        and base.value.id == "wt" and base.attr in HEADS:
                    owner = HEADS[base.attr]
                elif isinstance(base, ast.Name) and base.id in heads:
                    owner = heads[base.id]
                if owner is None:
                    continue
                target = getattr(owner, fn.attr, None)
                label = f"{owner.__name__}.{fn.attr}"
            elif isinstance(fn, ast.Name) and fn.id in blocks.__all__:
                target, label = getattr(blocks, fn.id), fn.id
            elif isinstance(fn, ast.Name) and inspect.isclass(getattr(fluentvibe, fn.id, None)):
                target, label = getattr(fluentvibe, fn.id), fn.id
            else:
                continue
            if target is None:
                problems.append(f"`{label}` does not exist")
                continue
            if callable(target):
                problems += [f"`{label}` has no parameter `{kw}`" for kw in _bad_keywords(target, node)]
    assert not problems, problems


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_prose_names_real_head_methods(path):
    text = path.read_text(encoding="utf-8")
    missing = [f"wt.{head}.{meth}" for head, meth in set(re.findall(r"`wt\.(liha|mca96|gripper)\.([a-z_]+)", text))
               if not hasattr(HEADS[head], meth)]
    assert not missing, missing


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_roles_are_real(path):
    text = path.read_text(encoding="utf-8")
    roles = set(re.findall(r"role=[\"'](\w+)[\"']", text)) | set(re.findall(r"role \(`(\w+)`\)", text))
    bad = roles - set(ROLES) - set(SPEC_ROLE_ALIASES)
    assert not bad, f"roles not in {ROLES}: {sorted(bad)}"


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_liquid_classes_are_allowed(path):
    text = path.read_text(encoding="utf-8")
    used = set(re.findall(r"liquid_class=[\"']([^\"']+)[\"']", text))
    bad = {lc for lc in used if lc not in ALLOWED_LIQUID_CLASSES and not re.fullmatch(r"[A-Z][A-Z0-9_]*", lc)}
    assert not bad, f"liquid classes outside the allow-list: {sorted(bad)}"


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_tip_boxes_match_the_head(path):
    text = path.read_text(encoding="utf-8")
    assert "TipBox(" not in text, "use FCA200Box / FCA1000Box / MCA*Box, not TipBox"
    assert not re.search(r"MCA\d+Box\([^)]*catalog=\"FCA", text), "an MCA box class with an FCA catalog"
    assert not re.search(r"FCA\d+Box\([^)]*catalog=\"MCA", text), "an FCA box class with an MCA catalog"


@pytest.mark.parametrize("path", FILES, ids=_ids(FILES))
def test_no_stale_worklist_claims(path):
    text = path.read_text(encoding="utf-8")
    assert "VALIDATION_ONLY" not in text
    assert "will not reflect" not in text


def test_families_do_not_pull_in_unrelated_families():
    families = [s for s in CATALOG if s.axis == "family"]
    for skill in [s for s in CATALOG if s.axis != "deck"]:
        for other in families:
            if other.name != skill.name and other.name in skill.body:
                assert (skill.name, other.name) in FAMILY_LINKS, \
                    f"{skill.name} names {other.name}, which loads it into every prompt that loads {skill.name}"


def test_immobilization_does_not_bring_spri():
    chosen = _expand_cross_references({"family-bead-immobilization", "api-magnetization-model"}, CATALOG)
    assert "family-bead-cleanup-spri" not in chosen


def test_complete_example_simulates_strict():
    text = (SKILLS / "api" / "core-worktable-api.md").read_text(encoding="utf-8")
    code = next(c for c in CODE.findall(text) if "def build_worktable" in c)
    code = code.replace("WORKSPACE_NAME", SAT_780[0]).replace("WORKSPACE_GUID", SAT_780[1])
    namespace: dict = {}
    exec(compile(code, "core-worktable-api.md", "exec"), namespace)
    wt = namespace["build_worktable"]()
    wt.simulate(strict=True)
