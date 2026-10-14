"""Work typing and mix adjustment.

AI changes *which* work humans do, not just how fast. Every unit of work is typed
so that results can be compared like with like and reweighted to a fixed mix.
"""

from probative.worktypes.drift import DriftReport, category_drift
from probative.worktypes.mix import (
    baseline_type_means,
    baseline_weights,
    before_after_table,
    mix_adjusted_mean,
    residualize,
)
from probative.worktypes.rules import assign_work_types

__all__ = [
    "DriftReport",
    "assign_work_types",
    "baseline_type_means",
    "baseline_weights",
    "before_after_table",
    "category_drift",
    "mix_adjusted_mean",
    "residualize",
]
