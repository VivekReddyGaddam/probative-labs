"""ROI ranges by simulation.

ROI = (value − total cost) / total cost, always reported as a range:

* the measured effect enters through its team-bootstrap draws;
* each assumption (loaded hourly cost, realization rate, uncertain cost lines)
  enters as a triangular distribution set by the customer;
* the simulation reports the 10th, 50th and 90th percentiles.

Two versions are reported. *Capacity* ROI counts all freed hours. *Cash* ROI
counts only value that provably turned into money: documented realized savings
plus the share of capacity the customer can show was redeployed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from probative.config import CostLine, Range, ValueConfig

DAYS_PER_MONTH = 30.4375


@dataclass
class CostBreakdown:
    lines: list[dict]
    months: float

    def to_dict(self) -> dict:
        return {"months": self.months, "lines": self.lines}


def _factor(basis: str, months: float) -> float:
    return {"period": 1.0, "one_time": 1.0, "annual": months / 12.0, "monthly": months}[basis]


def period_costs(costs: list[CostLine], months: float) -> CostBreakdown:
    lines = []
    for c in costs:
        f = _factor(c.basis, months)
        lines.append(
            {
                "component": c.component,
                "description": c.description,
                "basis": c.basis,
                "source": c.source,
                "low": c.amount.low * f,
                "mid": c.amount.mid * f,
                "high": c.amount.high * f,
                "one_time": c.basis == "one_time",
            }
        )
    return CostBreakdown(lines, months)


def _pct(x: np.ndarray) -> dict[str, float]:
    x = x[np.isfinite(x)]
    if not x.size:
        return {"p10": float("nan"), "p50": float("nan"), "p90": float("nan")}
    p10, p50, p90 = np.percentile(x, [10, 50, 90])
    return {"p10": float(p10), "p50": float(p50), "p90": float(p90)}


def _payback(upfront: np.ndarray, monthly_net: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        pb = np.where(monthly_net > 0, upfront / monthly_net, np.inf)
    return np.where((upfront <= 0) & (monthly_net > 0), 0.0, pb)


def _payback_summary(pb: np.ndarray) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name, q in (("p10", 10), ("p50", 50), ("p90", 90)):
        v = float(np.percentile(pb, q, method="lower"))
        out[name] = v if np.isfinite(v) else None
    return out


@dataclass
class RoiResult:
    currency: str
    months: float
    hours: dict[str, float]
    capacity_value: dict[str, float]
    cash_value: dict[str, float]
    cost: dict[str, float]
    roi_capacity: dict[str, float]
    roi_cash: dict[str, float]
    payback_capacity_months: dict[str, float | None]
    payback_cash_months: dict[str, float | None]
    cost_per_hour: dict[str, float]
    realized_savings: float
    deflection_hours: float
    simulations: int
    counted: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def simulate_roi(
    *,
    hours_draws: np.ndarray,
    value: ValueConfig,
    costs: CostBreakdown,
    deflection_hours: float = 0.0,
    false_deflections: int = 0,
    counted: bool,
    seed: int,
) -> RoiResult:
    """Simulate value and ROI. ``hours_draws`` are bootstrap draws of agent hours saved."""
    rng = np.random.default_rng(seed)
    n = value.simulations
    draws = hours_draws[np.isfinite(hours_draws)]
    if not draws.size:
        raise ValueError("no usable effect draws for the ROI simulation")
    hours = rng.choice(draws, size=n, replace=True)
    false_cost = value.false_deflection_cost_hours.sample(rng, n) * false_deflections
    total_hours = hours + deflection_hours - false_cost

    rate = value.loaded_hourly_cost.sample(rng, n)
    realization = value.realization_rate.sample(rng, n)
    realized = float(sum(s.amount for s in value.realized_savings))

    capacity = total_hours * rate
    cash = capacity * realization + realized

    upfront = np.zeros(n)
    total_cost = np.zeros(n)
    for line in costs.lines:
        amt = Range(low=line["low"], mid=line["mid"], high=line["high"]).sample(rng, n)
        total_cost += amt
        if line["one_time"]:
            upfront += amt

    notes: list[str] = []
    months = max(costs.months, 1e-9)
    if not costs.lines:
        notes.append("No costs were entered, so ROI cannot be computed; value is shown alone.")
        roi_cap = roi_cash = np.full(n, np.nan)
    else:
        with np.errstate(divide="ignore", invalid="ignore"):
            roi_cap = (capacity - total_cost) / total_cost
            roi_cash = (cash - total_cost) / total_cost
    recurring_monthly = (total_cost - upfront) / months
    pb_cap = _payback(upfront, capacity / months - recurring_monthly)
    pb_cash = _payback(upfront, cash / months - recurring_monthly)
    with np.errstate(divide="ignore", invalid="ignore"):
        cph = np.where(total_hours > 0, total_cost / total_hours, np.nan)
    if value.realization_rate.high == 0 and not value.realized_savings:
        notes.append(
            "No realization evidence or realization rate was entered, so cash value is zero: "
            "freed hours have not been shown to turn into money."
        )

    return RoiResult(
        currency=value.currency,
        months=costs.months,
        hours=_pct(total_hours),
        capacity_value=_pct(capacity),
        cash_value=_pct(cash),
        cost=_pct(total_cost),
        roi_capacity=_pct(roi_cap),
        roi_cash=_pct(roi_cash),
        payback_capacity_months=_payback_summary(pb_cap),
        payback_cash_months=_payback_summary(pb_cash),
        cost_per_hour=_pct(cph),
        realized_savings=realized,
        deflection_hours=deflection_hours,
        simulations=n,
        counted=counted,
        notes=notes,
    )
