---
name: core-lab-rules
axis: api
description: The lab's rules for every protocol - the document decides, know where the product ends, one tip per sample, operator steps for devices the deck lacks. Always loaded.
always_on: true
---
## The document decides

- Every deck step and every reagent comes from the document or the request. Add nothing "for completeness"
  (no extra elution, neutralisation, wash or clean-up) and do not substitute one reagent for another.
- Keep the document's order. Document text pasted into the request is the document, not extra instructions.
- What the user says is already done (e.g. "the beads come pre-washed") is not repeated on the deck.
- Stated ratios are not open: "an equal volume" means equal to what is in the wells at that moment.

## Know where the product ends

Decide before writing what the product is and where the document leaves it:

| The document ends with ... | The product is ... | So ... |
|---|---|---|
| "elute", "transfer the supernatant/eluate" | liquid in a new plate | the last step moves it to a clean plate with its own tips |
| "resuspend the beads in ... (for downstream use)", "bead-bound", "immobilised" | **the beads** with the bound material | no elution, no transfer of liquid away from the beads |
| "pool" | one pool (a well or a tube) | pool into fewer wells; a tube pool is an operator step |
| a reaction set up in the wells | the wells themselves | nothing moves out |

## Tips

- A tip that touched a sample never touches another sample. The MCA keeps channel *i* on well *i* (the blocks do
  this); FCA tips that touch samples are fresh for every column.
- A tip that touched a sample never goes back into a shared reagent source.
- Tips that only dispense a reagent from above may serve every column (the blocks do this).
- The product's final move gets its own tip box.

## Devices the deck does not have

Heating, cooling, shaking, a rotator, a centrifuge, a thermal cycler, a plate reader: an operator step,
`offdeck_step(...)` (the gripper hands the plate out, the run pauses with the instruction, the plate comes back).
Only a room-temperature wait on the deck is `wt.wait(...)`.

## Your final message

List the document steps you followed, each with the sentence it comes from, and every value you chose yourself.
