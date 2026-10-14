from datetime import date

import numpy as np
import pandas as pd
import pytest

from truelift import schema as S
from truelift.config import CostLine, Range, ValueConfig
from truelift.guardrails import GuardrailSpec, evaluate_level, wilson
from truelift.rollout import NoiseModel, plan_waves, simulate_power
from truelift.value import period_costs, simulate_roi, verified_deflections


def test_range_coercion_and_validation():
    assert Range.model_validate(5) == Range(low=5, mid=5, high=5)
    assert Range.model_validate([1, 2, 3]).mid == 2
    with pytest.raises(ValueError):
        Range(low=3, mid=2, high=1)


def test_spec_illustration_capacity_and_cash_roi():
    """The spec's worked example: 1,460 hours at $60, $40,000 cost, half redeployed → 119% and 9.5%."""
    value = ValueConfig(loaded_hourly_cost=60, realization_rate=0.5, simulations=1000)
    costs = period_costs([CostLine(component="license", description="AI", amount=40000)], months=6)
    roi = simulate_roi(hours_draws=np.full(100, 1460.0), value=value, costs=costs, counted=True, seed=1)
    assert roi.capacity_value["p50"] == pytest.approx(87600)
    assert roi.roi_capacity["p50"] == pytest.approx(1.19)
    assert roi.roi_cash["p50"] == pytest.approx(0.095)


def test_cost_bases():
    lines = period_costs(
        [
            CostLine(component="license", description="a", amount=12000, basis="annual"),
            CostLine(component="human_oversight", description="b", amount=100, basis="monthly"),
            CostLine(component="build_integration", description="c", amount=500, basis="one_time"),
        ],
        months=6,
    ).lines
    assert [line["mid"] for line in lines] == [6000, 600, 500]


def test_payback_months():
    value = ValueConfig(loaded_hourly_cost=100, realization_rate=1.0, simulations=1000)
    costs = period_costs([CostLine(component="build_integration", description="x", amount=12000, basis="one_time")], 12)
    roi = simulate_roi(hours_draws=np.full(10, 240.0), value=value, costs=costs, counted=True, seed=1)
    assert roi.payback_cash_months["p50"] == pytest.approx(6.0)


def test_verified_deflection_matching():
    t0 = pd.Timestamp("2026-03-02 09:00")
    sessions = pd.DataFrame(
        {
            S.SESSION_ID: ["s1", "s2", "s3"],
            S.REQUESTER: ["u1", "u2", "u3"],
            S.SESSION_STARTED: [t0, t0, t0],
            S.SESSION_OUTCOME: ["resolved", "resolved", "abandoned"],
            S.WORK_TYPE: ["pw", "pw", "pw"],
        }
    )
    tickets = pd.DataFrame(
        {
            S.UNIT_ID: ["i1", "i2"],
            S.REQUESTER: ["u1", "u2"],
            S.WORK_TYPE: ["pw", "pw"],
            S.OPENED: [t0 + pd.Timedelta(days=2), t0 + pd.Timedelta(days=9)],
        }
    )
    d = verified_deflections(sessions, tickets, pd.Series({"pw": 0.5}))
    assert (d.resolved, d.verified, d.false) == (2, 1, 1)
    assert d.hours_avoided == pytest.approx(0.5)


def test_false_deflection_guardrail_levels():
    spec = GuardrailSpec("fd", "False deflection", "x", True, 0.15, level=True)
    assert evaluate_level(spec, 50, 1000).status == "pass"
    assert evaluate_level(spec, 300, 1000).status == "breach"
    assert evaluate_level(spec, None, None).status == "unavailable"
    lo, hi = wilson(10, 100)
    assert lo < 0.1 < hi


def test_wave_planner_is_balanced_reproducible_and_randomized():
    teams = pd.DataFrame({S.TEAM: [f"T{i}" for i in range(30)], "region": ["A", "B", "C"] * 10})
    a = plan_waves(
        teams,
        n_waves=3,
        first_start=date(2026, 11, 4),
        spacing_weeks=4,
        holdout_share=0.2,
        stratify_by="region",
        seed=42,
    )
    b = plan_waves(
        teams,
        n_waves=3,
        first_start=date(2026, 11, 4),
        spacing_weeks=4,
        holdout_share=0.2,
        stratify_by="region",
        seed=42,
    )
    assert a.equals(b)
    assert (a["kind"] == "holdout").sum() == 6
    assert set(a.loc[a["wave"] == 1, "start_date"]) == {"2026-11-02"}  # snapped to Monday
    sizes = a[a["kind"] == "wave"].groupby("wave").size()
    assert sizes.max() - sizes.min() <= 1
    assert a["randomized"].all()


def test_power_grows_with_teams():
    noise = NoiseModel(mean=1.0, sd_team=0.2, sd_week=0.05, sd_cell=0.15, rho=0.3)
    small = simulate_power(n_teams=10, n_waves=2, weeks_pre=12, weeks_post=8, effect=-0.05, noise=noise, sims=60)
    big = simulate_power(n_teams=40, n_waves=2, weeks_pre=12, weeks_post=8, effect=-0.05, noise=noise, sims=60)
    assert big.se < small.se and big.power > small.power
    assert abs(big.bias) < 0.02
