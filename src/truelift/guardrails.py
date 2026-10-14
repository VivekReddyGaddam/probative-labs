"""Quality guardrails. Speed that comes with worse quality does not count as value.

Each guardrail is estimated with the same design as the primary effect. Status:

* ``pass``   — confident the harm is below the threshold (upper bound of harm < threshold)
* ``breach`` — harm above the threshold and distinguishable from zero
* ``watch``  — inconclusive: the range includes the threshold
* ``unavailable`` — the metric is not in the data

Any breach blocks the program's value ("faster-but-worse block").
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from truelift.config import GuardrailConfig
from truelift.engine.imputation import Estimate

Status = Literal["pass", "watch", "breach", "unavailable"]


@dataclass(frozen=True)
class GuardrailSpec:
    key: str
    label: str
    column: str
    higher_is_worse: bool
    threshold: float
    kind: Literal["absolute", "relative"] = "absolute"
    unit: str = ""
    level: bool = False  # judged on its level, not on a causal change (e.g. false deflection rate)

    def with_overrides(self, cfg: GuardrailConfig | None) -> GuardrailSpec | None:
        if cfg is None:
            return self
        if not cfg.enabled:
            return None
        return GuardrailSpec(
            key=self.key,
            label=self.label,
            column=self.column,
            higher_is_worse=self.higher_is_worse,
            threshold=cfg.threshold if cfg.threshold is not None else self.threshold,
            kind=cfg.kind or self.kind,
            unit=self.unit,
            level=self.level,
        )

    def describe_threshold(self) -> str:
        direction = "rise" if self.higher_is_worse else "fall"
        if self.level:
            return f"at most {self.threshold:.0%}"
        if self.kind == "relative":
            return f"{direction} of at most {self.threshold:.0%}"
        return f"{direction} of at most {self.threshold:g}{self.unit}"


@dataclass
class GuardrailResult:
    spec: GuardrailSpec
    status: Status
    estimate: float = float("nan")
    ci_low: float = float("nan")
    ci_high: float = float("nan")
    threshold_abs: float = float("nan")
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.spec.key,
            "label": self.spec.label,
            "status": self.status,
            "estimate": self.estimate,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "threshold": self.spec.describe_threshold(),
            "threshold_abs": self.threshold_abs,
            "detail": self.detail,
            **self.extra,
        }


def evaluate_change(spec: GuardrailSpec, est: Estimate | None) -> GuardrailResult:
    if est is None:
        return GuardrailResult(spec, "unavailable", detail="Not in the data.")
    lo, hi = est.ci
    if np.isnan(lo):
        return GuardrailResult(spec, "watch", est.att, lo, hi, detail="Too few observations to judge.")
    sign = 1.0 if spec.higher_is_worse else -1.0
    harm, harm_lo, harm_hi = sign * est.att, min(sign * lo, sign * hi), max(sign * lo, sign * hi)
    thr = spec.threshold * abs(est.point.counterfactual_mean) if spec.kind == "relative" else spec.threshold
    if harm_hi < thr:
        status: Status = "pass"
        detail = f"Change {est.att:+.3g}{spec.unit} (range {lo:+.3g} to {hi:+.3g}); within the limit."
    elif harm > thr and harm_lo > 0:
        status = "breach"
        detail = (
            f"Worsened by {abs(est.att):.3g}{spec.unit} (range {lo:+.3g} to {hi:+.3g}), "
            f"beyond the allowed {thr:.3g}{spec.unit}."
        )
    else:
        status = "watch"
        detail = f"Change {est.att:+.3g}{spec.unit} (range {lo:+.3g} to {hi:+.3g}); cannot rule out a breach."
    return GuardrailResult(spec, status, est.att, lo, hi, thr, detail)


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def evaluate_level(spec: GuardrailSpec, successes: int | None, n: int | None) -> GuardrailResult:
    if not n:
        return GuardrailResult(spec, "unavailable", detail="No self-service sessions in the data.")
    rate = successes / n
    lo, hi = wilson(successes, n)
    if hi < spec.threshold:
        status: Status = "pass"
    elif lo > spec.threshold:
        status = "breach"
    else:
        status = "watch"
    detail = f"{rate:.1%} of resolved sessions came back as a ticket within 7 days (range {lo:.1%} to {hi:.1%})."
    return GuardrailResult(spec, status, rate, lo, hi, spec.threshold, detail, {"n": n})


def blocked(results: list[GuardrailResult]) -> bool:
    return any(r.status == "breach" for r in results)
