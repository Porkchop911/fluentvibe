---
name: head-gripper
axis: api
description: The wt.gripper (RGA) for moving plates between deck positions and onto/off a magnet rack. Select for any protocol that relocates labware — especially magnetic bead separation where moving a plate onto the magnet IS the magnetization.
always_on: false
---
## `wt.gripper`

```python
wt.gripper.move(plate, to=('Nest61mm_Pos', 2))
wt.gripper.move(plate, onto=magnet)
```
**Never call:** `pick_up`, `drop`, `place`, `aspirate`, `dispense`

## Rules

- **Magnetization is implied by geometry, never an explicit step.**
  `wt.gripper.move(plate, onto=magnet_rack)` *is* the magnetization; there is
  no engage/disengage command. Moving the plate back off the magnet
  (`to=(...)`) releases it.
- **Plate movements between locations MUST use the gripper**
  (`rga_transfer_labware` with `cga_get_fingers`/`cga_drop_fingers`). NEVER
  emit a `user_prompt` asking the user to move plates by hand.
