"""Rule-based work typing from existing fields."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from truelift.config import TypingConfig


def _match(series: pd.Series, cond: str | list[str]) -> pd.Series:
    values = cond if isinstance(cond, list) else [cond]
    hit = pd.Series(False, index=series.index)
    text = series.astype("string").fillna("")
    for v in values:
        if v.startswith("re:"):
            hit |= text.str.contains(re.compile(v[3:], re.IGNORECASE), na=False)
        else:
            hit |= text.str.casefold() == v.casefold()
    return hit


def assign_work_types(df: pd.DataFrame, cfg: TypingConfig, baseline_mask: pd.Series | None = None) -> pd.Series:
    """Type each unit of work.

    Explicit rules are applied first, in order. Remaining rows are typed from the
    configured fields (e.g. ``category / subcategory``). Types that make up less
    than ``min_share`` of the *baseline* volume are folded into ``other_label`` so
    the type list stays stable across periods.
    """
    labels = pd.Series(pd.NA, index=df.index, dtype="string")

    for rule in cfg.rules:
        hit = pd.Series(True, index=df.index)
        for field, cond in rule.when.items():
            if field not in df.columns:
                hit &= False
                continue
            hit &= _match(df[field], cond)
        labels = labels.mask(labels.isna() & hit, rule.name)

    fields = [f for f in cfg.fields if f in df.columns]
    if fields:
        parts = [df[f].astype("string").str.strip().fillna("(blank)") for f in fields]
        joined = parts[0]
        for p in parts[1:]:
            joined = joined + " / " + p
        labels = labels.fillna(joined)
    labels = labels.fillna(cfg.other_label)

    mask = baseline_mask if baseline_mask is not None and baseline_mask.any() else pd.Series(True, index=df.index)
    share = labels[mask].value_counts(normalize=True)
    keep = set(share[share >= cfg.min_share].index)
    rule_names = {r.name for r in cfg.rules}
    folded = labels.where(labels.isin(keep | rule_names), cfg.other_label)
    return folded.astype("string")


def type_shares(labels: pd.Series) -> pd.Series:
    return labels.value_counts(normalize=True).sort_values(ascending=False)


def entropy_bits(p: np.ndarray) -> float:
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())
