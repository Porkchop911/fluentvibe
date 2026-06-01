---
name: api-magnetization-model
axis: api
description: How magnetic-bead state is modelled — magnetization is implied by stacking a plate onto a MagnetRack (never an explicit step), beads are a solid-phase well attribute driven by reagent roles, and mixing on/off the magnet binds or releases analyte. Select for any protocol using magnetic beads, SPRI, or a magnet rack.
always_on: false
---
## Magnetization is geometry, not a command

`Labware.is_magnetized` is a derived `@property` — true exactly when a
`MagnetRack` is in the plate's `stack_below`. The ONLY thing that changes it is
a gripper transfer:

```python
wt.gripper.move(plate, onto=magnet_rack)        # is_magnetized → True
wt.gripper.move(plate, to=("Nest61mm_Pos", 1))  # off the magnet → False
```

There is no `engage_magnet()`, no `MagnetizeStep`, and the simulator never sets
the flag directly. This matches FluentControl: putting a plate on a magnet site
is a Transfer Labware step, with no separate engage command.

## A magnet withholds beads, never liquid

Do **not** use a "pinned liquid layer" model. Beads are a *solid-phase
attribute* of a well (`Well.bead_phase`), not a liquid layer. Reagent **role**
drives everything:

- `bead_carrier` — the bead suspension. Its µL is normal, fully aspirable
  liquid; *dispensing* it establishes the well's bead phase.
- `analyte` — the captured species (e.g. DNA), a small marker layer distinct
  from the bulk buffer (a `plain` reagent).
- `eluent` — the release buffer.

(The legacy `Reagent.pinned_when_magnetized` flag is a read-only alias for
`role == "bead_carrier"`; there is no pinned-layer skipping in the simulator.)

## Binding, release, and aspiration

- A **mix off the magnet** binds free analyte to the suspended beads.
- A **mix off the magnet with an eluent present** releases bound analyte back
  into the liquid. → Always mix after adding beads, and after adding elution
  buffer.
- **On the magnet**, an aspirate draws *all* free liquid; the beads + bound
  analyte stay in the well. So a supernatant/eluate aspirate gets the whole
  free volume minus the small retain volume — the magnet never reduces it.
- **Off the magnet**, aspirating a bead well entrains the beads + bound analyte
  into the tip (modelled silently in state, not an error).

```python
beads = Reagent("AMPure beads", role="bead_carrier")
dna   = Reagent("Sample DNA", role="analyte")
eb    = Reagent("Elution buffer", role="eluent")
# dispense beads → bead phase established; mix off-magnet → DNA binds beads
# move onto magnet → aspirate supernatant (all free liquid, beads stay)
# move off magnet → add eb, mix → DNA released; move on → transfer eluate
```

For the full bind→wash→elute workflow and the derived supernatant/eluate
volumes, see the bead-cleanup family skill.
