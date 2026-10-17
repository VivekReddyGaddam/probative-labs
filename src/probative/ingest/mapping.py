"""Map source columns onto the canonical data model.

Only mapped columns survive: ingestion strips everything the analysis does not
need before anything is stored.

Each preset maps canonical fields to the column names a platform's export uses.
Where export labels vary between versions or configurations, a field lists
several candidates; the first one present wins. Matching ignores case and
surrounding spaces.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from probative import schema as S
from probative.config import MappingEntry

PresetEntry = str | list[str] | dict

PRESETS: dict[str, dict[str, PresetEntry]] = {
    "canonical": {c: c for c in S.WORK_UNIT_COLUMNS},
    # ServiceNow incident / request table exports (list view CSV or Table API).
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
    # Jira Service Management: "Export > CSV (all fields)" from an issue search.
    # Custom fields export as "Custom field (Name)". Jira has no assignment group,
    # so the team comes from a Team field or, failing that, the first component.
    "jira_service_management": {
        S.UNIT_ID: ["Issue key"],
        S.TEAM: ["Custom field (Team)", "Team", "Component/s", "Components"],
        S.CATEGORY: [
            "Custom field (Request Type)",
            "Request Type",
            "Custom field (Customer Request Type)",
            "Issue Type",
        ],
        S.SUBCATEGORY: ["Issue Type"],
        S.PRIORITY: ["Priority"],
        S.OPENED: ["Created"],
        S.RESOLVED: ["Resolved"],
        S.EFFORT: {"source": ["Time Spent", "Σ Time Spent"], "transform": "seconds_to_hours"},
        S.REQUESTER: ["Reporter"],
        S.ASSIGNEE: ["Assignee"],
        S.STATE: ["Status"],
        S.CSAT: ["Custom field (Satisfaction)", "Satisfaction", "Custom field (Satisfaction rating)"],
        S.DESCRIPTION: ["Summary"],
    },
    # Zendesk ticket CSV export (Admin Center > Account > Tools > Reports > Export).
    # Zendesk does not record agent time by default, so measure resolution time or
    # add a staffing file.
    "zendesk": {
        S.UNIT_ID: ["ID", "Id"],
        S.TEAM: ["Group"],
        S.CATEGORY: ["Ticket type", "Type"],
        S.PRIORITY: ["Priority"],
        S.CHANNEL: ["Via"],
        S.OPENED: ["Created at"],
        S.RESOLVED: ["Solved at"],
        S.REOPENS: ["Reopens"],
        S.REASSIGNMENTS: {"source": ["Group stations"], "transform": "minus_one"},
        S.CSAT: {"source": ["Satisfaction Score"], "transform": "satisfaction_good_bad"},
        S.REQUESTER: ["Requester ID", "Requester email"],
        S.ASSIGNEE: ["Assignee"],
        S.STATE: ["Status"],
        S.DESCRIPTION: ["Subject"],
    },
    # Freshservice ticket export (Tickets list > Export, CSV).
    "freshservice": {
        S.UNIT_ID: ["Ticket Id", "Ticket ID", "ID"],
        S.TEAM: ["Group"],
        S.CATEGORY: ["Category"],
        S.SUBCATEGORY: ["Sub-Category", "Sub Category", "Subcategory"],
        S.PRIORITY: ["Priority"],
        S.CHANNEL: ["Source"],
        S.OPENED: ["Created Date", "Created Time", "Created at"],
        S.RESOLVED: ["Resolved Date", "Resolved Time", "Resolved at"],
        S.CLOSED: ["Closed Date", "Closed Time", "Closed at"],
        S.EFFORT: {"source": ["Time Tracked"], "transform": "duration_hours"},
        S.REQUESTER: ["Requester Email", "Email", "Requester Name"],
        S.ASSIGNEE: ["Agent", "Responder"],
        S.STATE: ["Status"],
        S.DESCRIPTION: ["Subject"],
    },
    # BMC Helix ITSM incidents (HPD:Help Desk field names, or report exports whose
    # labels add "*" and "+" markers).
    "bmc_helix": {
        S.UNIT_ID: ["Incident Number", "Incident ID", "Incident ID*+"],
        S.TEAM: ["Assigned Group", "Assigned Group*+"],
        S.CATEGORY: [
            "Operational Categorization Tier 1",
            "Categorization Tier 1",
            "Operational Categorization Tier 1+",
        ],
        S.SUBCATEGORY: [
            "Operational Categorization Tier 2",
            "Categorization Tier 2",
            "Operational Categorization Tier 2+",
        ],
        S.PRIORITY: ["Priority", "Priority*"],
        S.CHANNEL: ["Reported Source", "Reported Source+"],
        S.OPENED: ["Reported Date", "Submit Date", "Reported Date+"],
        S.RESOLVED: ["Last Resolved Date", "Resolved Date"],
        S.CLOSED: ["Closed Date"],
        S.REQUESTER: ["Person ID", "Customer Login ID", "Direct Contact Login ID", "Customer"],
        S.ASSIGNEE: ["Assignee Login ID", "Assignee", "Assignee+"],
        S.STATE: ["Status", "Status*"],
        S.DESCRIPTION: ["Description", "Summary", "Summary*"],
    },
    # Ivanti Neurons for ITSM incidents (Incident business object field names, or
    # grid exports with spaced labels).
    "ivanti": {
        S.UNIT_ID: ["IncidentNumber", "Incident Number", "Incident ID", "Incident #"],
        S.TEAM: ["OwnerTeam", "Owner Team", "Team"],
        S.CATEGORY: ["Category", "ActualCategory"],
        S.SUBCATEGORY: ["SubCategory", "Sub Category"],
        S.PRIORITY: ["Priority"],
        S.CHANNEL: ["Source"],
        S.OPENED: ["CreatedDateTime", "Created Date Time", "Created On", "Created Date"],
        S.RESOLVED: ["ResolvedDateTime", "Resolved Date Time", "Resolved On", "Resolved Date"],
        S.CLOSED: ["ClosedDateTime", "Closed Date Time", "Closed On", "Closed Date"],
        S.REQUESTER: ["ProfileLink_RecID", "ProfileLink", "ProfileFullName", "Customer"],
        S.ASSIGNEE: ["Owner"],
        S.STATE: ["Status"],
        S.DESCRIPTION: ["Subject", "Symptom"],
    },
}

# How each preset's column names were checked. "documented" presets match the vendor's
# published export or field reference; "typical" presets follow common exports and list
# variants, so check your own file if a required column is reported missing.
PRESET_INFO: dict[str, dict[str, str]] = {
    "canonical": {"label": "Probative Labs column names", "basis": "documented"},
    "servicenow": {"label": "ServiceNow (incident or request export)", "basis": "documented"},
    "jira_service_management": {"label": "Jira Service Management (CSV, all fields)", "basis": "typical"},
    "zendesk": {"label": "Zendesk (ticket CSV export)", "basis": "documented"},
    "freshservice": {"label": "Freshservice (ticket export)", "basis": "typical"},
    "bmc_helix": {"label": "BMC Helix ITSM (HPD:Help Desk incidents)", "basis": "typical"},
    "ivanti": {"label": "Ivanti Neurons for ITSM (incidents)", "basis": "documented"},
}

SESSION_PRESET: dict[str, PresetEntry] = {c: c for c in S.SESSION_COLUMNS}


class MappingError(ValueError):
    pass


@dataclass
class MappingReport:
    mapped: dict[str, str] = field(default_factory=dict)
    missing_optional: list[str] = field(default_factory=list)
    dropped_source_columns: list[str] = field(default_factory=list)
    coercion_failures: dict[str, int] = field(default_factory=dict)


def _entry(entry: PresetEntry | MappingEntry) -> MappingEntry:
    if isinstance(entry, MappingEntry):
        return entry
    if isinstance(entry, (str, list)):
        return MappingEntry(source=entry)
    return MappingEntry.model_validate(entry)


def resolve_mapping(
    preset: str | Mapping[str, PresetEntry] | None, overrides: Mapping[str, PresetEntry | MappingEntry]
) -> dict[str, MappingEntry]:
    base: Mapping[str, PresetEntry]
    if preset is None:
        base = {}
    elif isinstance(preset, str):
        if preset not in PRESETS:
            raise MappingError(f"unknown mapping preset {preset!r}; choose from {sorted(PRESETS)}")
        base = PRESETS[preset]
    else:
        base = preset
    return {canon: _entry(entry) for canon, entry in {**base, **overrides}.items()}


def _norm(name: object) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def find_column(columns: Iterable[object], candidates: list[str]) -> str | None:
    """Return the first candidate present in ``columns`` (case- and space-insensitive)."""
    lookup: dict[str, str] = {}
    for c in columns:
        lookup.setdefault(_norm(c), str(c))
    for cand in candidates:
        hit = lookup.get(_norm(cand))
        if hit is not None:
            return hit
    return None


_TRUE = {"true", "t", "yes", "y", "1", "1.0"}
_FALSE = {"false", "f", "no", "n", "0", "0.0", ""}

_DURATION_RE = re.compile(
    r"(?:(?P<days>\d+(?:\.\d+)?)\s*days?)?\s*(?:(?P<hours>\d+(?:\.\d+)?)\s*hours?)?\s*"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)\s*minutes?)?\s*(?:(?P<seconds>\d+(?:\.\d+)?)\s*seconds?)?",
    re.IGNORECASE,
)

# Tried in order when the default parser leaves many values unparsed. The first is
# Jira's export format, e.g. "14/Oct/26 9:14 AM".
_DATE_FORMATS = ("%d/%b/%y %I:%M %p", "%d/%b/%Y %I:%M %p", "%d/%b/%y %H:%M", "mixed")


_NUMERIC_DATE = re.compile(r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}\b")


def _day_first(s: pd.Series) -> bool | None:
    """Whether numeric dates such as 5/3/2016 put the day first, judged from the unambiguous ones.

    A first number above 12 can only be a day, a second number above 12 only a day too. Exports
    follow the user's date preference, so one file is consistent: the first clear case decides.
    """
    parts = s.dropna().astype(str).str.extract(_NUMERIC_DATE).dropna().astype(int)
    if parts.empty:
        return None
    day_first, month_first = int((parts[0] > 12).sum()), int((parts[1] > 12).sum())
    if day_first and not month_first:
        return True
    if month_first and not day_first:
        return False
    return None


def _to_datetime(s: pd.Series, day_first: bool | None = None) -> pd.Series:
    """Parse dates. ``day_first`` is the file's date order when known; otherwise this column decides."""
    present = s.notna().sum()
    order = day_first if day_first is not None else _day_first(s)
    with warnings.catch_warnings():  # unknown formats are handled by the fallbacks below
        warnings.simplefilter("ignore", UserWarning)
        if order is None:
            best = pd.to_datetime(s, errors="coerce", utc=True)
        else:  # dates like 5/3/2016: parse each value with the order the file's clear cases show
            best = pd.to_datetime(s, errors="coerce", utc=True, dayfirst=order, format="mixed")
    for fmt in _DATE_FORMATS:
        if best.notna().sum() >= present * 0.9:
            break
        trial = pd.to_datetime(s, errors="coerce", utc=True, format=fmt)
        if trial.notna().sum() > best.notna().sum():
            best = trial
    return best.dt.tz_localize(None)


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
    """Parse durations: seconds as numbers, ``HH:MM:SS``, ``HH:MM`` or ``1 Day 2 Hours``."""

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
            try:
                nums = [float(p) for p in t.split(":")]
            except ValueError:
                return np.nan
            if len(nums) == 2:  # HH:MM, e.g. Freshservice time tracked
                return nums[0] + nums[1] / 60
            h, m, sec = ([0.0] * 3 + nums)[-3:]
            return h + m / 60 + sec / 3600
        m = _DURATION_RE.fullmatch(t)
        if not m or not any(m.groupdict().values()):
            return np.nan
        g = {k: float(v) if v else 0.0 for k, v in m.groupdict().items()}
        return g["days"] * 24 + g["hours"] + g["minutes"] / 60 + g["seconds"] / 3600

    return s.map(conv).astype(float)


def _satisfaction_good_bad(s: pd.Series) -> pd.Series:
    """Zendesk-style ratings: good → 1, bad → 0; offered or unoffered → missing."""
    t = s.astype("string").str.strip().str.lower()
    return t.map({"good": 1.0, "bad": 0.0}).astype(float)


def _apply_transform(s: pd.Series, transform: str | None, kind: str, day_first: bool | None = None) -> pd.Series:
    transform = transform or {"datetime": "datetime", "number": "number", "bool": "bool"}.get(kind, "string")
    if transform == "datetime":
        return _to_datetime(s, day_first)
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
    if transform == "minus_one":  # e.g. "group stations" (groups visited) → reassignments
        return (pd.to_numeric(s, errors="coerce") - 1).clip(lower=0)
    if transform == "satisfaction_good_bad":
        return _satisfaction_good_bad(s)
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
    found = {canon: find_column(df.columns, entry.candidates) for canon, entry in mapping.items()}
    missing_required = [
        f"{c} (looked for {mapping[c].label})" if c in mapping else f"{c} (not mapped)"
        for c in required
        if found.get(c) is None
    ]
    if missing_required:
        raise MappingError(
            "required fields missing: "
            + ", ".join(missing_required)
            + ". Map them to your column names in the mapping settings."
        )

    out = pd.DataFrame(index=df.index)
    used_sources: set[str] = set()
    # One export uses one date order: judge it from every date column together, so a column whose dates
    # are all ambiguous (5/3/2016) follows the file's clear cases (29/2/2016).
    date_sources = [found[c] for c, kind in columns.items() if kind == "datetime" and found.get(c) is not None]
    day_first = _day_first(pd.concat([df[c] for c in date_sources], ignore_index=True)) if date_sources else None
    for canon, kind in columns.items():
        entry, src_col = mapping.get(canon), found.get(canon)
        if entry is None or src_col is None:
            report.missing_optional.append(canon)
            continue
        src = df[src_col]
        converted = _apply_transform(src, entry.transform, kind, day_first)
        failures = int((src.notna() & converted.isna()).sum())
        if failures and entry.transform != "satisfaction_good_bad":
            report.coercion_failures[canon] = failures
        out[canon] = converted
        report.mapped[canon] = src_col
        used_sources.add(src_col)
    report.dropped_source_columns = [str(c) for c in df.columns if str(c) not in used_sources]
    return out.reset_index(drop=True), report
