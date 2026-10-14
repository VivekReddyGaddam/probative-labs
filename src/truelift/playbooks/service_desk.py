"""IT service management and employee support: the first playbook.

High ticket volume, rich timestamps and natural staggered rollouts by assignment
group make the service desk the most measurable function in the enterprise.
"""

from __future__ import annotations

import pandas as pd

from truelift import schema as S
from truelift.guardrails import GuardrailSpec
from truelift.playbooks.base import Metric, Playbook


def derive(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if S.RESOLVED in out.columns:
        out[S.RESOLUTION] = (out[S.RESOLVED] - out[S.OPENED]).dt.total_seconds() / 3600.0
    if S.REOPENS in out.columns:
        out[S.REOPENED] = (out[S.REOPENS].fillna(0) > 0).astype(float)
    if S.ESCALATED in out.columns:
        out["escalated_rate"] = out[S.ESCALATED].astype("Float64").astype(float)
    hour = out[S.OPENED].dt.hour
    weekend = out[S.OPENED].dt.weekday >= 5
    out[S.AFTER_HOURS] = ((hour < 8) | (hour >= 18) | weekend).astype(float)
    return out


SERVICE_DESK = Playbook(
    name="service_desk",
    title="IT service desk",
    unit_noun="ticket",
    unit_noun_plural="tickets",
    metrics={
        S.EFFORT: Metric(S.EFFORT, "Agent hours per ticket", " h"),
        S.RESOLUTION: Metric(S.RESOLUTION, "Resolution time", " h"),
    },
    guardrails=(
        GuardrailSpec("reopen_rate", "Reopen rate", S.REOPENED, True, 0.01, "absolute", " (share)"),
        GuardrailSpec("reassignments", "Reassignments per ticket", S.REASSIGNMENTS, True, 0.10, "absolute"),
        GuardrailSpec("escalation_rate", "Escalation rate", "escalated_rate", True, 0.01, "absolute", " (share)"),
        GuardrailSpec("csat", "Satisfaction score", S.CSAT, False, 0.10, "absolute", " pts"),
        GuardrailSpec(
            "false_deflection", "False deflection rate", "false_deflection", True, 0.15, "absolute", level=True
        ),
    ),
    placebo_metrics={
        S.AFTER_HOURS: Metric(S.AFTER_HOURS, "Share of tickets opened outside business hours", "", fmt=".3f"),
    },
    derive=derive,
    traps=(
        "Deflection pushes headline resolution time up even when every ticket type got faster; "
        "results are mix-adjusted by ticket type.",
        "When AI does the categorizing, post-AI categories are not comparable with pre-AI ones; "
        "the category drift check looks for this.",
        "Channel shifts to self-service change ticket volume and mix at the same time.",
        "Seasonal spikes (onboarding waves, patch cycles, year-end) coincide with rollouts; the "
        "comparison group absorbs them.",
    ),
)
