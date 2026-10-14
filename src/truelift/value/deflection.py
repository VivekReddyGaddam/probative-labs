"""Verified deflection.

A self-service resolution counts only if the same user did not open a ticket
on the same type of issue within 7 days. Sessions that come back are *false*
deflections: the work was done twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from truelift import schema as S


@dataclass
class DeflectionSummary:
    sessions: int
    resolved: int
    verified: int
    false: int
    hours_avoided: float
    by_type: list[dict] = field(default_factory=list)
    counted: bool = False
    reason: str = ""

    @property
    def false_rate(self) -> float:
        return self.false / self.resolved if self.resolved else float("nan")

    def to_dict(self) -> dict:
        return {
            "sessions": self.sessions,
            "resolved": self.resolved,
            "verified": self.verified,
            "false": self.false,
            "false_rate": self.false_rate,
            "hours_avoided": self.hours_avoided,
            "by_type": self.by_type,
            "counted": self.counted,
            "reason": self.reason,
        }


def verified_deflections(
    sessions: pd.DataFrame,
    tickets: pd.DataFrame,
    effort_by_type: pd.Series,
    *,
    window_days: int = 7,
) -> DeflectionSummary:
    """Match resolved sessions to follow-up tickets by (pseudonymized) user and work type."""
    resolved = sessions[sessions[S.SESSION_OUTCOME].astype("string").str.lower() == "resolved"].copy()
    by = [S.REQUESTER] + ([S.WORK_TYPE] if S.WORK_TYPE in resolved.columns else [])
    resolved = resolved.dropna(subset=[S.REQUESTER, S.SESSION_STARTED]).sort_values(S.SESSION_STARTED)
    t = tickets[[*by, S.OPENED, S.UNIT_ID]].dropna(subset=[S.REQUESTER]).sort_values(S.OPENED)
    for col in by:
        resolved[col] = resolved[col].astype("string")
        t[col] = t[col].astype("string")
    matched = pd.merge_asof(
        resolved,
        t,
        left_on=S.SESSION_STARTED,
        right_on=S.OPENED,
        by=by,
        direction="forward",
        tolerance=pd.Timedelta(days=window_days),
        allow_exact_matches=False,
    )
    matched["false"] = matched[S.UNIT_ID].notna()

    rows = []
    hours = 0.0
    if S.WORK_TYPE in matched.columns:
        grouped = matched.groupby(S.WORK_TYPE, observed=True)["false"].agg(["size", "sum"])
        for wtype, g in grouped.iterrows():
            verified = int(g["size"] - g["sum"])
            effort = float(effort_by_type.get(wtype, float("nan")))
            h = verified * effort if effort == effort else 0.0
            hours += h
            rows.append(
                {
                    "work_type": str(wtype),
                    "resolved": int(g["size"]),
                    "verified": verified,
                    "false": int(g["sum"]),
                    "baseline_hours_per_ticket": effort,
                    "hours_avoided": h,
                }
            )
    else:
        verified = int((~matched["false"]).sum())
        hours = verified * float(effort_by_type.mean())
    rows.sort(key=lambda r: -r["hours_avoided"])
    return DeflectionSummary(
        sessions=len(sessions),
        resolved=len(resolved),
        verified=int((~matched["false"]).sum()),
        false=int(matched["false"].sum()),
        hours_avoided=hours,
        by_type=rows,
    )
