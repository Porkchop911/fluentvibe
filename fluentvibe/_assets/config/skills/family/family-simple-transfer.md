---
name: family-simple-transfer
axis: family
description: Plain liquid transfer — move a volume from a source (plate or trough) to a destination plate, optionally column-wise. The default family when the request has no assay-specific shape (no beads, no magnet, no dilution series).
always_on: false
---
## Canonical workflow: simple transfer

The minimal plate-to-plate / trough-to-plate transfer. Keep this structure;
adapt the volume and labware to the request.

1. **Variables** (top-level, before any `wt.group`): `TARGET_VOLUME_UL` and
   one liquid-class variable, e.g. `LIQUID_CLASS_TRANSFER` (default and sim
   value `"Water Free Single"`).
2. **Labware Placement** group: source labware, destination `Plate96`, and a
   tip box for the head you use.
3. **Transfer** group:
   - **Column-wise (LiHa, from a trough or per-column source):** get tips,
     then a single aspirate/dispense wrapped in one native loop over the 12
     columns. Drop tips at the end.
     ```python
     head = wt.liha
     head.get_tips(tips)
     with wt.loop(times=12, name="Transfer columns", loop_variable="col"):
         head.aspirate(source, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER")
         head.dispense(dest, "TARGET_VOLUME_UL",
                       liquid_class="LIQUID_CLASS_TRANSFER", well_offset="(col-1)*8")
     head.drop_tips()
     ```
   - **Whole-plate (MCA96, plate-to-plate):** one aspirate + one dispense
     touches all 96 wells — no loop.
     ```python
     head = wt.mca96
     head.mount_adapter()
     head.pick_up(tips)
     head.aspirate(source, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER")
     head.dispense(dest, "TARGET_VOLUME_UL", liquid_class="LIQUID_CLASS_TRANSFER")
     head.return_tips(tips)
     head.drop_adapter()
     ```

Pick MCA96 only for true 96-channel plate-to-plate moves; use the LiHa for
trough-to-plate and per-column work. Pass volume and liquid_class BY NAME
(strings), not the Python values. Include only the groups the request needs.
