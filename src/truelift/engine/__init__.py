"""Causal engine: comparison designs, estimates with team-level ranges, diagnostics, grades."""

from truelift.engine.design import DesignChoice, GradeResult, choose_design, evidence_grade
from truelift.engine.diagnostics import Check
from truelift.engine.imputation import Estimate, EstimationError, Fit, TeamBootstrap, estimate, fit
from truelift.engine.panel import Panel, count_panel, mean_panel

__all__ = [
    "Check",
    "DesignChoice",
    "Estimate",
    "EstimationError",
    "Fit",
    "GradeResult",
    "Panel",
    "TeamBootstrap",
    "choose_design",
    "count_panel",
    "estimate",
    "evidence_grade",
    "fit",
    "mean_panel",
]
