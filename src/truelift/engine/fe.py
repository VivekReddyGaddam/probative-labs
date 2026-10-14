"""Weighted two-way fixed effects by alternating projections.

Fits ``y ≈ a[team] + b[period]`` on unbalanced panels without building dummy
matrices, which keeps the team-level bootstrap fast.
"""

from __future__ import annotations

import numpy as np


def _wmean(values: np.ndarray, groups: np.ndarray, w: np.ndarray, n_groups: int) -> tuple[np.ndarray, np.ndarray]:
    num = np.bincount(groups, weights=values * w, minlength=n_groups)
    den = np.bincount(groups, weights=w, minlength=n_groups)
    with np.errstate(invalid="ignore", divide="ignore"):
        return num / den, den


def fit_twoway(
    y: np.ndarray,
    team: np.ndarray,
    period: np.ndarray,
    w: np.ndarray,
    n_teams: int,
    n_periods: int,
    *,
    tol: float = 1e-10,
    max_iter: int = 5000,
) -> tuple[np.ndarray, np.ndarray]:
    """Return team effects ``a`` and period effects ``b`` (NaN where a group has no data).

    Only the sums ``a[i] + b[t]`` are identified; that is all the imputation
    estimator uses.
    """
    a = np.zeros(n_teams)
    b = np.zeros(n_periods)
    scale = tol * (1.0 + float(np.nanstd(y)) if y.size else 1.0)
    prev = np.zeros_like(y)
    wa = wb = None
    for _ in range(max_iter):
        a, wa = _wmean(y - b[period], team, w, n_teams)
        a = np.nan_to_num(a)
        b, wb = _wmean(y - a[team], period, w, n_periods)
        b = np.nan_to_num(b)
        fitted = a[team] + b[period]
        if np.max(np.abs(fitted - prev), initial=0.0) < scale:
            break
        prev = fitted
    a = a.astype(float)
    b = b.astype(float)
    if wa is not None:
        a[wa == 0] = np.nan
    if wb is not None:
        b[wb == 0] = np.nan
    return a, b


def demean_twoway(
    x: np.ndarray,
    team: np.ndarray,
    period: np.ndarray,
    w: np.ndarray,
    n_teams: int,
    n_periods: int,
    *,
    tol: float = 1e-10,
    max_iter: int = 5000,
) -> np.ndarray:
    """Residualize each column of ``x`` on team and period effects (weighted)."""
    x = np.array(x, dtype=float, copy=True)
    if x.ndim == 1:
        x = x[:, None]
    for j in range(x.shape[1]):
        col = x[:, j]
        scale = tol * (1.0 + float(np.std(col)))
        for _ in range(max_iter):
            m_i, _ = _wmean(col, team, w, n_teams)
            col = col - np.nan_to_num(m_i)[team]
            m_t, _ = _wmean(col, period, w, n_periods)
            m_t = np.nan_to_num(m_t)
            col = col - m_t[period]
            if np.max(np.abs(m_t), initial=0.0) < scale:
                break
        x[:, j] = col
    return x
