---
name: family-protein-assay
axis: family
description: Colorimetric protein quantitation (Pierce BCA, Bradford) — dispense working reagent into the plate, add samples (optionally in replicate), mix, incubate at 37C, then read off-deck. Select for BCA, Bradford, protein concentration, or total-protein assay requests.
always_on: false
---
## Canonical workflow: BCA / Bradford protein assay

The Tecan does the liquid handling and the incubation wait; the colorimetric
read happens off-deck on a plate reader. Adapt volumes/replicates to the request.

1. **Variables** (top-level, before any `wt.group`):
   `WORKING_REAGENT_VOL_UL` (BCA default 200, Bradford ~250),
   `SAMPLE_VOL_UL` (25 or 10), `MIX_VOLUME_UL`, `INCUBATION_SECONDS`
   (BCA 37C ≈ 1800), and a liquid-class variable per role
   (`LIQUID_CLASS_REAGENT`, `LIQUID_CLASS_SAMPLE`), default + sim value
   `"Water Free Single"`. Compute the reagent trough fill
   (`96 × WORKING_REAGENT_VOL_UL × 1.1`).
2. **Labware Placement** group: working `Plate96` (the reaction plate), sample
   source `Plate96`, a working-reagent reservoir `Trough` (`100ml` — 96×200 µL
   ≈ 19 mL needs the high-capacity trough), MCA tip box, FCA tip box. **FCA
   tip box must be FCA-class (not MCA)** for the LiHa:
   ```python
   from fluentvibe import TipBox, MCA100Box
   fca_tips = wt.place(TipBox("FCA_Tips", catalog="FCA, 1000ul SBS"), "Nest61mm_Pos", 6)
   mca_tips = wt.place(MCA100Box("MCA_Tips", catalog="MCA96, 100ul, Box"), "Nest61mm_Pos", 4)
   ```
3. **Add working reagent** (the bulk reagent goes to every well first):
   ```python
   head = wt.liha
   head.get_tips(fca_tips)
   with wt.loop(times=12, name="Add working reagent", loop_variable="col"):
       head.aspirate(reagent_trough, "WORKING_REAGENT_VOL_UL", liquid_class="LIQUID_CLASS_REAGENT")
       head.dispense(plate, "WORKING_REAGENT_VOL_UL",
                     liquid_class="LIQUID_CLASS_REAGENT", well_offset="(col-1)*8")
   head.drop_tips()
   ```
4. **Add samples**: transfer `SAMPLE_VOL_UL` from the sample plate into the
   reaction plate. Whole-plate 1:1 with the MCA96:
   ```python
   head = wt.mca96
   head.mount_adapter(); head.pick_up(mca_tips)
   head.aspirate(sample_plate, "SAMPLE_VOL_UL", liquid_class="LIQUID_CLASS_SAMPLE")
   head.dispense(plate, "SAMPLE_VOL_UL", liquid_class="LIQUID_CLASS_SAMPLE")
   head.mix(plate, "MIX_VOLUME_UL", cycles=3, liquid_class="LIQUID_CLASS_SAMPLE")
   head.return_tips(mca_tips); head.drop_adapter()
   ```
   For **duplicate/replicate** wells, lay the reaction plate out so replicates
   are separate columns and dispense the sample into each replicate column with
   a LiHa column loop instead.
5. **Incubate**: `wt.wait(duration_seconds="INCUBATION_SECONDS")` — this is where
   the Opentrons heater-shaker/temperature-module 37 °C step maps; note the
   intended temperature in a `#` comment (fluentvibe has no module abstraction).
6. **Read**: `wt.add_comment("Measure absorbance at 562 nm (BCA) / 595 nm (Bradford) on a plate reader")`
   — the read itself is off-deck.

## Standard curve

If the request includes a standard curve, prepare the 2-fold dilution series
with the serial-dilution pattern (see the **family-serial-dilution** skill) into
the first columns of the sample plate, then proceed from step 3. Do not
re-derive the dilution logic here.

## Notes / limits

- The working-reagent prep (mixing Reagent A:B 50:1) is assumed done off-deck or
  pre-filled in the reservoir; if on-deck prep is requested, add a group that
  aspirates A and B from troughs into the reservoir and mixes.
- 37 °C incubation and the absorbance read are not Tecan operations here — they
  are a timed `wt.wait` and an off-deck `add_comment`.
