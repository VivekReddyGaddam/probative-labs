"""Team-by-week panels: the unit of analysis is always the team."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Panel:
    """Cells of (team, period) with an outcome and a weight.

    ``y`` is the cell outcome (e.g. mean hours per ticket), ``n`` the number of
    units of work behind it, ``offset`` what must be added to ``y`` to get back
    to the raw metric's level (non-zero for type-residualized outcomes).
    ``cohort_by_team`` holds each team's first treated period (NaN = never in
    the window); it is indexed by the *global* team codes shared by every panel
    in an analysis, so one set of bootstrap draws applies to all of them.
    """

    team: np.ndarray
    period: np.ndarray
    y: np.ndarray
    n: np.ndarray
    offset: np.ndarray
    cohort_by_team: np.ndarray
    n_periods: int
    metric: str = ""
    _rows: list[np.ndarray] | None = field(default=None, repr=False)

    @property
    def n_teams(self) -> int:
        return int(self.cohort_by_team.size)

    @property
    def cohort(self) -> np.ndarray:
        return self.cohort_by_team[self.team]

    @property
    def treated(self) -> np.ndarray:
        c = self.cohort
        with np.errstate(invalid="ignore"):
            return ~np.isnan(c) & (self.period >= c)

    @property
    def event_time(self) -> np.ndarray:
        return self.period - self.cohort

    def __len__(self) -> int:
        return int(self.y.size)

    def subset(self, mask: np.ndarray) -> Panel:
        return Panel(
            team=self.team[mask],
            period=self.period[mask],
            y=self.y[mask],
            n=self.n[mask],
            offset=self.offset[mask],
            cohort_by_team=self.cohort_by_team,
            n_periods=self.n_periods,
            metric=self.metric,
        )

    def with_cohorts(self, cohort_by_team: np.ndarray) -> Panel:
        return Panel(self.team, self.period, self.y, self.n, self.offset, cohort_by_team, self.n_periods, self.metric)

    def _rows_by_team(self) -> list[np.ndarray]:
        if self._rows is None:
            order = np.argsort(self.team, kind="stable")
            counts = np.bincount(self.team, minlength=self.n_teams)
            self._rows = np.split(order, np.cumsum(counts)[:-1])
        return self._rows

    def resample(self, draw: np.ndarray) -> Panel:
        """Rebuild the panel from a bootstrap draw of whole teams (duplicates become new teams)."""
        rows = self._rows_by_team()
        parts = [rows[c] for c in draw]
        lens = np.fromiter((p.size for p in parts), dtype=np.int64, count=len(parts))
        idx = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
        return Panel(
            team=np.repeat(np.arange(len(draw)), lens),
            period=self.period[idx],
            y=self.y[idx],
            n=self.n[idx],
            offset=self.offset[idx],
            cohort_by_team=self.cohort_by_team[draw],
            n_periods=self.n_periods,
            metric=self.metric,
        )


def mean_panel(
    df: pd.DataFrame,
    value_col: str,
    *,
    team_code_col: str,
    period_col: str,
    cohort_by_team: np.ndarray,
    n_periods: int,
    offset_col: str | None = None,
    metric: str = "",
) -> Panel:
    """Cell mean of ``value_col`` per (team, period), weighted by the number of units."""
    cols = [team_code_col, period_col, value_col] + ([offset_col] if offset_col else [])
    d = df[cols].dropna(subset=[value_col])
    g = d.groupby([team_code_col, period_col], sort=True)
    stats = g[value_col].agg(["mean", "size"])
    offset = g[offset_col].mean().to_numpy(float) if offset_col else np.zeros(len(stats))
    teams = stats.index.get_level_values(0).to_numpy(np.int64)
    periods = stats.index.get_level_values(1).to_numpy(np.int64)
    return Panel(
        team=teams,
        period=periods,
        y=stats["mean"].to_numpy(float),
        n=stats["size"].to_numpy(float),
        offset=offset,
        cohort_by_team=cohort_by_team,
        n_periods=n_periods,
        metric=metric or value_col,
    )


def count_panel(
    df: pd.DataFrame,
    *,
    team_code_col: str,
    period_col: str,
    cohort_by_team: np.ndarray,
    n_periods: int,
    active: pd.DataFrame,
    metric: str = "units_per_week",
) -> Panel:
    """Units of work per team-week, including zero weeks within each team's active span.

    ``active`` has one row per team code with ``first`` and ``last`` periods.
    """
    counts = df.groupby([team_code_col, period_col]).size()
    teams, periods = [], []
    for code, row in active.iterrows():
        span = np.arange(int(row["first"]), int(row["last"]) + 1)
        teams.append(np.full(span.size, int(code)))
        periods.append(span)
    team_arr = np.concatenate(teams) if teams else np.empty(0, np.int64)
    period_arr = np.concatenate(periods) if periods else np.empty(0, np.int64)
    idx = pd.MultiIndex.from_arrays([team_arr, period_arr])
    y = counts.reindex(idx, fill_value=0).to_numpy(float)
    return Panel(
        team=team_arr.astype(np.int64),
        period=period_arr.astype(np.int64),
        y=y,
        n=np.ones_like(y),
        offset=np.zeros_like(y),
        cohort_by_team=cohort_by_team,
        n_periods=n_periods,
        metric=metric,
    )
