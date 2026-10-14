"""Diagnostics attached to every result.

* Pre-trend: were AI and comparison teams moving together before rollout?
* Placebo dates: does a fake, earlier rollout date show an "effect"? It shouldn't.
* Placebo outcomes: does a metric the AI cannot touch move? It shouldn't.
* Leave-one-team-out: does one unusual team drive the whole result?
* Power: the smallest effect this data could detect.

The falsification tests (pre-trend, placebo dates, placebo outcomes) share one
5% family-wise error budget, split equally (Bonferroni). Failing any of them
moves a result to grade D, so testing each at 5% would wrongly downgrade about
one valid program in seven.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from probative.engine.imputation import Estimate, EstimationError, TeamBootstrap, estimate, fit
from probative.engine.panel import Panel

Status = Literal["pass", "fail", "not_run"]

# z(0.975) + z(0.80): an effect this many standard errors from zero is detected
# 80% of the time in a two-sided test at the 5% level.
MDE_MULTIPLIER = 2.8016


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    values: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail, "values": self.values}


def pretrend_check(est: Estimate, alpha: float = 0.05) -> Check:
    stat, df, p = est.pretrend_test()
    if np.isnan(p):
        return Check("Pre-trend test", "not_run", "Not enough pre-rollout weeks to test.")
    vals = {"wald": stat, "df": df, "p_value": p, "leads": est.point.pre}
    if p >= alpha:
        return Check(
            "Pre-trend test",
            "pass",
            f"AI and comparison teams moved together before rollout (joint p = {p:.2f} over {df} weeks).",
            vals,
        )
    return Check(
        "Pre-trend test",
        "fail",
        f"AI teams were already diverging before rollout (joint p = {p:.3f}). "
        "The comparison group may not show what would have happened anyway.",
        vals,
    )


def placebo_panel(panel: Panel, shift: int, min_pre: int = 4) -> Panel | None:
    """Keep only pre-rollout data and pretend each AI team started ``shift`` weeks early."""
    cohort = panel.cohort
    with np.errstate(invalid="ignore"):
        keep = np.isnan(cohort) | (panel.period < cohort)
    p = panel.subset(keep)
    fake = panel.cohort_by_team - shift
    first = np.full(panel.n_teams, np.inf)
    np.minimum.at(first, p.team, p.period.astype(float))
    ok_team = np.isnan(fake) | (fake - first >= min_pre)
    p = p.subset(ok_team[p.team])
    fake = np.where(ok_team, fake, np.nan)
    if np.all(np.isnan(fake)):
        return None
    return p.with_cohorts(fake)


def placebo_date_check(
    panel: Panel, boot: TeamBootstrap, shift: int, level: float, alpha: float = 0.05
) -> tuple[Check, Estimate | None]:
    pp = placebo_panel(panel, shift)
    if pp is None:
        return Check("Placebo dates", "not_run", f"Not enough history to fake a rollout {shift} weeks early."), None
    try:
        est = estimate(pp, boot, label="placebo date", level=level)
    except EstimationError as exc:
        return Check("Placebo dates", "not_run", f"Could not estimate placebo: {exc}"), None
    lo, hi = est.interval(1 - alpha)
    vals = {"shift_weeks": shift, "estimate": est.att, "ci_low": lo, "ci_high": hi, "level": 1 - alpha}
    if np.isnan(lo):
        return Check("Placebo dates", "not_run", "Placebo estimate too unstable to judge.", vals), est
    if lo <= 0 <= hi:
        return (
            Check(
                "Placebo dates",
                "pass",
                f"A fake rollout {shift} weeks early shows no effect ({est.att:+.3g}, range {lo:+.3g} to {hi:+.3g}).",
                vals,
            ),
            est,
        )
    return (
        Check(
            "Placebo dates",
            "fail",
            f"A fake rollout {shift} weeks early shows an 'effect' of {est.att:+.3g} "
            f"(range {lo:+.3g} to {hi:+.3g}); something other than the AI is moving the metric.",
            vals,
        ),
        est,
    )


def placebo_outcome_check(name: str, est: Estimate | None, alpha: float = 0.05) -> Check:
    title = f"Placebo outcome: {name}"
    if est is None:
        return Check(title, "not_run", "Metric not available in the data.")
    lo, hi = est.interval(1 - alpha)
    vals = {"estimate": est.att, "ci_low": lo, "ci_high": hi, "level": 1 - alpha}
    if np.isnan(lo):
        return Check(title, "not_run", "Estimate too unstable to judge.", vals)
    if lo <= 0 <= hi:
        return Check(title, "pass", f"A metric the AI cannot affect did not move ({est.att:+.3g}).", vals)
    return Check(
        title,
        "fail",
        f"A metric the AI cannot affect moved ({est.att:+.3g}, range {lo:+.3g} to {hi:+.3g}); "
        "AI teams changed in other ways at the same time.",
        vals,
    )


def leave_one_team_out(panel: Panel, main: Estimate, labels: list[str]) -> Check:
    treated_teams = np.unique(panel.team[panel.treated])
    results: dict[str, float] = {}
    for code in treated_teams:
        try:
            f = fit(panel.subset(panel.team != code))
        except EstimationError:
            continue
        results[labels[code]] = f.att
    if not results:
        return Check("Leave-one-team-out", "not_run", "Too few AI teams to test.")
    vals = np.array(list(results.values()))
    base = main.att
    worst_team = max(results, key=lambda k: abs(results[k] - base))
    lo, hi = float(vals.min()), float(vals.max())
    flips = main.excludes_zero and (np.sign(lo) != np.sign(base) or np.sign(hi) != np.sign(base))
    # One team may not move the estimate by more than half the effect or, for a null
    # effect, by more than the estimate's own uncertainty.
    ci_lo, ci_hi = main.ci
    half_width = (ci_hi - ci_lo) / 2 if not np.isnan(ci_lo) else 0.0
    big = max(abs(lo - base), abs(hi - base)) > max(0.5 * abs(base), half_width)
    info = {"min": lo, "max": hi, "most_influential_team": worst_team, "teams_tested": len(results)}
    if flips or big:
        return Check(
            "Leave-one-team-out",
            "fail",
            f"Dropping a single team moves the estimate from {base:+.3g} to as far as "
            f"{results[worst_team]:+.3g} (team {worst_team}); the result rests on one team.",
            info,
        )
    return Check(
        "Leave-one-team-out",
        "pass",
        f"Dropping any one AI team keeps the estimate between {lo:+.3g} and {hi:+.3g}.",
        info,
    )


def power_check(est: Estimate) -> Check:
    se = est.se
    if np.isnan(se):
        return Check("Power", "not_run", "Standard error unavailable.")
    mde = MDE_MULTIPLIER * se
    cf = est.point.counterfactual_mean
    rel = mde / abs(cf) if cf else float("nan")
    detail = (
        f"With this data the smallest reliably detectable effect is ±{mde:.3g} per unit of work (±{rel:.0%})."
        if not np.isnan(rel)
        else f"Smallest reliably detectable effect: ±{mde:.3g}."
    )
    if not est.excludes_zero:
        detail += " No effect was found, but effects smaller than this could not have been detected."
    return Check("Power", "pass", detail, {"mde": mde, "mde_relative": rel, "se": se})
