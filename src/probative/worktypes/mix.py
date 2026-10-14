"""Mix-adjusted metrics.

When AI absorbs the easy tickets, the average human ticket gets harder and the
headline average rises even if every ticket type got faster. Two tools fix this:

* ``residualize`` subtracts each type's baseline mean, so the causal engine
  compares tickets only with tickets of the same type;
* ``mix_adjusted_mean`` reweights a period's per-type means to the baseline mix,
  for the descriptive before/after view shown beside the headline number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from probative import schema as S


def baseline_weights(df: pd.DataFrame, baseline_mask: pd.Series) -> pd.Series:
    return df.loc[baseline_mask, S.WORK_TYPE].value_counts(normalize=True)


def baseline_type_means(df: pd.DataFrame, metric: str, baseline_mask: pd.Series) -> pd.Series:
    return df.loc[baseline_mask].groupby(S.WORK_TYPE, observed=True)[metric].mean()


def residualize(df: pd.DataFrame, metric: str, baseline_mask: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (residual, type baseline mean) per row; NaN for types never seen at baseline."""
    means = baseline_type_means(df, metric, baseline_mask)
    mu = df[S.WORK_TYPE].map(means).astype(float)
    return df[metric].astype(float) - mu, mu


def mix_adjusted_mean(df: pd.DataFrame, metric: str, weights: pd.Series) -> float:
    by_type = df.groupby(S.WORK_TYPE, observed=True)[metric].mean()
    common = by_type.index.intersection(weights.index)
    if not len(common):
        return float("nan")
    w = weights[common]
    return float((by_type[common] * w).sum() / w.sum())


def before_after_table(
    df: pd.DataFrame,
    *,
    before: pd.Series,
    after: pd.Series,
    resolution_metric: str | None,
    effort_metric: str | None,
) -> list[dict]:
    """Descriptive before/after view for AI teams (no comparison group yet).

    Rows: headline mean resolution time, mix-adjusted mean resolution time, and
    agent hours for the same work (the after-period workload priced at before-
    and after-period hours per ticket type).
    """
    rows: list[dict] = []
    weights = baseline_weights(df, before)
    if resolution_metric and resolution_metric in df.columns:
        b = float(df.loc[before, resolution_metric].mean())
        a = float(df.loc[after, resolution_metric].mean())
        rows.append({"metric": "Headline mean resolution time (h)", "before": b, "after": a})
        b_adj = mix_adjusted_mean(df.loc[before], resolution_metric, weights)
        a_adj = mix_adjusted_mean(df.loc[after], resolution_metric, weights)
        rows.append({"metric": "Mix-adjusted mean resolution time (h)", "before": b_adj, "after": a_adj})
    if effort_metric and effort_metric in df.columns:
        workload = df.loc[after, S.WORK_TYPE].value_counts()
        e_before = df.loc[before].groupby(S.WORK_TYPE, observed=True)[effort_metric].mean()
        e_after = df.loc[after].groupby(S.WORK_TYPE, observed=True)[effort_metric].mean()
        common = workload.index.intersection(e_before.index).intersection(e_after.index)
        rows.append(
            {
                "metric": "Agent hours for the same work",
                "before": float((workload[common] * e_before[common]).sum()),
                "after": float((workload[common] * e_after[common]).sum()),
            }
        )
    for r in rows:
        r["change"] = (r["after"] - r["before"]) / r["before"] if r["before"] else np.nan
    return rows
