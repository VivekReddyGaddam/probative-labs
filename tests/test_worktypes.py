import pandas as pd
import pytest

from probative import schema as S
from probative.config import TypeRule, TypingConfig
from probative.worktypes import assign_work_types, before_after_table, category_drift, mix_adjusted_mean, residualize


def test_rules_then_fields_then_folding():
    df = pd.DataFrame(
        {
            "category": ["Software"] * 50 + ["Hardware"] * 49 + ["Rare"],
            "subcategory": ["Email"] * 50 + ["Laptop"] * 49 + ["Thing"],
            S.DESCRIPTION: ["password reset please"] + [""] * 99,
        }
    )
    cfg = TypingConfig(rules=[TypeRule(name="Password", when={S.DESCRIPTION: "re:password"})], min_share=0.02)
    labels = assign_work_types(df, cfg)
    assert labels[0] == "Password"
    assert labels[1] == "Software / Email"
    assert labels[99] == "Other"


def test_deflection_trap_mix_adjustment():
    """Every type gets 10% faster but easy work disappears: headline rises, mix-adjusted falls."""
    before = pd.DataFrame({S.WORK_TYPE: ["easy"] * 50 + ["hard"] * 50, "y": [1.0] * 50 + [10.0] * 50})
    after = pd.DataFrame({S.WORK_TYPE: ["easy"] * 10 + ["hard"] * 50, "y": [0.9] * 10 + [9.0] * 50})
    df = pd.concat([before, after], ignore_index=True)
    b = pd.Series([True] * 100 + [False] * 60)
    assert after["y"].mean() > before["y"].mean()
    w = before[S.WORK_TYPE].value_counts(normalize=True)
    assert mix_adjusted_mean(after, "y", w) == pytest.approx(0.9 * 5.5)
    resid, mu = residualize(df, "y", b)
    assert resid[~b].mean() < 0
    rows = before_after_table(
        df.assign(effort=df["y"]), before=b, after=~b, resolution_metric=None, effort_metric="effort"
    )
    assert rows[0]["change"] == pytest.approx(-0.10)


def test_category_drift_flags_new_categories():
    labels = pd.Series(["A"] * 50 + ["B"] * 50 + ["A"] * 50 + ["C"] * 50)
    treated = pd.Series([True] * 200)
    post = pd.Series([False] * 100 + [True] * 100)
    rep = category_drift(labels, treated, post)
    assert rep.status == "fail" and rep.new_categories == ["C"] and rep.vanished_categories == ["B"]
