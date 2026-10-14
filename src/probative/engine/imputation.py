"""Imputation estimator for staggered rollouts, with a team-level bootstrap.

Following Borusyak, Jaravel and Spiess (2024, *Review of Economic Studies*):

1. Fit team and week effects using only cells *without* AI (comparison teams,
   and AI teams before their rollout).
2. For every cell *with* AI, predict what would have happened without it:
   ``ŷ0 = team effect + week effect``.
3. The effect in that cell is ``τ = y − ŷ0``; aggregate τ by work volume.

Each AI cell is compared only with untreated data, so the estimator never uses
already-treated teams as controls — the flaw that biases ordinary two-way fixed
effects regressions when effects grow over time. A single rollout date with a
comparison group is the special case "two-group difference-in-differences".

Uncertainty comes from resampling whole teams (stratified by rollout wave),
which respects the fact that tickets within a team are not independent. With
few teams per wave the plain bootstrap is too narrow, so intervals get a
small-sample correction (``TeamBootstrap.scale``): a t critical value with
(teams − 1) degrees of freedom and the n/(n − 1) variance correction per wave.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from probative.engine.fe import demean_twoway, fit_twoway
from probative.engine.panel import Panel


class EstimationError(ValueError):
    pass


@dataclass
class Fit:
    att: float  # effect per unit of work (volume-weighted)
    att_cells: float  # effect per treated team-week (equal weights)
    total: float  # summed effect over all treated units of work
    units: float  # treated units of work
    counterfactual_mean: float  # mean outcome AI cells would have had without AI
    event: dict[int, float] = field(default_factory=dict)
    event_units: dict[int, float] = field(default_factory=dict)
    event_teams: dict[int, int] = field(default_factory=dict)
    pre: dict[int, float] = field(default_factory=dict)
    pre_cov: np.ndarray | None = None  # cluster-robust covariance of the lead coefficients
    n_clusters: int = 0
    n_treated_cells: int = 0
    n_dropped_cells: int = 0
    n_treated_teams: int = 0
    n_untreated_cells: int = 0


def fit(panel: Panel, *, pretrend_k: int = 0, event_max: int | None = None) -> Fit:
    treated = panel.treated
    untreated = ~treated
    if not treated.any():
        raise EstimationError("no cells with AI in the analysis window")
    if not untreated.any():
        raise EstimationError("no cells without AI to compare against")

    iu, tu = panel.team[untreated], panel.period[untreated]
    yu, wu = panel.y[untreated], panel.n[untreated]
    a, b = fit_twoway(yu, iu, tu, wu, panel.n_teams, panel.n_periods)

    it, tt = panel.team[treated], panel.period[treated]
    y0 = a[it] + b[tt]
    ok = ~np.isnan(y0)
    if not ok.any():
        raise EstimationError(
            "no AI cell has both a pre-rollout history and a comparison week; "
            "the rollout cannot be compared with anything"
        )
    tau = panel.y[treated][ok] - y0[ok]
    w = panel.n[treated][ok]
    units = float(w.sum())
    e = (tt[ok] - panel.cohort_by_team[it[ok]]).astype(np.int64)
    teams_ok = it[ok]

    out = Fit(
        att=float((w * tau).sum() / units),
        att_cells=float(tau.mean()),
        total=float((w * tau).sum()),
        units=units,
        counterfactual_mean=float((w * (y0[ok] + panel.offset[treated][ok])).sum() / units),
        n_treated_cells=int(ok.sum()),
        n_dropped_cells=int((~ok).sum()),
        n_treated_teams=int(np.unique(teams_ok).size),
        n_untreated_cells=int(untreated.sum()),
        n_clusters=int(np.unique(panel.team).size),
    )

    limit = event_max if event_max is not None else int(e.max())
    for k in np.unique(e):
        if k > limit:
            continue
        m = e == k
        out.event[int(k)] = float((w[m] * tau[m]).sum() / w[m].sum())
        out.event_units[int(k)] = float(w[m].sum())
        out.event_teams[int(k)] = int(np.unique(teams_ok[m]).size)

    if pretrend_k > 0:
        out.pre, out.pre_cov = _pretrend(panel, untreated, pretrend_k)
    return out


def _pretrend(panel: Panel, untreated: np.ndarray, k_max: int) -> tuple[dict[int, float], np.ndarray | None]:
    """Lead coefficients for the weeks just before rollout, estimated on untreated cells only.

    Each coefficient is the average deviation of AI teams from the fitted
    team-and-week model *k* weeks before their rollout. Under parallel trends they
    should all be close to zero. The covariance is cluster-robust by team (CR1).
    """
    e = panel.event_time[untreated]
    leads = [k for k in range(1, k_max + 1) if np.any(e == -k)]
    if not leads:
        return {}, None
    iu, tu = panel.team[untreated], panel.period[untreated]
    wu = panel.n[untreated]
    lead_mat = np.column_stack([(e == -k).astype(float) for k in leads])
    yd = demean_twoway(panel.y[untreated], iu, tu, wu, panel.n_teams, panel.n_periods)[:, 0]
    ld = demean_twoway(lead_mat, iu, tu, wu, panel.n_teams, panel.n_periods)
    sw = np.sqrt(wu)
    xw, yw = ld * sw[:, None], yd * sw
    coef, *_ = np.linalg.lstsq(xw, yw, rcond=None)
    pre = {-k: float(c) for k, c in zip(leads, coef, strict=True)}
    resid = yw - xw @ coef
    clusters, inv = np.unique(iu, return_inverse=True)
    g, n, k = clusters.size, yw.size, len(leads)
    if g < 3:
        return pre, None
    scores = np.zeros((g, k))
    np.add.at(scores, inv, xw * resid[:, None])
    bread = np.linalg.pinv(xw.T @ xw)
    dof = max(n - k - panel.n_periods, 1)
    cov = bread @ (scores.T @ scores) @ bread * (g / (g - 1)) * ((n - 1) / dof)
    return pre, cov


class TeamBootstrap:
    """Bootstrap draws of whole teams, stratified by rollout cohort.

    Draws are made over global team codes, so the same draws can be applied to
    every panel in an analysis and their results combined jointly (e.g. value
    summed across metrics keeps its correlation).
    """

    def __init__(self, cohort_by_team: np.ndarray, reps: int, seed: int):
        rng = np.random.default_rng(seed)
        key = np.where(np.isnan(cohort_by_team), -1.0, cohort_by_team)
        self.strata = [np.flatnonzero(key == k) for k in np.unique(key)]
        self.reps = reps
        self.seed = seed
        sizes = np.array([s.size for s in self.strata], dtype=float)
        self.n_clusters = int(sizes.sum())
        # Resampling n teams with replacement understates a stratum's variance by (n - 1) / n.
        per_stratum = np.minimum(sizes / np.maximum(sizes - 1, 1), 2.0)
        self.variance_correction = float(np.sum(sizes * per_stratum) / sizes.sum())
        self.draws = [
            np.concatenate([rng.choice(s, size=s.size, replace=True) for s in self.strata]) for _ in range(reps)
        ]

    @property
    def smallest_stratum(self) -> int:
        return min(s.size for s in self.strata)

    def scale(self, level: float) -> float:
        """Factor by which percentile half-widths are widened for few teams."""
        df = max(self.n_clusters - 1, 1)
        z = stats.norm.ppf(0.5 + level / 2)
        t = stats.t.ppf(0.5 + level / 2, df)
        return float(t / z * np.sqrt(self.variance_correction))

    @property
    def df(self) -> int:
        return max(self.n_clusters - 1, 1)


@dataclass
class Estimate:
    metric: str
    label: str
    point: Fit
    draws_att: np.ndarray
    draws_total: np.ndarray
    draws_rel: np.ndarray
    draws_event: dict[int, np.ndarray]
    draws_pre: dict[int, np.ndarray]
    failed_draws: int
    level: float = 0.95
    variance_correction: float = 1.0
    df: int = 1000
    jackknife_se: float = float("nan")

    @property
    def inflation(self) -> float:
        """How much the bootstrap spread is widened: the per-wave n/(n-1) correction, or more if the
        delete-one-team jackknife finds more variance (it holds up better with few teams per wave)."""
        d = self.draws_att[~np.isnan(self.draws_att)]
        base = float(np.sqrt(self.variance_correction))
        raw = float(d.std(ddof=1)) if d.size > 1 else 0.0
        if raw > 0 and np.isfinite(self.jackknife_se):
            return max(base, self.jackknife_se / raw)
        return base

    def _scale(self, level: float) -> float:
        z = stats.norm.ppf(0.5 + level / 2)
        t = stats.t.ppf(0.5 + level / 2, self.df)
        return float(t / z * self.inflation)

    def adjusted_total_draws(self) -> np.ndarray:
        """Bootstrap draws of the total effect, widened like the intervals; used for ROI simulation."""
        return self.point.total + self.inflation * (self.draws_total - self.point.total)

    def _ci(self, draws: np.ndarray, center: float, level: float | None = None) -> tuple[float, float]:
        """Percentile interval, widened around the point estimate for few teams."""
        level = level or self.level
        d = draws[~np.isnan(draws)]
        if d.size < 10:
            return float("nan"), float("nan")
        alpha = (1 - level) / 2
        lo, hi = np.quantile(d, [alpha, 1 - alpha])
        f = self._scale(level)
        return float(center - f * (center - lo)), float(center + f * (hi - center))

    def interval(self, level: float) -> tuple[float, float]:
        return self._ci(self.draws_att, self.point.att, level)

    @property
    def att(self) -> float:
        return self.point.att

    @property
    def ci(self) -> tuple[float, float]:
        return self._ci(self.draws_att, self.point.att)

    @property
    def total_ci(self) -> tuple[float, float]:
        return self._ci(self.draws_total, self.point.total)

    @property
    def relative(self) -> float:
        cf = self.point.counterfactual_mean
        return self.point.att / cf if cf else float("nan")

    @property
    def relative_ci(self) -> tuple[float, float]:
        return self._ci(self.draws_rel, self.relative)

    @property
    def se(self) -> float:
        """Standard error: bootstrap spread with the small-sample and jackknife corrections."""
        d = self.draws_att[~np.isnan(self.draws_att)]
        return float(d.std(ddof=1) * self.inflation) if d.size > 1 else float("nan")

    @property
    def p_value(self) -> float:
        """Two-sided p-value for 'no effect' (t with teams − 1 degrees of freedom)."""
        se = self.se
        if not se or np.isnan(se):
            return float("nan")
        return float(2 * stats.t.sf(abs(self.point.att) / se, self.df))

    @property
    def excludes_zero(self) -> bool:
        lo, hi = self.ci
        return bool(lo > 0 or hi < 0)

    def event_rows(self) -> list[dict]:
        rows = []
        for k in sorted(self.point.pre):
            lo, hi = self._ci(self.draws_pre.get(k, np.array([])), self.point.pre[k])
            rows.append({"event_time": k, "estimate": self.point.pre[k], "ci_low": lo, "ci_high": hi, "pre": True})
        for k in sorted(self.point.event):
            lo, hi = self._ci(self.draws_event.get(k, np.array([])), self.point.event[k])
            rows.append(
                {
                    "event_time": k,
                    "estimate": self.point.event[k],
                    "ci_low": lo,
                    "ci_high": hi,
                    "pre": False,
                    "teams": self.point.event_teams.get(k, 0),
                    "units": self.point.event_units.get(k, 0.0),
                }
            )
        return rows

    def pretrend_test(self) -> tuple[float, int, float]:
        """Joint test that all lead coefficients are zero.

        Uses the team-clustered covariance with a Hotelling T-squared correction
        (F with q and teams − q degrees of freedom); falls back to the bootstrap
        covariance if the analytic one is unavailable. Returns (F, q, p).
        """
        keys = sorted(self.point.pre)
        if not keys:
            return float("nan"), 0, float("nan")
        g = np.array([self.point.pre[k] for k in keys])
        cov = self.point.pre_cov
        if cov is not None and cov.shape == (len(keys), len(keys)):
            lead_order = sorted(keys, reverse=True)  # pre_cov rows follow leads -1, -2, ..., -K
            idx = [lead_order.index(k) for k in keys]
            cov = cov[np.ix_(idx, idx)]
            rank = int(np.linalg.matrix_rank(cov))
            clusters = self.point.n_clusters
            if rank and clusters > rank + 1:
                # Hotelling T-squared small-sample correction: the covariance is estimated from
                # only `clusters` teams, so a plain Wald/F test over-rejects with many leads.
                wald = float(g @ np.linalg.pinv(cov) @ g)
                f_stat = wald * (clusters - rank) / (rank * (clusters - 1))
                return f_stat, rank, float(stats.f.sf(f_stat, rank, clusters - rank))
        mat = np.column_stack([self.draws_pre[k] for k in keys])
        mat = mat[~np.isnan(mat).any(axis=1)]
        if mat.shape[0] < len(keys) + 5:
            return float("nan"), len(keys), float("nan")
        cov = np.atleast_2d(np.cov(mat, rowvar=False))
        rank = int(np.linalg.matrix_rank(cov))
        stat = float(g @ np.linalg.pinv(cov) @ g) / max(rank, 1)
        return stat, rank, float(stats.f.sf(stat, rank, self.df)) if rank else float("nan")

    def summary(self) -> dict:
        lo, hi = self.ci
        rlo, rhi = self.relative_ci
        tlo, thi = self.total_ci
        return {
            "metric": self.metric,
            "label": self.label,
            "estimate": self.att,
            "ci_low": lo,
            "ci_high": hi,
            "se": self.se,
            "p_value": self.p_value,
            "relative": self.relative,
            "relative_ci_low": rlo,
            "relative_ci_high": rhi,
            "total": self.point.total,
            "total_ci_low": tlo,
            "total_ci_high": thi,
            "units": self.point.units,
            "counterfactual_mean": self.point.counterfactual_mean,
            "treated_teams": self.point.n_treated_teams,
            "treated_cells": self.point.n_treated_cells,
            "dropped_cells": self.point.n_dropped_cells,
            "failed_draws": self.failed_draws,
        }


def estimate(
    panel: Panel,
    boot: TeamBootstrap,
    *,
    label: str = "",
    pretrend_k: int = 0,
    event_max: int | None = None,
    level: float = 0.95,
) -> Estimate:
    point = fit(panel, pretrend_k=pretrend_k, event_max=event_max)
    reps = len(boot.draws)
    d_att = np.full(reps, np.nan)
    d_total = np.full(reps, np.nan)
    d_rel = np.full(reps, np.nan)
    d_event = {k: np.full(reps, np.nan) for k in point.event}
    d_pre = {k: np.full(reps, np.nan) for k in point.pre}
    failed = 0
    for r, draw in enumerate(boot.draws):
        try:
            f = fit(panel.resample(draw), pretrend_k=pretrend_k, event_max=event_max)
        except EstimationError:
            failed += 1
            continue
        d_att[r] = f.att
        d_total[r] = f.total
        d_rel[r] = f.att / f.counterfactual_mean if f.counterfactual_mean else np.nan
        for k in d_event:
            if k in f.event:
                d_event[k][r] = f.event[k]
        for k in d_pre:
            if k in f.pre:
                d_pre[k][r] = f.pre[k]
    return Estimate(
        jackknife_se=_jackknife_se(panel),
        metric=panel.metric,
        label=label or panel.metric,
        point=point,
        draws_att=d_att,
        draws_total=d_total,
        draws_rel=d_rel,
        draws_event=d_event,
        draws_pre=d_pre,
        failed_draws=failed,
        level=level,
        variance_correction=boot.variance_correction,
        df=boot.df,
    )


def _jackknife_se(panel: Panel) -> float:
    """Delete-one-team jackknife standard error of the per-unit effect."""
    values = []
    for code in np.unique(panel.team):
        try:
            values.append(fit(panel.subset(panel.team != code)).att)
        except EstimationError:
            continue
    g = len(values)
    if g < 3:
        return float("nan")
    v = np.array(values)
    return float(np.sqrt((g - 1) / g * np.sum((v - v.mean()) ** 2)))
