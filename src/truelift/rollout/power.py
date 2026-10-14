"""Power calculator: how many teams and weeks are needed to detect the hoped-for effect.

Simulates team-by-week panels with team differences, shared weekly shocks and
autocorrelated noise, runs the same imputation estimator used for real results,
and reads off the standard error. Noise can be calibrated from the customer's own
pre-rollout history with :func:`estimate_noise`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

from truelift.engine.diagnostics import MDE_MULTIPLIER
from truelift.engine.fe import fit_twoway
from truelift.engine.imputation import EstimationError, fit
from truelift.engine.panel import Panel


@dataclass(frozen=True)
class NoiseModel:
    mean: float = 1.0  # average outcome per team-week, e.g. hours per ticket
    sd_team: float = 0.25  # spread of team averages
    sd_week: float = 0.05  # shocks shared by all teams in a week
    sd_cell: float = 0.15  # team-week noise
    rho: float = 0.3  # week-to-week autocorrelation of team-week noise

    def to_dict(self) -> dict:
        return asdict(self)


def noise_from_units(units: pd.DataFrame, metric: str) -> NoiseModel:
    """Calibrate noise from canonical work units (``team_id``, ``opened_at`` and the metric).

    Treats all history as untreated, which is right before a rollout; afterwards
    it slightly overstates noise, so plans err on the side of more teams or weeks.
    """
    from truelift import schema as S
    from truelift.engine.panel import mean_panel

    d = units.dropna(subset=[S.TEAM, S.OPENED, metric]).copy()
    if d.empty:
        raise ValueError(f"no {metric} values to calibrate from")
    origin = d[S.OPENED].min().normalize()
    origin -= pd.Timedelta(days=origin.weekday())
    d["_period"] = ((d[S.OPENED] - origin).dt.days // 7).astype(int)
    codes = {t: i for i, t in enumerate(sorted(d[S.TEAM].unique()))}
    d["_code"] = d[S.TEAM].map(codes).astype(int)
    panel = mean_panel(
        d,
        metric,
        team_code_col="_code",
        period_col="_period",
        cohort_by_team=np.full(len(codes), np.nan),
        n_periods=int(d["_period"].max()) + 1,
    )
    return estimate_noise(panel)


def estimate_noise(panel: Panel) -> NoiseModel:
    """Calibrate the noise model from untreated cells of a real panel."""
    untreated = ~panel.treated
    p = panel.subset(untreated)
    a, b = fit_twoway(p.y, p.team, p.period, p.n, p.n_teams, p.n_periods)
    resid = p.y - (a[p.team] + b[p.period])
    order = np.lexsort((p.period, p.team))
    r, t = resid[order], p.team[order]
    same = t[1:] == t[:-1]
    rho = float(np.corrcoef(r[1:][same], r[:-1][same])[0, 1]) if same.sum() > 10 else 0.0
    weights = p.n / p.n.sum()
    return NoiseModel(
        mean=float((p.y * weights).sum()),
        sd_team=float(np.nanstd(a[~np.isnan(a)])),
        sd_week=float(np.nanstd(b[~np.isnan(b)])),
        sd_cell=float(np.std(resid)),
        rho=float(np.clip(rho, -0.9, 0.95)),
    )


@dataclass
class PowerResult:
    n_teams: int
    n_waves: int
    weeks_pre: int
    weeks_post: int
    wave_spacing: int
    holdout_share: float
    effect: float
    effect_relative: float
    se: float
    mde: float
    mde_relative: float
    power: float
    bias: float
    sims: int

    def to_dict(self) -> dict:
        return asdict(self)


def _cohorts(n_teams: int, n_waves: int, holdout_share: float, weeks_pre: int, spacing: int) -> np.ndarray:
    n_hold = int(round(n_teams * holdout_share))
    treated = n_teams - n_hold
    cohort = np.full(n_teams, np.nan)
    waves = np.array_split(np.arange(treated), n_waves)
    for w, idx in enumerate(waves):
        cohort[idx] = weeks_pre + w * spacing
    return cohort


def simulate_power(
    *,
    n_teams: int,
    n_waves: int,
    weeks_pre: int,
    weeks_post: int,
    effect: float,
    noise: NoiseModel,
    wave_spacing: int = 4,
    holdout_share: float = 0.2,
    alpha: float = 0.05,
    sims: int = 200,
    seed: int = 7,
) -> PowerResult:
    """``effect`` is the absolute change per unit of work (e.g. -0.15 hours per ticket).

    ``weeks_post`` counts weeks after the *last* wave starts.
    """
    if n_waves < 1 or n_teams < 4:
        raise ValueError("need at least 1 wave and 4 teams")
    if holdout_share == 0 and n_waves == 1:
        raise ValueError("a single wave needs a holdout share > 0 to have a comparison group")
    rng = np.random.default_rng(seed)
    cohort = _cohorts(n_teams, n_waves, holdout_share, weeks_pre, wave_spacing)
    n_periods = weeks_pre + (n_waves - 1) * wave_spacing + weeks_post
    team = np.repeat(np.arange(n_teams), n_periods)
    period = np.tile(np.arange(n_periods), n_teams)
    c = cohort[team]
    with np.errstate(invalid="ignore"):
        treated = ~np.isnan(c) & (period >= c)
    estimates = []
    innov_sd = noise.sd_cell * np.sqrt(max(1e-9, 1 - noise.rho**2))
    for _ in range(sims):
        a = rng.normal(0, noise.sd_team, n_teams)
        b = rng.normal(0, noise.sd_week, n_periods)
        e = np.empty((n_teams, n_periods))
        e[:, 0] = rng.normal(0, noise.sd_cell, n_teams)
        shocks = rng.normal(0, innov_sd, (n_teams, n_periods))
        for t in range(1, n_periods):
            e[:, t] = noise.rho * e[:, t - 1] + shocks[:, t]
        y = noise.mean + a[team] + b[period] + e.ravel() + effect * treated
        p = Panel(team, period, y, np.ones_like(y), np.zeros_like(y), cohort, n_periods, "sim")
        try:
            estimates.append(fit(p).att)
        except EstimationError:
            continue
    est = np.array(estimates)
    se = float(est.std(ddof=1))
    z = stats.norm.ppf(1 - alpha / 2)
    shift = abs(effect) / se if se > 0 else np.inf
    power = float(stats.norm.cdf(shift - z) + stats.norm.cdf(-shift - z))
    mde = MDE_MULTIPLIER * se
    return PowerResult(
        n_teams=n_teams,
        n_waves=n_waves,
        weeks_pre=weeks_pre,
        weeks_post=weeks_post,
        wave_spacing=wave_spacing,
        holdout_share=holdout_share,
        effect=effect,
        effect_relative=effect / noise.mean if noise.mean else float("nan"),
        se=se,
        mde=mde,
        mde_relative=mde / abs(noise.mean) if noise.mean else float("nan"),
        power=power,
        bias=float(est.mean() - effect),
        sims=len(est),
    )


def power_table(
    *,
    team_options: list[int],
    post_options: list[int],
    n_waves: int,
    weeks_pre: int,
    effect: float,
    noise: NoiseModel,
    wave_spacing: int = 4,
    holdout_share: float = 0.2,
    sims: int = 200,
    seed: int = 7,
) -> list[PowerResult]:
    out = []
    for n_teams in team_options:
        for weeks_post in post_options:
            out.append(
                simulate_power(
                    n_teams=n_teams,
                    n_waves=n_waves,
                    weeks_pre=weeks_pre,
                    weeks_post=weeks_post,
                    effect=effect,
                    noise=noise,
                    wave_spacing=wave_spacing,
                    holdout_share=holdout_share,
                    sims=sims,
                    seed=seed,
                )
            )
    return out
