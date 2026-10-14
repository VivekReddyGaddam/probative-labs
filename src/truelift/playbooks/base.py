from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from truelift.guardrails import GuardrailSpec


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    higher_is_better: bool = False
    fmt: str = ".2f"


@dataclass(frozen=True)
class Playbook:
    name: str
    title: str
    unit_noun: str
    unit_noun_plural: str
    metrics: dict[str, Metric]
    guardrails: tuple[GuardrailSpec, ...]
    placebo_metrics: dict[str, Metric]
    derive: Callable[[pd.DataFrame], pd.DataFrame]
    traps: tuple[str, ...] = field(default_factory=tuple)
