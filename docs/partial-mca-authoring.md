# Partial MCA stamp authoring

On two 96-well plates, a left-half-to-center stamp transfers columns 1–6
of the source to columns 4–9 of the destination, preserving rows A–H and
sample order. At 50 µL per well this transfers 48 samples and 2,400 µL total.
Destination columns 7–12 are the right half, not the center.

Use `columns=` on MCA pickup, aspirate, dispense, and return. Columns are
1-based, ascending, unique integers from 1 through 12. Partial pickup must
use an exposed edge of the tip box or the complete remaining set of tips.
When returning a partial set, supply the original box columns explicitly.
The simulator rejects addressing more wells than mounted tips.

For a shifted transfer with partially mounted MCA tips, the renderer keeps
the mounted tip selection fixed and encodes the destination shift in the
placement column. Thus tips 1–6 dispensing to plate columns 4–9 retain
`FirstTipXPosition=1` and `LastTipXPosition=6`, with `Column=3`.

Skills/enforce mode now uses its own complete-protocol system prompt and
the profile's liquid-class default. It exposes only `simulate_python_draft`
and `compile_and_simulate`, without approval or grounding instructions.
The selector supplements an omitted explicitly requested head and a missing
protocol family. Its legacy client runs selection without authoring tools.
Corrections refresh the skill context; trace files record selected skills.

The authoring validator recognizes “stamp” as liquid handling. For simple
left-half-to-center MCA stamps between 96-well plates it checks source and
destination column selections, final requested volume, and unfilled wells
outside the destination region, even without a `declare_intent` tool.
Repeated transfer pairs can split the volume. This geometric inference is
limited to that shape; it is not a general natural-language mapping verifier.

`examples/partial_mca_stamp.py` demonstrates the 1080_DEV_TABLE profile with
one identifiable sample per source well. Compilation and strict simulation
are software checks; FluentControl acceptance and physical execution of the
shifted transfer have not been tested here.

Restart the workspace app and create a fresh authoring session to load the
updated code and skill context.

Default workspace outputs now use a dated, unique subfolder under
`build/workbench/authored` (and the equivalent folder for other jobs).
An explicitly chosen output directory is still honored.

Workspace FluentControl validation backs up the shell and leaves the
inspected script open in it (2026-10-06: no restore; a human inspection
ends with FluentControl in front, an agent inspection gives the focus back). It closes the shell editor before patching, preserves the shell
identity, verifies the vendor checksum on a staged file, and replaces the
file atomically. Validation requires the expected script tab and readable
InfoPad; load failures are displayed as failures in the app. UI automation
is restricted to the FluentControl process, and pending save prompts stop
the operation so unsaved edits are not discarded.
