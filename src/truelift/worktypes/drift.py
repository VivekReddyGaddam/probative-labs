"""Category drift: taxonomy changes over time look like effects if nobody checks."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def js_distance(p: pd.Series, q: pd.Series) -> float:
    """Jensen-Shannon distance (base 2, between 0 and 1) between two share vectors."""
    idx = p.index.union(q.index)
    a = p.reindex(idx, fill_value=0).to_numpy(float)
    b = q.reindex(idx, fill_value=0).to_numpy(float)
    if a.sum() == 0 or b.sum() == 0:
        return float("nan")
    a, b = a / a.sum(), b / b.sum()
    m = 0.5 * (a + b)

    def kl(x: np.ndarray, y: np.ndarray) -> float:
        nz = x > 0
        return float((x[nz] * np.log2(x[nz] / y[nz])).sum())

    return float(np.sqrt(max(0.0, 0.5 * kl(a, m) + 0.5 * kl(b, m))))


@dataclass
class DriftReport:
    treated_shift: float
    control_shift: float
    new_categories: list[str] = field(default_factory=list)
    vanished_categories: list[str] = field(default_factory=list)
    status: str = "pass"
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "treated_shift": self.treated_shift,
            "control_shift": self.control_shift,
            "new_categories": self.new_categories,
            "vanished_categories": self.vanished_categories,
            "status": self.status,
            "message": self.message,
        }


def category_drift(
    labels: pd.Series,
    treated_team: pd.Series,
    post: pd.Series,
    *,
    min_share: float = 0.02,
    gap_threshold: float = 0.10,
) -> DriftReport:
    """Compare how much the category mix moved for AI teams versus comparison teams.

    ``treated_team`` marks rows from teams that ever got the program; ``post``
    marks rows after their team's rollout (for comparison teams, after the first
    rollout date). Some mix shift is expected when AI deflects easy work; a
    *taxonomy* change shows up as categories appearing or vanishing, and as a
    shift for AI teams far larger than for comparison teams.
    """

    def shares(mask: pd.Series) -> pd.Series:
        return labels[mask].value_counts(normalize=True)

    t_pre, t_post = shares(treated_team & ~post), shares(treated_team & post)
    c_pre, c_post = shares(~treated_team & ~post), shares(~treated_team & post)
    t_shift = js_distance(t_pre, t_post)
    c_shift = js_distance(c_pre, c_post) if len(c_pre) and len(c_post) else float("nan")

    new = sorted(str(k) for k, v in t_post.items() if v >= min_share and t_pre.get(k, 0) == 0)
    vanished = sorted(str(k) for k, v in t_pre.items() if v >= min_share and t_post.get(k, 0) == 0)

    rep = DriftReport(treated_shift=t_shift, control_shift=c_shift, new_categories=new, vanished_categories=vanished)
    gap = t_shift - (0 if np.isnan(c_shift) else c_shift)
    if new or vanished:
        rep.status = "fail"
        rep.message = (
            "Categories appeared or vanished for AI teams after rollout, which suggests the taxonomy "
            "or the categorizer changed. Type both periods the same way before trusting results."
        )
    elif gap > gap_threshold:
        rep.status = "warn"
        rep.message = (
            f"The category mix of AI teams moved much more than comparison teams' "
            f"(JS distance {t_shift:.2f} vs {c_shift:.2f}). Expected if AI deflects easy work; "
            "check that categorization itself did not change."
        )
    else:
        rep.message = "No sign that the category taxonomy changed with the rollout."
    return rep
