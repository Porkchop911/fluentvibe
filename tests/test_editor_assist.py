"""Editor assistance: protocol-aware completion values, Ctrl+I imports/preview, explain grounding."""
from __future__ import annotations

from fluentvibe.copilot.complete import complete_at
from fluentvibe.copilot.edit import _import_insert_line, _missing_block_imports, edit_region
from fluentvibe.copilot.explain import explain_region
from fluentvibe.copilot.values import head_aliases, open_call, placed_labware

PROTOCOL = '''from fluentvibe import Worktable, Plate96, Trough25mL, Trough100mL, MCA200Box, FCA200Box, Reagent
from fluentvibe.blocks import (
    distribute_reagent,
    mix_wells,
)


def build_worktable() -> Worktable:
    wt = Worktable.from_workspace("SAT_Fluent_780_Rev3", workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096",
                                  auto_place=False, protocol_name="Assist")
    work = wt.place(Plate96("Work", catalog="96_ABgene_SuperPlate_Thermo_AB2800"), "Nest61mm_Pos", 1)
    work_tips = wt.place(MCA200Box("WorkTips", catalog="MCA96, 200ul, Box"), "Nest61mm_Pos", 2)
    fca_tips = wt.place(FCA200Box("FcaTips", catalog="FCA, 200ul SBS"), "Nest61mm_Pos", 3)
    wash = wt.place(Trough100mL("Wash", catalog="60ml SBS MCA96"), "Nest61mm_Pos", 4)
    buffer = wt.place(Trough25mL("Buffer", catalog="25ml_short"), "WS_100ml_1", 2)
    waste = wt.place(Trough25mL("Waste", catalog="300ml SBS"), "Nest7mm_Pos", 4)
    fca = wt.liha
    head = wt.liha
    buffer.fill_all(Reagent("Buffer"), 20000)
    distribute_reagent(wt, source=buffer, plate=work, volume_ul=20, tips=fca_tips, liquid_class="Water Free Single")
    mix_wells(wt, plate=work, tips=work_tips, volume_ul=15)
    return wt
'''


def _complete(typed: str):
    lines = PROTOCOL.splitlines()
    at = next(i for i, l in enumerate(lines) if l.strip() == "return wt")
    lines.insert(at, typed)
    return [c.label for c in complete_at("\n".join(lines), at, len(typed))]


def test_the_protocol_is_read_without_running_it():
    placed = {p.var: p for p in placed_labware(PROTOCOL)}
    assert placed["buffer"].kind == "trough" and placed["buffer"].slim and not placed["wash"].slim
    assert placed["fca_tips"].kind == "fca_tips" and placed["work_tips"].kind == "mca_tips"
    assert (placed["waste"].site, placed["waste"].index) == ("Nest7mm_Pos", 4)
    assert head_aliases(PROTOCOL) == {"fca": "liha", "head": "liha"}
    call = open_call("distribute_reagent(wt,\n    source=buffer, tips=Fc", 1, 26)
    assert (call.callee, call.keyword, call.partial) == ("distribute_reagent", "tips", "Fc")


def test_tips_offers_the_box_for_the_blocks_head():
    assert _complete("    distribute_reagent(wt, source=buffer, plate=work, volume_ul=5, tips=") == ["fca_tips"]
    assert _complete("    mix_wells(wt, plate=work, tips=") == ["work_tips"]
    assert _complete("    fca.get_tips(") == ["fca_tips"]


def test_sources_follow_the_head():
    # The MCA cannot pipette in a slim trough; an FCA block prefers one; waste comes last as a source.
    assert "buffer" not in _complete("    add_reagent(wt, reagent_source=")
    offered = _complete("    distribute_reagent(wt, source=")
    assert offered[0] == "buffer" and offered[-1] == "waste"
    assert _complete("    remove_liquid(wt, plate=work, waste=")[0] == "waste"


def test_strings_liquid_classes_roles_wells():
    assert {"Water Free Single", "Water Mix", "Empty Tip"} <= set(_complete('    mix_wells(wt, plate=work, tips=work_tips, volume_ul=5, liquid_class="'))
    assert _complete('    r = Reagent("B", role="bead') == ["bead_carrier"]
    assert _complete('    pool_wells(wt, source=work, dest=work, source_wells=["A1", "H1') == ["H1", "H10", "H11", "H12"]


def test_our_method_completion_follows_the_assignment():
    methods = _complete("    head.")
    assert "get_tips" in methods and "mount_adapter" not in methods   # head = wt.liha is the FCA


def test_edit_adds_the_block_import_and_proposes_the_whole_file():
    class Client:
        def complete(self, *, messages, tools):
            assert "wt.add(" not in messages[0]["content"]
            return {"content": "    remove_liquid(wt, plate=work, waste=waste, volume_ul=30, tips=work_tips,\n"
                               "                  liquid_class=\"Water Free Single\")"}

    lines = PROTOCOL.splitlines()
    target = next(i for i, l in enumerate(lines, 1) if l.strip().startswith("mix_wells("))
    result = edit_region(PROTOCOL, target, target, "remove the liquid instead", client=Client(), revalidate=False)
    assert result.imports == ["remove_liquid"]
    assert result.to_dict()["import_text"] == "from fluentvibe.blocks import remove_liquid"
    proposed = result.proposed_source.splitlines()
    assert proposed[result.import_line - 1] == "from fluentvibe.blocks import remove_liquid"
    assert "mix_wells(wt, plate=work, tips=work_tips, volume_ul=15)" not in result.proposed_source
    assert _import_insert_line(PROTOCOL.splitlines()) == 6
    assert _missing_block_imports(PROTOCOL, "    mix_wells(wt)") == []


def test_explain_selection_gets_the_simulated_state():
    seen = {}

    class Client:
        def complete(self, *, messages, tools):
            seen["user"] = messages[1]["content"]
            return {"content": "It adds buffer."}

    lines = PROTOCOL.splitlines()
    line = next(i for i, l in enumerate(lines, 1) if "distribute_reagent(wt, source" in l)
    assert explain_region(PROTOCOL, line, line, path="assist.py", client=Client()) == "It adds buffer."
    state = seen["user"].split("Simulated deck state after these lines:")[1]
    assert "Work at Nest61mm_Pos 1: 96 well(s) with liquid, 20.0 µl each" in state


def test_chat_context_is_grounded_and_short():
    from fluentvibe.copilot.chat import chat_context

    lines = PROTOCOL.splitlines()
    line = next(i for i, l in enumerate(lines, 1) if "distribute_reagent(wt, source" in l)
    ctx = chat_context(PROTOCOL, "assist.py", start_line=line, end_line=line, has_selection=True)
    assert "wt.add(" in ctx["system"] and "Never use wt.add" in ctx["system"]
    assert "- distribute_reagent(wt, source" in ctx["system"]          # used: full signature
    assert "Also available:" in ctx["system"] and "spri_cleanup" in ctx["system"]   # unused: name only
    assert "never goes back into a shared" in ctx["system"]
    assert f"Selected lines {line}-{line}" in ctx["context"]
    assert "Work at Nest61mm_Pos 1: 96 well(s) with liquid, 20.0 µl each" in ctx["context"]
    assert len(ctx["system"]) < 4500


def test_chat_proposal_is_checked_like_ctrl_i():
    from fluentvibe.copilot.edit import propose_region

    lines = PROTOCOL.splitlines()
    target = next(i for i, l in enumerate(lines, 1) if l.strip().startswith("mix_wells("))
    result = propose_region(PROTOCOL, target, target, "    mix_wells(wt, plate=work, tips=work_tips, volume_ul=15, cycles=3)",
                            path="assist.py")
    assert not result.introduces_errors and "cycles=3" in result.proposed_source
