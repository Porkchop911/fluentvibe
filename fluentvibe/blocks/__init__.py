"""Verified building blocks for common protocol stages.

A block is a plain function that emits a whole, known-good stage onto a
:class:`~fluentvibe.Worktable`. Most are physical primitives with no chemistry
(add: ``distribute_reagent`` / ``add_reagent``; transfer: ``stamp`` /
``pool_columns``; ``remove_liquid``; ``mix_wells``; magnet ``separate`` /
``release``; operator: ``offdeck_step``); any plate protocol is a sequence of
them. ``spri_cleanup`` is a macro: one fixed order of those primitives. Blocks take **roles and scientific parameters**
(which plate holds the samples, how much beads to add), derive the dependent
volumes themselves, and follow fixed tip rules so the stage cannot
cross-contaminate samples. They lower to ordinary head / gripper calls, so the
simulator, renderer and every check treat them like hand-written code.

Authoring models should call a block instead of writing the stage step by step:

    from fluentvibe.blocks import spri_cleanup
    spri_cleanup(wt, sample_plate=samples, magnet=magnet, ...)

Tip rules used throughout:

* **reagent tips** only aspirate from a reagent source and dispense into
  samples from above, so they stay sample-free and may be reused;
* **sample tips** touch sample liquid (mix, supernatant removal); on the MCA96
  each channel always meets the same well, so reuse across stages of one
  sample plate is safe;
* **eluate tips** are used once, to move the clean product.

Blocks raise :class:`BlockError` with a fix-oriented message when their
preconditions do not hold, instead of emitting a protocol that would be wrong
on the bench.
"""

from __future__ import annotations

from .bead_cleanup import CleanupVolumes, spri_cleanup
from .common import BlockError
from .operator import offdeck_step, thermal_step
from .primitives import mix_wells, release, remove_liquid, separate
from .transfers import (
    add_reagent,
    distribute_reagent,
    distribute_volumes,
    pool_columns,
    pool_wells,
    stamp,
    transfer_volumes,
)

__all__ = [
    "BlockError",
    "CleanupVolumes",
    "add_reagent",
    "distribute_reagent",
    "distribute_volumes",
    "mix_wells",
    "offdeck_step",
    "pool_columns",
    "pool_wells",
    "release",
    "remove_liquid",
    "separate",
    "spri_cleanup",
    "stamp",
    "thermal_step",
    "transfer_volumes",
]
