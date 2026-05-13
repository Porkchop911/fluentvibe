"""fluentdsl reference for the simple_transfer parity test.

Mirrors `examples/simple_transfer.py` exactly — same labware types, labels,
locations, positions, and pipetting volumes. Used by the parity test to
generate the byte-level reference `.xscr`.

Note: this version does not use `var("PlateType", ...)` because fluentvibe v1
does not model FC variables on the authoring side. Both pipelines must
produce identical Protocol IR for byte-equal parity.
"""

from fluentdsl import *

protocol("Simple transfer", "Move liquid from one plate to another")
group("Setup")
add("96 Well Flat", "SourcePlate", "Site", 1)
add("96 Well Flat", "DestPlate", "Site", 2)
add("MCA96, 100ul, Box", "Tips", "Site", 4)
group("Transfer")
mca96_adapter()
mca96_tips("Tips")
mca96_aspirate("SourcePlate", 20.0)
mca96_dispense("DestPlate", 20.0)
mca96_return_tips("Tips")
mca96_drop_adapter()
