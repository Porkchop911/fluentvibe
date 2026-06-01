---
name: deck-sat-780
axis: deck
description: The SAT_Fluent_780_Rev3 deck profile — workspace GUID, valid deck positions, the default role-to-slot layout, and reachability constraints. Always loaded (this lab runs one deck); swap this file to target an alternate deck.
always_on: true
---
## Deck / workspace

- Workspace: `SAT_Fluent_780_Rev3`
- Workspace GUID: `291ba293-6361-4f8f-aa8d-7c2643d3f096`
- Always: `Worktable.from_workspace("SAT_Fluent_780_Rev3", workspace_guid="291ba293-6361-4f8f-aa8d-7c2643d3f096", auto_place=False, ...)`

## Valid deck positions

| Location | Valid positions |
|---|---|
| `InfiniteM200_Pos` | 1 |
| `MCA384_Diti_ActiveNest` | 1, 2 |
| `Nest61mm_Pos` | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 |
| `Nest7mm_Pos` | 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16 |
| `Regrip_Pos` | 1 |
| `ThruDeckWaste_Pos` | 1 |
| `WS_100ml_1` | 1, 2, 3, 4, 5, 6, 7, 8 |

## Default layout (role → slot)

Place on `Nest61mm_Pos`: sample plate → site 1, final/elution plate → site 2,
magnet rack → site 3, MCA tips → site 4, waste → site 5, FCA tips → site 6.
Reservoirs go on the `WS_100ml_1` location.

| Role | Location | Site |
|---|---|---|
| sample_plate | `Nest61mm_Pos` | 1 |
| final_plate | `Nest61mm_Pos` | 2 |
| magnet_plate | `Nest61mm_Pos` | 3 |
| mca_tips | `Nest61mm_Pos` | 4 |
| waste | `Nest61mm_Pos` | 5 |
| fca_tips | `Nest61mm_Pos` | 6 |
| reservoir_start | `WS_100ml_1` | 1 |

## Reachability rules

- Use ONLY the exact location keys above; do not invent names like
  `WS_100ml_2/3/4`.
- For a `100ml Trough Double` at `WS_100ml_1`, avoid sites 1 and 5 (LiHa
  reachability limits); use 2/3/4/6/7/8, or use `100ml Trough 156mm`.
- FCA SBS tip boxes on `Nest61mm_Pos` are reachable only at sites 1–6; avoid
  7–10 and avoid `Nest7mm_Pos` placements that can be out of range.
- Alpaqua magnet plates (`LV_Alpaqua_A000350`, `LV_Alpaqua_384`) are SBS
  format and go on normal `Nest61mm_Pos` positions — there is NO special
  "magnet station" location on the Fluent worktable.
- Do not aspirate/dispense/mix directly on a magnet carrier label. Pipette on
  the process plate label; the magnet carrier only supplies the field.
