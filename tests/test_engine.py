import numpy as np
import pytest

from tests.conftest import make_panel
from truelift.engine.design import choose_design, evidence_grade
from truelift.engine.diagnostics import Check, leave_one_team_out, placebo_date_check, placebo_panel, pretrend_check
from truelift.engine.fe import demean_twoway, fit_twoway
from truelift.engine.imputation import EstimationError, TeamBootstrap, estimate, fit


def test_fit_twoway_recovers_additive_model():
    rng = np.random.default_rng(0)
    n_i, n_t = 15, 20
    i = np.repeat(np.arange(n_i), n_t)
    t = np.tile(np.arange(n_t), n_i)
    keep = rng.random(i.size) > 0.2  # unbalanced
    a, b = rng.normal(size=n_i), rng.normal(size=n_t)
    y = a[i] + b[t]
    fa, fb = fit_twoway(y[keep], i[keep], t[keep], np.ones(keep.sum()), n_i, n_t)
    assert np.allclose(fa[i] + fb[t], y, atol=1e-6)


def test_demean_matches_regression_residuals():
    rng = np.random.default_rng(1)
    n_i, n_t = 8, 10
    i = np.repeat(np.arange(n_i), n_t)
    t = np.tile(np.arange(n_t), n_i)
    x = rng.normal(size=i.size)
    dummies = np.column_stack([np.eye(n_i)[i], np.eye(n_t)[t][:, 1:]])
    beta, *_ = np.linalg.lstsq(dummies, x, rcond=None)
    expected = x - dummies @ beta
    got = demean_twoway(x, i, t, np.ones_like(x), n_i, n_t)[:, 0]
    assert np.allclose(got, expected, atol=1e-8)


def test_imputation_recovers_planted_effect(panel):
    f = fit(panel, pretrend_k=6)
    assert f.att == pytest.approx(-0.5, abs=0.03)
    assert set(f.pre) == {-1, -2, -3, -4, -5, -6}
    assert all(abs(v) < 0.2 for v in f.pre.values())


def test_imputation_zero_effect_interval_covers_zero():
    p = make_panel(effect=0.0, seed=4)
    est = estimate(p, TeamBootstrap(p.cohort_by_team, 149, 3))
    lo, hi = est.ci
    assert lo < 0 < hi


def test_dynamic_effects_not_biased_like_twfe():
    """Effects that grow over time bias ordinary TWFE; the imputation estimator stays on target."""
    p = make_panel(effect=0.0, seed=5, noise=0.05)
    e = p.event_time
    growth = np.where(p.treated, -0.05 * (e + 1), 0.0)
    p.y = p.y + growth
    f = fit(p)
    truth = float((growth[p.treated] * p.n[p.treated]).sum() / p.n[p.treated].sum())
    assert f.att == pytest.approx(truth, abs=0.01)


def test_requires_comparison():
    p = make_panel()
    p2 = p.with_cohorts(np.zeros(p.n_teams))  # everyone treated from period 0
    with pytest.raises(EstimationError):
        fit(p2)


def test_bootstrap_resample_keeps_strata(panel):
    boot = TeamBootstrap(panel.cohort_by_team, 5, 1)
    for draw in boot.draws:
        assert np.isnan(panel.cohort_by_team[draw]).sum() == np.isnan(panel.cohort_by_team).sum()


def test_pretrend_detects_anticipation():
    p = make_panel(effect=-0.5, seed=2, noise=0.1)
    e = p.event_time
    # AI teams start improving 4 weeks before their official rollout date.
    p.y = p.y + np.where((e >= -4) & (e < 0), -0.4, 0.0)
    est = estimate(p, TeamBootstrap(p.cohort_by_team, 149, 1), pretrend_k=6)
    assert pretrend_check(est).status == "fail"


def test_placebo_panel_only_uses_pre_data(panel):
    pp = placebo_panel(panel, shift=6)
    assert pp is not None
    with np.errstate(invalid="ignore"):
        assert not np.any(panel.cohort_by_team[pp.team] <= pp.period)
    assert np.nanmax(panel.cohort_by_team - pp.cohort_by_team) == 6


def test_placebo_and_loo_pass_on_clean_data(panel):
    boot = TeamBootstrap(panel.cohort_by_team, 99, 1)
    main = estimate(panel, boot)
    check, _ = placebo_date_check(panel, boot, 6, 0.95)
    assert check.status == "pass"
    loo = leave_one_team_out(panel, main, [f"t{i}" for i in range(panel.n_teams)])
    assert loo.status == "pass"


def test_design_selection():
    staggered = np.array([np.nan] * 5 + [10.0] * 5 + [14.0] * 5)
    assert choose_design(staggered, randomized=False).design == "staggered"
    two = np.array([np.nan] * 6 + [10.0] * 6)
    assert choose_design(two, randomized=False).design == "two_group"
    all_same = np.full(12, 10.0)
    choice = choose_design(all_same, randomized=False)
    assert choice.design == "none"
    assert "interrupted time series" in choice.reasons[-1]
    assert choose_design(np.array([np.nan, 3.0, 3.0]), randomized=False, min_teams=8).design == "none"


def test_grades():
    choice = choose_design(np.array([np.nan] * 10 + [8.0] * 10 + [12.0] * 10), randomized=True)
    ok = [Check("Pre-trend test", "pass", "")]
    assert evidence_grade(choice, ok, preregistered_before_rollout=True, registration_intact=True).grade == "A"
    assert evidence_grade(choice, ok, preregistered_before_rollout=False, registration_intact=True).grade == "B"
    bad = [Check("Placebo dates", "fail", "")]
    g = evidence_grade(choice, bad, preregistered_before_rollout=True, registration_intact=True)
    assert g.grade == "D" and not g.counted
    few = choose_design(np.array([np.nan] * 4 + [8.0] * 6), randomized=False)
    assert evidence_grade(few, ok, preregistered_before_rollout=False, registration_intact=False).grade == "C"
