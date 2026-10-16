"""Project configuration: one ``probative.yaml`` per AI program being measured."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


class Range(BaseModel):
    """An assumption with uncertainty: low, most likely and high values.

    Accepts a plain number (no uncertainty), a ``[low, mid, high]`` list or a mapping.
    Sampled as a triangular distribution in the ROI simulation.
    """

    model_config = ConfigDict(frozen=True)

    low: float
    mid: float
    high: float

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> Any:
        if isinstance(v, (int, float)):
            return {"low": v, "mid": v, "high": v}
        if isinstance(v, (list, tuple)) and len(v) == 3:
            return dict(zip(("low", "mid", "high"), v, strict=True))
        return v

    @model_validator(mode="after")
    def _ordered(self) -> Range:
        if not self.low <= self.mid <= self.high:
            raise ValueError(f"range must satisfy low <= mid <= high, got {self.low}, {self.mid}, {self.high}")
        return self

    @property
    def is_point(self) -> bool:
        return self.low == self.high

    def sample(self, rng: np.random.Generator, size: int) -> np.ndarray:
        if self.is_point:
            return np.full(size, self.mid, dtype=float)
        return rng.triangular(self.low, self.mid, self.high, size)


class MappingEntry(BaseModel):
    """A source column (or several candidate names, first match wins) and an optional transform."""

    source: str | list[str]
    transform: str | None = None

    @property
    def candidates(self) -> list[str]:
        return [self.source] if isinstance(self.source, str) else list(self.source)

    @property
    def label(self) -> str:
        return " or ".join(repr(c) for c in self.candidates)


class ProgramConfig(BaseModel):
    id: str
    name: str
    vendor: str | None = None
    tool: str | None = None
    models: list[str] = Field(default_factory=list)
    function: str = "it_service_management"
    owner: str | None = None
    contract_ref: str | None = None


class HypothesisConfig(BaseModel):
    statement: str
    primary_metric: Literal["effort_hours", "resolution_hours"] = "effort_hours"
    expected_effect: float | None = Field(
        None, description="Expected relative change in the primary metric, e.g. -0.15 for 15% less."
    )
    by: date | None = None


class DataConfig(BaseModel):
    work_units: list[Path]
    mapping_preset: Literal[
        "canonical", "servicenow", "jira_service_management", "zendesk", "freshservice", "bmc_helix", "ivanti"
    ] = "canonical"
    mapping: dict[str, str | MappingEntry] = Field(default_factory=dict)
    exposure: Path
    self_service: Path | None = None
    self_service_mapping: dict[str, str | MappingEntry] = Field(default_factory=dict)
    teams: Path | None = None
    staffing: Path | None = None
    pseudonymized: bool = Field(
        False, description="Identifiers were already pseudonymized upstream (e.g. by a connector); skip hashing."
    )

    @field_validator("work_units", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, (str, Path)) else v


class TypeRule(BaseModel):
    """First matching rule wins. Values are exact matches; prefix with ``re:`` for a regex."""

    name: str
    when: dict[str, str | list[str]]


class TypingConfig(BaseModel):
    fields: list[str] = Field(default_factory=lambda: ["category", "subcategory"])
    rules: list[TypeRule] = Field(default_factory=list)
    min_share: float = Field(0.01, ge=0, lt=0.5)
    other_label: str = "Other"


class AnalysisConfig(BaseModel):
    start: date | None = None
    end: date | None = None
    design: Literal["auto", "staggered", "two_group"] = "auto"
    randomized: bool | None = Field(
        None, description="Override; by default read from the exposure file's 'randomized' column."
    )
    bootstrap_reps: int = Field(499, ge=49)
    seed: int = 20261014
    confidence: float = Field(0.95, gt=0.5, lt=1)
    min_group_size: int = Field(5, ge=1)
    min_teams: int = Field(8, ge=2)
    pretrend_weeks: int = Field(8, ge=1)
    max_event_weeks: int | None = None
    placebo_shift_weeks: int | None = None
    placebo_outcomes: list[str] = Field(default_factory=lambda: ["after_hours"])
    censoring_threshold: float = Field(0.05, gt=0, lt=1)
    min_type_units: int = Field(200, ge=10)
    include_teams: list[str] | None = Field(
        None, description="Analyse only these teams (used by bake-offs to compare one vendor's teams with control)."
    )


class GuardrailConfig(BaseModel):
    enabled: bool = True
    threshold: float | None = None
    kind: Literal["absolute", "relative"] | None = None


class RealizedSaving(BaseModel):
    description: str
    amount: float
    evidence: str


class ValueConfig(BaseModel):
    currency: str = "USD"
    loaded_hourly_cost: Range
    realization_rate: Range = Field(default_factory=lambda: Range(low=0, mid=0, high=0))
    false_deflection_cost_hours: Range = Field(default_factory=lambda: Range(low=0.25, mid=0.5, high=1.0))
    realized_savings: list[RealizedSaving] = Field(default_factory=list)
    simulations: int = Field(10000, ge=1000)
    owner: str | None = Field(None, description="Who agreed these unit values, e.g. 'Finance partner, IT'.")
    agreed_on: date | None = None

    @field_validator("realization_rate")
    @classmethod
    def _share(cls, v: Range) -> Range:
        if v.low < 0 or v.high > 1:
            raise ValueError("realization_rate must be between 0 and 1")
        return v


CostComponent = Literal[
    "license",
    "model_usage",
    "infrastructure",
    "build_integration",
    "change_training",
    "run_govern",
    "human_oversight",
    "quality",
    "other",
]


class CostLine(BaseModel):
    component: CostComponent
    description: str
    amount: Range
    basis: Literal["period", "annual", "monthly", "one_time"] = "period"
    source: str | None = None


class ClaimedValue(BaseModel):
    source: str
    hours_saved: float | None = None
    value: float | None = None
    note: str | None = None


class AuditConfig(BaseModel):
    """Resolution audit settings for ``probative audit``."""

    window_days: int = Field(7, ge=1, le=60, description="A return within this many days counts against a claim.")
    match: Literal["same_type", "any"] = "same_type"
    price_per_resolution: float | None = Field(
        None, ge=0, description="Price paid per claimed resolution, if the AI is billed per outcome."
    )
    resolved_value: str = Field("resolved", description="Outcome value that marks a claimed resolution.")
    contractual: bool = Field(
        False,
        description="The contract defines a resolution this way, so each claim that came back is disputable "
        "(see docs/outcome-terms.md). Otherwise only billing errors are, and the excess is a negotiation figure.",
    )
    definition_name: str = Field("Verified resolution", description="Name of the outcome definition in the contract.")
    ai_resolvers: list[str] = Field(
        default_factory=list,
        description="For `probative closures`: the 'resolved by' values that mean the AI closed the ticket itself.",
    )
    resolver_column: str | None = Field(None, description="Ticket column saying who resolved it (auto-detected).")
    ai_flag_column: str | None = Field(None, description="Or a yes/no ticket column marking tickets the AI closed.")


class BakeoffVendor(BaseModel):
    name: str
    annual_price: Range | None = Field(None, description="Quoted price per year, for value for money.")
    claims: Path | None = Field(None, description="This vendor's conversations, for a resolution audit per vendor.")


class BakeoffConfig(BaseModel):
    """A randomized head-to-head trial of AI vendors on the buyer's own work (``probative bakeoff``)."""

    assignment: Path = Field(Path("bakeoff.csv"), description="Team-to-arm assignment from `probative bakeoff plan`.")
    start: date
    end: date
    vendors: list[BakeoffVendor]


class ProjectConfig(BaseModel):
    """Everything needed to measure one AI program."""

    program: ProgramConfig
    hypothesis: HypothesisConfig
    playbook: Literal["service_desk"] = "service_desk"
    data: DataConfig
    typing: TypingConfig = Field(default_factory=TypingConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    guardrails: dict[str, GuardrailConfig] = Field(default_factory=dict)
    value: ValueConfig
    costs: list[CostLine] = Field(default_factory=list)
    claimed: list[ClaimedValue] = Field(default_factory=list)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    bakeoff: BakeoffConfig | None = None
    registration: Path = Path("registration.yaml")

    _base_dir: Path = PrivateAttr(default=Path("."))
    _source_path: Path | None = PrivateAttr(default=None)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else (self._base_dir / p)

    @classmethod
    def load(cls, path: str | Path) -> ProjectConfig:
        path = Path(path)
        with path.open() as fh:
            raw = yaml.safe_load(fh) or {}
        cfg = cls.model_validate(raw)
        cfg._base_dir = path.resolve().parent
        cfg._source_path = path.resolve()
        return cfg

    @classmethod
    def from_dict(cls, raw: dict[str, Any], base_dir: str | Path = ".") -> ProjectConfig:
        cfg = cls.model_validate(raw)
        cfg._base_dir = Path(base_dir).resolve()
        return cfg
