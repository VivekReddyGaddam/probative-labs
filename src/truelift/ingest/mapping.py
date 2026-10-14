"""Map source columns onto the canonical data model.

Only mapped columns survive: ingestion strips everything the analysis does not
need before anything is stored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from truelift import schema as S
from truelift.config import MappingEntry

PRESETS: dict[str, dict[str, str | dict]] = {
    "canonical": {c: c for c in S.WORK_UNIT_COLUMNS},
    # ServiceNow incident / request table exports (list view CSV or Table API JSON
    # flattened with sysparm_display_value=false).
    "servicenow": {
        S.UNIT_ID: "number",
        S.TEAM: "assignment_group",
        S.CATEGORY: "category",
        S.SUBCATEGORY: "subcategory",
        S.PRIORITY: "priority",
        S.CHANNEL: "contact_type",
        S.OPENED: "opened_at",
        S.RESOLVED: "resolved_at",
        S.CLOSED: "closed_at",
        S.EFFORT: {"source": "time_worked", "transform": "duration_hours"},
        S.REOPENS: "reopen_count",
        S.REASSIGNMENTS: "reassignment_count",
        S.ESCALATED: {"source": "escalation", "transform": "positive_bool"},
        S.REQUESTER: "caller_id",
        S.ASSIGNEE: "assigned_to",
        S.STATE: "state",
        S.DESCRIPTION: "short_description",
        # AI involvement and survey scores usually live in custom fields or joined
        # tables; map yours here.
        S.AI_TOUCHED: {"source": "u_ai_assisted", "transform": "bool"},
        S.CSAT: "u_csat",
    },
}

SESSION_PRESET: dict[str, str | dict] = {c: c for c in S.SESSION_COLUMNS}


class MappingError(ValueError):
    pass


@dataclass
class MappingReport:
    mapped: dict[str, str] = field(default_factory=dict)
    missing_optional: list[str] = field(default_factory=list)
    dropped_source_columns: list[str] = field(default_factory=list)
    coercion_failures: dict[str, int] = field(default_factory=dict)


def resolve_mapping(
    preset: str | Mapping[str, str | dict] | None, overrides: Mapping[str, str | MappingEntry | dict]
) -> dict[str, MappingEntry]:
    base: Mapping[str, str | dict]
    if preset is None:
        base = {}
    elif isinstance(preset, str):
        if preset not in PRESETS:
            raise MappingError(f"unknown mapping preset {preset!r}; choose from {sorted(PRESETS)}")
        base = PRESETS[preset]
    else:
        base = preset
    out: dict[str, MappingEntry] = {}
    for canon, entry in {**base, **overrides}.items():
        if isinstance(entry, MappingEntry):
            out[canon] = entry
        elif isinstance(entry, str):
            out[canon] = MappingEntry(source=entry)
        else:
            out[canon] = MappingEntry.model_validate(entry)
    return out


_TRUE = {"true", "t", "yes", "y", "1", "1.0"}
_FALSE = {"false", "f", "no", "n", "0", "0.0", ""}

_DURATION_RE = re.compile(
    r"(?:(?P<days>\d+(?:\.\d+)?)\s*days?)?\s*(?:(?P<hours>\d+(?:\.\d+)?)\s*hours?)?\s*"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)\s*minutes?)?\s*(?:(?P<seconds>\d+(?:\.\d+)?)\s*seconds?)?",
    re.IGNORECASE,
)


def _to_datetime(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s, errors="coerce", utc=True)
    if out.notna().sum() < s.notna().sum() * 0.9:
        out = pd.to_datetime(s, errors="coerce", utc=True, format="mixed")
    return out.dt.tz_localize(None)


def _to_bool(s: pd.Series) -> pd.Series:
    def conv(v: object) -> object:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return pd.NA
        t = str(v).strip().lower()
        if t in _TRUE:
            return True
        if t in _FALSE:
            return False
        return pd.NA

    return s.map(conv).astype("boolean")


def _duration_hours(s: pd.Series) -> pd.Series:
    """Parse durations: seconds as numbers, ``HH:MM:SS`` or ``1 Day 2 Hours``."""

    def conv(v: object) -> float:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        t = str(v).strip()
        if not t:
            return np.nan
        try:
            return float(t) / 3600.0
        except ValueError:
            pass
        if t.startswith("1970-01-"):  # ServiceNow API duration values: offsets from the epoch
            try:
                return (pd.Timestamp(t) - pd.Timestamp("1970-01-01")).total_seconds() / 3600
            except ValueError:
                return np.nan
        if ":" in t:
            parts = t.split(":")
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                return np.nan
            while len(nums) < 3:
                nums.insert(0, 0.0)
            h, m, sec = nums[-3:]
            return h + m / 60 + sec / 3600
        m = _DURATION_RE.fullmatch(t)
        if not m or not any(m.groupdict().values()):
            return np.nan
        g = {k: float(v) if v else 0.0 for k, v in m.groupdict().items()}
        return g["days"] * 24 + g["hours"] + g["minutes"] / 60 + g["seconds"] / 3600

    return s.map(conv).astype(float)


def _apply_transform(s: pd.Series, transform: str | None, kind: str) -> pd.Series:
    transform = transform or {"datetime": "datetime", "number": "number", "bool": "bool"}.get(kind, "string")
    if transform == "datetime":
        return _to_datetime(s)
    if transform == "number":
        return pd.to_numeric(s, errors="coerce").astype(float)
    if transform == "bool":
        return _to_bool(s)
    if transform == "positive_bool":
        return (pd.to_numeric(s, errors="coerce") > 0).astype("boolean").where(s.notna())
    if transform == "duration_hours":
        return _duration_hours(s)
    if transform == "seconds_to_hours":
        return pd.to_numeric(s, errors="coerce") / 3600.0
    if transform == "minutes_to_hours":
        return pd.to_numeric(s, errors="coerce") / 60.0
    if transform == "string":
        return s.astype("string").str.strip()
    raise MappingError(f"unknown transform {transform!r}")


def apply_mapping(
    df: pd.DataFrame,
    mapping: Mapping[str, MappingEntry],
    columns: Mapping[str, str],
    required: tuple[str, ...] = (),
) -> tuple[pd.DataFrame, MappingReport]:
    """Return a frame with only canonical columns, typed according to ``columns``."""
    report = MappingReport()
    missing_required = [
        f"{c} (source column {mapping[c].source!r})" if c in mapping else f"{c} (not mapped)"
        for c in required
        if c not in mapping or mapping[c].source not in df.columns
    ]
    if missing_required:
        raise MappingError("required fields missing: " + ", ".join(missing_required))

    out = pd.DataFrame(index=df.index)
    used_sources: set[str] = set()
    for canon, kind in columns.items():
        entry = mapping.get(canon)
        if entry is None or entry.source not in df.columns:
            report.missing_optional.append(canon)
            continue
        src = df[entry.source]
        converted = _apply_transform(src, entry.transform, kind)
        failures = int((src.notna() & converted.isna()).sum())
        if failures:
            report.coercion_failures[canon] = failures
        out[canon] = converted
        report.mapped[canon] = entry.source
        used_sources.add(entry.source)
    report.dropped_source_columns = [c for c in df.columns if c not in used_sources]
    return out.reset_index(drop=True), report
