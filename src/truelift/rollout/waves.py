"""Wave planner: assign teams to rollout waves in a recorded, randomized order."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from truelift import schema as S


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def plan_waves(
    teams: pd.DataFrame,
    *,
    n_waves: int,
    first_start: date,
    spacing_weeks: int = 4,
    holdout_share: float = 0.0,
    holdout_weeks: int | None = None,
    stratify_by: str | None = None,
    seed: int,
) -> pd.DataFrame:
    """Return one row per team: wave, start date, kind and the randomization seed.

    Teams are shuffled within strata (e.g. region or size band) and dealt into
    waves round-robin so each wave gets a similar mix. A ``holdout_share`` of
    teams is kept without AI and released ``holdout_weeks`` after the last wave.
    The output is a valid exposure file; ``randomized`` is recorded so the
    analysis can award grade A.
    """
    if n_waves < 1:
        raise ValueError("n_waves must be at least 1")
    if not 0 <= holdout_share < 1:
        raise ValueError("holdout_share must be in [0, 1)")
    if n_waves == 1 and holdout_share == 0:
        raise ValueError("a single wave needs a holdout to create a comparison group")
    rng = np.random.default_rng(seed)
    start = _monday(first_start)
    ids = teams[S.TEAM].astype(str).tolist()
    strata = teams[stratify_by].astype(str).tolist() if stratify_by else ["all"] * len(ids)

    slots = n_waves + (1 if holdout_share > 0 else 0)
    weights = [(1 - holdout_share) / n_waves] * n_waves + ([holdout_share] if holdout_share > 0 else [])
    assignment: dict[str, int] = {}
    targets = np.array(weights) * len(ids)
    totals = np.zeros(slots)
    for stratum in sorted(set(strata)):
        members = [t for t, s in zip(ids, strata, strict=True) if s == stratum]
        rng.shuffle(members)
        counts = np.floor(np.array(weights) * len(members)).astype(int)
        # Leftover teams go to the slots furthest below their overall target, so waves stay
        # balanced across strata; ties are broken at random.
        for _ in range(len(members) - counts.sum()):
            deficit = targets - (totals + counts) + rng.random(slots) * 1e-6
            counts[int(np.argmax(deficit))] += 1
        totals += counts
        pos = 0
        for slot, k in enumerate(counts):
            for t in members[pos : pos + k]:
                assignment[t] = slot
            pos += k

    hold_release = start + timedelta(weeks=(n_waves - 1) * spacing_weeks + (holdout_weeks or 12))
    rows = []
    for t in ids:
        slot = assignment[t]
        holdout = holdout_share > 0 and slot == n_waves
        rows.append(
            {
                S.TEAM: t,
                "wave": "holdout" if holdout else slot + 1,
                "start_date": (hold_release if holdout else start + timedelta(weeks=slot * spacing_weeks)).isoformat(),
                "kind": "holdout" if holdout else "wave",
                "randomized": True,
                "seed": seed,
            }
        )
    return pd.DataFrame(rows)
