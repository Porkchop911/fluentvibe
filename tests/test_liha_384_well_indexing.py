"""LiHa well indexes must use the row count of the addressed plate.

FluentControl serializes a plate column-major with ``rows`` per column: 8 for a
96 plate, 16 for a 384 plate. The renderer took that row count from
``labware_reference``, which used to load only the ``labware`` and ``other``
sections of ``_assets/reference/labware.yaml``. Plate entries live under
``plates:``, so a 384 plate resolved to ``None`` and fell back to the 96-well
stride: a dispense meant for C2/E2 was serialized as 10;12, which FluentControl
shows as K1/M1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fluentvibe import FCA200Box, Plate384, Reagent, Trough25mL, Worktable  # noqa: E402
from fluentvibe.compiler.renderer import Renderer  # noqa: E402

INK = Reagent("Ink")


def _render(wt: Worktable) -> str:
    protocol = wt.to_protocol()
    protocol.worktable_guid = protocol.worktable_guid or "00000000-0000-0000-0000-000000000000"
    protocol.worktable_name = protocol.worktable_name or "TestWorkspace"
    return Renderer().render(protocol)


def _print_plate_worktable() -> tuple[Worktable, object, object, object]:
    wt = Worktable(name="liha 384 indexing")
    wt.group("Setup")
    plate = wt.place(Plate384("PrintPlate", catalog="384 Well LowVol LoBase"), "Nest", 1)
    tips = wt.place(FCA200Box("Tips", catalog="FCA, 200ul SBS"), "Nest", 2)
    trough = wt.place(Trough25mL("Ink", catalog="25ml_short"), "WS_100ml_1", 1)
    trough.fill_all(INK, 1000.0)
    return wt, plate, tips, trough


# ── Labware reference ───────────────────────────────────────────────

@pytest.mark.parametrize(
    "catalog,wells",
    [
        ("384 Well LowVol LoBase", 384),
        ("384 Well", 384),
        ("96_ABgene_SuperPlate_Thermo_AB2800", 96),
    ],
)
def test_labware_reference_covers_plate_sections(catalog: str, wells: int) -> None:
    meta = Renderer().labware_reference.get(catalog)
    assert meta is not None, f"{catalog} is missing from the labware reference"
    assert meta["wells"] == wells


def test_deck_sections_do_not_invent_categories() -> None:
    """A plate must not be tagged 'reservoir': that forces single-well indexing."""
    meta = Renderer().labware_reference["384 Well LowVol LoBase"]
    assert meta["category"] != "reservoir"


# ── Index encoding ──────────────────────────────────────────────────

def test_explicit_wells_use_the_plate_row_count() -> None:
    assert Renderer._liha_explicit_wells("C2;E2", 384, False)[1] == "18;20;"
    assert Renderer._liha_explicit_wells("I18;K18;M18", 384, False)[1] == "280;282;284;"
    # 96 plates keep the 8-per-column stride.
    assert Renderer._liha_explicit_wells("C2;E2", 96, False)[1] == "10;12;"


def test_dispense_indexes_match_the_selected_wells() -> None:
    """SerializedWellIndexes and SelectedWellsString must describe the same wells."""
    wt, plate, tips, trough = _print_plate_worktable()
    wt.group("Print")
    head = wt.liha
    head.get_tips(tips)
    wells = ["I18", "K18", "M18"]
    head.aspirate(
        trough,
        25.0,
        liquid_class="Water Free Single",
        wells=["A1"] * len(wells),
        volumes=[25.0] * len(wells),
        channels=list(range(len(wells))),
    )
    head.dispense(
        plate,
        25.0,
        liquid_class="Water Free Single",
        wells=wells,
        volumes=[25.0] * len(wells),
        channels=list(range(len(wells))),
    )
    head.drop_tips()

    xml = _render(wt)
    blocks = [
        tuple(int(number) for number in block.split(";") if number.isdigit())
        for block in re.findall(r"<SerializedWellIndexes>([^<]*)</SerializedWellIndexes>", xml)
    ]

    def encode(rows: int) -> tuple[int, ...]:
        return tuple((int(addr[1:]) - 1) * rows + (ord(addr[0]) - ord("A")) for addr in wells)

    assert encode(16) in blocks, "384 plate must be serialized 16 wells per column"
    assert encode(8) not in blocks, "the 96-well stride put the ink in the wrong wells"
