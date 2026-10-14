"""Pick the strongest comparison design the data allows, and grade the evidence.

Evidence grades (see docs/evidence-grades.md):

A  Randomized holdout or randomized wave order, pre-registered, diagnostics pass.
B  Staggered rollout or comparison group, diagnostics pass.
C  Valid design but weaker (few teams); counts, flagged as weaker.
   (Synthetic control and interrupted time series also land here; they ship in v2.)
D  Diagnostics failed: shown as indicative, never added to totals.
E  Vendor estimates, surveys, self-reported time saved: shown as claimed only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from probative.engine.diagnostics import Check

DesignName = Literal["staggered", "two_group", "none"]
Grade = Literal["A", "B", "C", "D", "E", "insufficient"]

COUNTED_GRADES = {"A", "B", "C"}
FEW_TEAMS = 12


@dataclass
class DesignChoice:
    design: DesignName
    randomized: bool
    reasons: list[str] = field(default_factory=list)
    stronger_unavailable: list[str] = field(default_factory=list)
    n_teams: int = 0
    n_treated_teams: int = 0
    n_never_treated: int = 0
    n_cohorts: int = 0

    @property
    def label(self) -> str:
        return {
            "staggered": "Staggered rollout (imputation difference-in-differences)",
            "two_group": "Two-group difference-in-differences",
            "none": "No valid comparison",
        }[self.design]

    def to_dict(self) -> dict:
        return {
            "design": self.design,
            "label": self.label,
            "randomized": self.randomized,
            "reasons": self.reasons,
            "stronger_unavailable": self.stronger_unavailable,
            "n_teams": self.n_teams,
            "n_treated_teams": self.n_treated_teams,
            "n_never_treated": self.n_never_treated,
            "n_cohorts": self.n_cohorts,
        }


def choose_design(
    cohort_by_team: np.ndarray,
    *,
    randomized: bool,
    requested: str = "auto",
    min_teams: int = 8,
    always_treated: int = 0,
) -> DesignChoice:
    treated = ~np.isnan(cohort_by_team)
    cohorts = np.unique(cohort_by_team[treated])
    choice = DesignChoice(
        design="none",
        randomized=randomized,
        n_teams=int(cohort_by_team.size),
        n_treated_teams=int(treated.sum()),
        n_never_treated=int((~treated).sum()),
        n_cohorts=int(cohorts.size),
    )
    if always_treated:
        choice.reasons.append(
            f"{always_treated} team(s) had the AI before the analysis window and were left out: "
            "there is no before period to compare."
        )
    if choice.n_treated_teams == 0:
        choice.reasons.append("No team received the program inside the analysis window.")
        return choice
    if choice.n_teams < min_teams:
        choice.reasons.append(
            f"Only {choice.n_teams} teams; at least {min_teams} are needed for team-level uncertainty."
        )
        return choice
    if cohorts.size == 1 and choice.n_never_treated == 0:
        choice.reasons.append(
            "Every team got the AI on the same date and no team was held back, so there is no "
            "comparison group. An interrupted time series (grade C) needs a year of stable history "
            "and ships in v2; until then Probative Labs reports no number."
        )
        return choice

    if requested == "two_group" and cohorts.size > 1:
        choice.reasons.append("Two-group design requested, but teams adopted on different dates; using staggered.")
    if cohorts.size == 1:
        choice.design = "two_group"
        choice.reasons.append(
            f"{choice.n_treated_teams} teams got the AI on one date and {choice.n_never_treated} "
            "comparable teams did not."
        )
    else:
        choice.design = "staggered"
        choice.reasons.append(
            f"{choice.n_treated_teams} teams adopted in {cohorts.size} waves"
            + (f", with {choice.n_never_treated} teams never exposed in the window" if choice.n_never_treated else "")
            + ". Each wave is compared with teams that did not have the AI yet."
        )
    if randomized:
        choice.reasons.append("Rollout order was assigned by lottery, so waves are comparable by design.")
    else:
        choice.stronger_unavailable.append(
            "Grade A needs a randomized holdout or randomized wave order; the rollout records "
            "do not show randomization."
        )
    return choice


@dataclass
class GradeResult:
    grade: Grade
    counted: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        return {"grade": self.grade, "counted": self.counted, "reasons": self.reasons}


def evidence_grade(
    choice: DesignChoice,
    checks: list[Check],
    *,
    preregistered_before_rollout: bool,
    registration_intact: bool,
) -> GradeResult:
    if choice.design == "none":
        return GradeResult("insufficient", False, ["No design reaches grade C, so no number is produced."])

    reasons: list[str] = []
    failed = [c for c in checks if c.status == "fail"]
    if failed:
        reasons.append("Failed diagnostics: " + "; ".join(c.name for c in failed) + ".")
        reasons.append("Shown as indicative only and never added to measured totals.")
        return GradeResult("D", False, reasons)

    if choice.randomized and preregistered_before_rollout and registration_intact:
        grade: Grade = "A"
        reasons.append("Randomized rollout, pre-registered before go-live, all diagnostics passed.")
    else:
        grade = "B"
        if choice.randomized and not preregistered_before_rollout:
            reasons.append("Randomized rollout, but the question was not registered before go-live, so capped at B.")
        elif choice.randomized and not registration_intact:
            reasons.append("Randomized rollout, but the analysis deviates from the registration, so capped at B.")
        else:
            reasons.append("Comparison-group design with pre-trend and placebo tests passed.")

    if choice.n_teams < FEW_TEAMS:
        grade = {"A": "B", "B": "C"}.get(grade, grade)  # type: ignore[assignment]
        reasons.append(f"Only {choice.n_teams} teams: confidence ranges are approximate, so downgraded one grade.")
    not_run = [c.name for c in checks if c.status == "not_run"]
    if not_run:
        reasons.append("Not run: " + ", ".join(not_run) + ".")
    return GradeResult(grade, grade in COUNTED_GRADES, reasons)
