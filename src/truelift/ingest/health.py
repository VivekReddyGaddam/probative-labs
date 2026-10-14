"""Data health monitor: flag and exclude records that would corrupt an analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from truelift import schema as S

Severity = Literal["error", "warning", "info"]


@dataclass
class HealthIssue:
    check: str
    severity: Severity
    message: str
    rows: int = 0

    def to_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity, "message": self.message, "rows": self.rows}


@dataclass
class HealthReport:
    rows_in: int = 0
    issues: list[HealthIssue] = field(default_factory=list)
    exclusions: dict[str, int] = field(default_factory=dict)

    @property
    def rows_excluded(self) -> int:
        return sum(self.exclusions.values())

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def add(self, check: str, severity: Severity, message: str, rows: int = 0) -> None:
        self.issues.append(HealthIssue(check, severity, message, rows))

    def exclude(self, reason: str, rows: int) -> None:
        if rows:
            self.exclusions[reason] = self.exclusions.get(reason, 0) + rows

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "rows_in": self.rows_in,
            "rows_excluded": self.rows_excluded,
            "exclusions": dict(self.exclusions),
            "issues": [i.to_dict() for i in self.issues],
        }


def check_work_units(
    df: pd.DataFrame,
    *,
    now: pd.Timestamp | None = None,
    optional_null_warn: float = 0.2,
    long_resolution_days: float = 365,
) -> tuple[pd.DataFrame, HealthReport]:
    """Return the rows safe to analyze and a report of what was flagged or removed."""
    report = HealthReport(rows_in=len(df))
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)

    missing = [c for c in S.REQUIRED_WORK_UNIT_COLUMNS if c not in df.columns]
    if missing:
        report.add("required_fields", "error", f"missing required fields: {', '.join(missing)}")
        return df.iloc[0:0], report

    keep = pd.Series(True, index=df.index)

    for col in S.REQUIRED_WORK_UNIT_COLUMNS:
        null = df[col].isna()
        if null.any():
            report.add("required_nulls", "warning", f"{int(null.sum())} rows have no {col}", int(null.sum()))
            report.exclude(f"missing {col}", int((null & keep).sum()))
            keep &= ~null

    dup = df[S.UNIT_ID].duplicated(keep="first") & keep
    if dup.any():
        report.add("duplicates", "warning", f"{int(dup.sum())} duplicate work-unit IDs; kept the first", int(dup.sum()))
        report.exclude("duplicate ID", int(dup.sum()))
        keep &= ~dup

    future = (df[S.OPENED] > now) & keep
    if future.any():
        report.add("future_timestamps", "warning", f"{int(future.sum())} rows opened in the future", int(future.sum()))
        report.exclude("opened in the future", int(future.sum()))
        keep &= ~future

    if S.RESOLVED in df.columns:
        impossible = (df[S.RESOLVED] < df[S.OPENED]) & keep
        if impossible.any():
            report.add(
                "impossible_timestamps",
                "warning",
                f"{int(impossible.sum())} rows resolved before they were opened",
                int(impossible.sum()),
            )
            report.exclude("resolved before opened", int(impossible.sum()))
            keep &= ~impossible
        dur_days = (df[S.RESOLVED] - df[S.OPENED]).dt.total_seconds() / 86400
        very_long = (dur_days > long_resolution_days) & keep
        if very_long.any():
            report.add(
                "long_durations",
                "info",
                f"{int(very_long.sum())} rows took over {long_resolution_days:.0f} days to resolve (kept)",
                int(very_long.sum()),
            )

    if S.EFFORT in df.columns:
        negative = (df[S.EFFORT] < 0) & keep
        if negative.any():
            report.add(
                "negative_effort",
                "warning",
                f"{int(negative.sum())} rows with negative time worked",
                int(negative.sum()),
            )
            report.exclude("negative time worked", int(negative.sum()))
            keep &= ~negative

    for col in df.columns:
        if col in S.REQUIRED_WORK_UNIT_COLUMNS:
            continue
        rate = float(df.loc[keep, col].isna().mean()) if keep.any() else 0.0
        if rate > optional_null_warn and col not in {S.RESOLVED, S.CLOSED, S.CSAT}:
            report.add("sparse_field", "warning", f"{col} is empty in {rate:.0%} of rows")

    clean = df.loc[keep].reset_index(drop=True)

    if len(clean):
        weeks = clean[S.OPENED].dt.to_period("W-SUN")
        span = pd.period_range(weeks.min(), weeks.max(), freq="W-SUN")
        empty = span.difference(pd.PeriodIndex(weeks.unique()))
        if len(empty):
            report.add(
                "extraction_gaps",
                "warning",
                f"{len(empty)} week(s) with no records at all, e.g. {empty[0].start_time.date()}; "
                "check the extract is complete",
            )

    if not len(clean):
        report.add("empty", "error", "no usable rows after health checks")
    return clean, report


def censored_tail(df: pd.DataFrame, period_col: str, threshold: float) -> tuple[int | None, pd.DataFrame]:
    """Find trailing periods where too many work units are still unresolved.

    Recent tickets that are still open are missing from resolution-time averages,
    which makes recent weeks look artificially fast. Returns the first period to
    drop (or ``None``) and the per-period unresolved share.
    """
    if S.RESOLVED not in df.columns or not len(df):
        return None, pd.DataFrame()
    share = df.groupby(period_col)[S.RESOLVED].apply(lambda s: float(s.isna().mean())).sort_index()
    cut = None
    for period in share.index[::-1]:
        if share[period] > threshold:
            cut = int(period)
        else:
            break
    return cut, share.rename("unresolved_share").reset_index()
