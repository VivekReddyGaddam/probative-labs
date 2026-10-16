"""Action audit, first version: tickets the AI agent closed by itself.

AI agents increasingly act instead of suggesting: they resolve and close tickets with no
person involved. An action that has to be undone is not saved work. For every ticket the
AI closed, the audit checks whether it was reversed: reopened, or followed by a repeat
ticket from the same person about the same type of issue within the window. The same
check runs on tickets your agents closed, in the same months and weighted to the same
issue mix, and only the excess counts against the AI, with the same estimator and ranges
as the resolution audit.

Other actions (approving access, issuing refunds, changing records) need the system's
change history and are planned once a design partner has that data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from probative import schema as S
from probative.audit.resolutions import (
    ALL_TYPES,
    AuditError,
    Match,
    _rates,
    choose_match,
    confidence_level,
    estimate_excess,
    next_ticket,
    type_standardizer,
)
from probative.config import TypingConfig
from probative.ingest import Pseudonymizer, apply_mapping, check_work_units, read_tables, resolve_mapping
from probative.ingest.mapping import find_column
from probative.lineage import code_version, fingerprint
from probative.worktypes import assign_work_types

RESOLVER_CANDIDATES = ["resolved_by", "Resolved by", "Resolved By", "ResolvedBy", "closed_by", "Closed by", "Closed By"]
AI_CLOSED = "_ai_closed"
TRUE_VALUES = {"true", "yes", "y", "1", "t"}


@dataclass
class ClosureInputs:
    tickets: pd.DataFrame
    files: list[dict]
    health: dict
    flag_source: str
    ai_closed: int


def load_closure_inputs(
    tickets: list[Path],
    *,
    ai_resolvers: list[str] | None = None,
    resolver_column: str | None = None,
    ai_flag_column: str | None = None,
    preset: str = "canonical",
    ticket_mapping: dict[str, Any] | None = None,
    typing_fields: list[str] | None = None,
    pseudonymizer: Pseudonymizer | None = None,
    now: pd.Timestamp | None = None,
) -> ClosureInputs:
    """Read the ticket export and mark the tickets the AI closed, before any name is pseudonymized."""
    raw, files, warnings = read_tables(tickets)
    if ai_flag_column:
        col = find_column(raw.columns, [ai_flag_column])
        if col is None:
            raise AuditError(f"no column {ai_flag_column!r} in the ticket export")
        flag = raw[col].astype("string").str.strip().str.lower().isin(TRUE_VALUES)
        source = f"{col} is yes"
    else:
        col = find_column(raw.columns, [resolver_column] if resolver_column else RESOLVER_CANDIDATES)
        if col is None:
            raise AuditError(
                "no 'resolved by' column found: name it with --resolver-column, or give a yes/no column with "
                "--ai-flag-column"
            )
        if not ai_resolvers:
            top = raw[col].astype("string").value_counts().head(5).index.tolist()
            raise AuditError(
                f"say which {col!r} values are the AI with --ai-resolver (most common values: {', '.join(top)})"
            )
        wanted = {v.strip().lower() for v in ai_resolvers}
        flag = raw[col].astype("string").str.strip().str.lower().isin(wanted)
        source = f"{col} is " + " or ".join(repr(v) for v in ai_resolvers)
    flag = flag.fillna(False).to_numpy()

    units, _ = apply_mapping(
        raw,
        resolve_mapping(preset, ticket_mapping or {}),
        S.WORK_UNIT_COLUMNS,
        required=(S.UNIT_ID, S.OPENED, S.REQUESTER),
    )
    units[AI_CLOSED] = flag
    if pseudonymizer is not None:
        units = pseudonymizer.apply(units, (*S.PERSON_COLUMNS, *S.SOURCE_ID_COLUMNS))
    units[S.TEAM] = units[S.TEAM].astype("string").fillna("(unassigned)") if S.TEAM in units.columns else ALL_TYPES
    units, health = check_work_units(units, now=now)
    for w in warnings:
        health.add("schema_change", "warning", w)
    fields = [
        f for f in (typing_fields or ["category", "subcategory"]) if f in units.columns and units[f].notna().any()
    ]
    if fields:
        units[S.WORK_TYPE] = assign_work_types(units, TypingConfig(fields=fields, rules=[], min_share=0.0))
    if S.DESCRIPTION in units.columns:
        units = units.drop(columns=[S.DESCRIPTION])
    return ClosureInputs(
        tickets=units,
        files=[f.to_dict() for f in files],
        health=health.to_dict(),
        flag_source=source,
        ai_closed=int(units[AI_CLOSED].sum()),
    )


@dataclass
class ClosureAudit:
    headline: str
    settings: dict
    period: dict
    counts: dict
    reversals: dict
    failed_share: dict
    by_type: list[dict]
    by_month: list[dict]
    confidence: dict
    program: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    kind: str = "closure_audit"

    def to_dict(self) -> dict:
        from probative.pipeline import _clean

        return _clean(self.__dict__)


def audit_closures(
    tickets: pd.DataFrame,
    *,
    window_days: int = 7,
    match: Match = "same_type",
    reps: int = 999,
    seed: int = 20261014,
    level: float = 0.95,
    min_cell: int = 30,
    flag_source: str = "",
    program: dict | None = None,
    files: list[dict] | None = None,
) -> ClosureAudit:
    """Compare how often the AI's own closures were reversed with your agents' closures."""
    if AI_CLOSED not in tickets.columns:
        raise AuditError("the tickets do not say which ones the AI closed; load them with load_closure_inputs")
    notes: list[str] = []
    window = pd.Timedelta(days=window_days)
    tk = tickets.dropna(subset=[S.OPENED, S.REQUESTER])
    data_end = tk[S.OPENED].max()
    closed = tk.dropna(subset=[S.RESOLVED])
    eligible = closed[closed[S.RESOLVED] <= data_end - window].copy()
    ai = eligible[eligible[AI_CLOSED].astype(bool)].copy()
    people = eligible[~eligible[AI_CLOSED].astype(bool)].copy()
    if ai.empty:
        raise AuditError(f"no tickets closed by the AI with {window_days} days of follow-up ({flag_source})")
    if people.empty:
        raise AuditError("no tickets closed by people to compare with")

    used_match, _, match_notes, typed, by = choose_match(ai, tk, match, noun="AI-closed tickets")
    notes.extend(match_notes)
    std_type = type_standardizer(ai, typed)
    has_reopens = S.REOPENS in tk.columns and tk[S.REOPENS].notna().any()
    if not has_reopens:
        notes.append("Reopen counts are not in the export, so only repeat tickets count as reversals.")
    lo, hi = ai[S.RESOLVED].min(), ai[S.RESOLVED].max()
    people = people[(people[S.RESOLVED] >= lo) & (people[S.RESOLVED] <= hi)].copy()
    for df in (ai, people):
        df["_type"] = std_type(df[S.WORK_TYPE] if typed else df[S.REQUESTER])
        df["_month"] = df[S.RESOLVED].dt.strftime("%Y-%m")
        df["_reopened"] = (df[S.REOPENS].fillna(0) > 0).to_numpy() if has_reopens else False
        df["_repeat"] = next_ticket(df, S.RESOLVED, tk, by, window).notna().to_numpy()
        df["_ret"] = df["_reopened"] | df["_repeat"]

    est = estimate_excess(ai, people, reps=reps, seed=seed, level=level, min_cell=min_cell)
    point = est["point"]
    f, f_lo, f_hi = point["failed"], est["f_lo"], est["f_hi"]
    half_width = (f_hi - f_lo) / 2 if f_hi == f_hi else float("nan")
    confidence = confidence_level(
        n_items=len(ai),
        n_base=len(people),
        half_width=half_width,
        items="AI closures",
        base="closures by your agents",
        match_fallback=match == "same_type" and used_match == "any",
    )
    by_type = [r for r in sorted(point["rows"], key=lambda r: -r["claims"]) if r["claims"] >= min_cell]
    by_month = []
    for month in sorted(ai["_month"].unique()):
        a_m, p_m = ai[ai["_month"] == month], people[people["_month"] == month]
        if len(a_m) >= min_cell and len(p_m):
            m = _rates(a_m, p_m, min_cell)
            by_month.append(
                {
                    "month": month,
                    "closures": m["claims"],
                    "ai_rate": m["ai"],
                    "people_rate": m["base"],
                    "failed_share": m["failed"],
                }
            )

    rng = f"{int(level * 100)}% range {_p(f_lo)} to {_p(f_hi)}"
    head = (
        f"Of {len(ai):,} tickets the AI closed by itself, {_p(point['ai'])} were reversed (reopened, or a repeat "
        f"ticket within {window_days} days) against {_p(point['base'])} of tickets your agents closed"
    )
    if f_lo > 0:
        head += f": {_p(f)} of the AI's closures did not stick beyond that normal rate ({rng})."
    elif f_hi < 0:
        head += f": the AI's closures stuck better ({rng})."
    else:
        head += f": no difference that the data can show ({_p(f)}, {rng})."
    head += f" Audit confidence: {confidence['level']}."

    settings = {"window_days": window_days, "match": used_match, "reps": reps, "seed": seed, "level": level}
    return ClosureAudit(
        headline=head,
        settings=settings,
        period={"from": lo.date(), "to": hi.date()},
        counts={"ai_closures": len(ai), "people_closures": len(people), "flag": flag_source},
        reversals={
            "ai_rate": point["ai"],
            "ai_ci_low": est["ai_lo"],
            "ai_ci_high": est["ai_hi"],
            "people_rate": point["base"],
            "people_ci_low": est["b_lo"],
            "people_ci_high": est["b_hi"],
            "ai_reopened": float(ai["_reopened"].mean()),
            "ai_repeat": float(ai["_repeat"].mean()),
            "people_reopened": float(people["_reopened"].mean()),
            "people_repeat": float(people["_repeat"].mean()),
        },
        failed_share={"estimate": f, "ci_low": f_lo, "ci_high": f_hi, "excludes_zero": bool(f_lo > 0 or f_hi < 0)},
        by_type=by_type,
        by_month=by_month,
        confidence=confidence,
        program=program or {},
        lineage={
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code": code_version(),
            "settings_sha256": fingerprint(settings),
            "files": files or [],
            "interval": est["interval"],
        },
        notes=notes,
    )


def _p(x: float) -> str:
    return "n/a" if x is None or x != x else f"{x * 100:.0f}%".replace("-", "−")
