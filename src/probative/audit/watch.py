"""Silent-regression watch: did the AI get worse after a change nobody announced?

Bought AI changes underneath the buyer: vendors update models, prompts and knowledge
sources without notice. Without access to the vendor's logs, the buyer can still watch
outcomes in its own systems.

Each week, the share of the AI's claimed resolutions that came back is compared with the
human baseline in the same weeks, as in the resolution audit. The latest weeks are
compared with the weeks before them, and a significant worsening raises an alert. The
share of conversations the AI resolves is watched the same way. Known vendor release
dates can be tested one by one, with a Bonferroni correction across them.

The two windows are bootstrapped independently. People active in both windows make the
real difference less noisy than that, so the ranges are conservative: fewer false alarms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from probative import schema as S
from probative.audit.resolutions import (
    AuditError,
    Match,
    _interval,
    _rates,
    _returns,
    choose_match,
    estimate_excess,
    type_standardizer,
)
from probative.ingest.conversations import RESOLVED, normalize_outcomes
from probative.lineage import code_version, fingerprint

ORDER = {"regression": 3, "watch": 2, "stable": 1, "improved": 0, "insufficient": -1}


@dataclass
class WatchResult:
    headline: str
    status: str
    settings: dict
    period: dict
    checks: list[dict]
    events: list[dict]
    weekly: list[dict]
    change_point: dict | None
    program: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    kind: str = "regression_watch"

    def to_dict(self) -> dict:
        from probative.pipeline import _clean

        return _clean(self.__dict__)


def _status(delta: float, lo: float, hi: float, min_change: float, worse_is_up: bool) -> str:
    if delta != delta:
        return "insufficient"
    d, d_lo, d_hi = (delta, lo, hi) if worse_is_up else (-delta, -hi, -lo)
    if d >= min_change and d_lo > 0:
        return "regression"
    if d >= min_change:
        return "watch"
    if -d >= min_change and d_hi < 0:
        return "improved"
    return "stable"


def _week(ts: pd.Series) -> pd.Series:
    return (ts.dt.normalize() - pd.to_timedelta(ts.dt.weekday, unit="D")).dt.date


def _excess_between(cl, base, weeks_a, weeks_b, *, reps, seed, level, min_cell):
    """Excess return share in weeks_b minus weeks_a, with independent bootstraps of each window."""
    out = []
    for i, weeks in enumerate((weeks_a, weeks_b)):
        c = cl[cl["_week"].isin(weeks)].assign(_month="window")
        b = base[base["_week"].isin(weeks)].assign(_month="window")
        if len(c) < min_cell or len(b) < min_cell:
            return None
        out.append(estimate_excess(c, b, reps=reps, seed=seed + i, level=level, min_cell=min_cell))
    a, b = out
    fa, fb = a["point"]["failed"], b["point"]["failed"]
    diff = b["draws"]["failed"] - a["draws"]["failed"]
    lo, hi = _interval(diff, level)
    return {
        "before": fa,
        "after": fb,
        "delta": fb - fa,
        "ci_low": lo,
        "ci_high": hi,
        "n_after": len(cl[cl["_week"].isin(weeks_b)]),
    }


def _rate_between(conv, weeks_a, weeks_b, *, reps, seed, level):
    """Share of conversations the AI resolved, weeks_b minus weeks_a, bootstrapping people."""
    rng = np.random.default_rng(seed)
    out = []
    for weeks in (weeks_a, weeks_b):
        c = conv[conv["_week"].isin(weeks)]
        if c.empty:
            return None
        g = c.groupby(S.REQUESTER)["_resolved"].agg(["size", "sum"])
        w = rng.poisson(1.0, size=(reps, len(g)))
        draws = (w @ g["sum"].to_numpy(float)) / np.maximum(w @ g["size"].to_numpy(float), 1e-9)
        out.append((float(c["_resolved"].mean()), draws))
    (ra, da), (rb, db) = out
    lo, hi = _interval(db - da, level)
    return {"before": ra, "after": rb, "delta": rb - ra, "ci_low": lo, "ci_high": hi}


def watch(  # noqa: C901 - one ordered story, like the other audits
    conversations: pd.DataFrame,
    tickets: pd.DataFrame,
    *,
    window_days: int = 7,
    match: Match = "same_type",
    recent_weeks: int = 4,
    reference_weeks: int = 12,
    min_change: float = 0.02,
    events: list[tuple[date, str]] | None = None,
    resolved_value: str = "resolved",
    reps: int = 499,
    seed: int = 20261014,
    level: float = 0.95,
    min_cell: int = 30,
    program: dict | None = None,
    files: list[dict] | None = None,
) -> WatchResult:
    """Compare the latest weeks of AI outcomes with the weeks before, and test known release dates."""
    notes: list[str] = []
    window = pd.Timedelta(days=window_days)
    tk = tickets.dropna(subset=[S.OPENED, S.REQUESTER])
    if tk.empty:
        raise AuditError("no tickets with a requester to watch against")
    data_start, data_end = tk[S.OPENED].min(), tk[S.OPENED].max()

    conv = conversations.dropna(subset=[S.REQUESTER, S.SESSION_STARTED]).copy()
    has_outcome = S.SESSION_OUTCOME in conv.columns and conv[S.SESSION_OUTCOME].notna().any()
    outcome = normalize_outcomes(conv[S.SESSION_OUTCOME] if has_outcome else None, conv.index, resolved_value)
    conv["_resolved"] = outcome.eq(RESOLVED).astype(bool)
    conv = conv[(conv[S.SESSION_STARTED] >= data_start) & (conv[S.SESSION_STARTED] <= data_end - window)]
    conv["_week"] = _week(conv[S.SESSION_STARTED])
    cl = conv[conv["_resolved"]].copy()
    if cl.empty:
        raise AuditError("no claimed resolutions inside the ticket export with a full window of follow-up")

    used_match, _, match_notes, typed, by = choose_match(cl, tk, match)
    notes.extend(match_notes)
    std_type = type_standardizer(cl, typed)
    cl["_type"] = std_type(cl[S.WORK_TYPE] if typed else cl[S.REQUESTER])
    cl["_ret"] = _returns(cl, S.SESSION_STARTED, tk, by, window)
    base = tk.dropna(subset=[S.RESOLVED])
    base = base[(base[S.RESOLVED] >= cl[S.SESSION_STARTED].min()) & (base[S.RESOLVED] <= data_end - window)].copy()
    base["_type"] = std_type(base[S.WORK_TYPE] if typed else base[S.REQUESTER])
    base["_week"] = _week(base[S.RESOLVED])
    base["_ret"] = _returns(base, S.RESOLVED, tk, by, window)

    weeks = sorted(cl["_week"].unique())
    # The last week may be partial: it is kept, since every claim in it has a full return window.
    recent = weeks[-recent_weeks:]
    reference = weeks[-(recent_weeks + reference_weeks) : -recent_weeks]
    checks: list[dict] = []
    if len(reference) < 4:
        status = "insufficient"
        notes.append(f"Only {len(weeks)} weeks of claims: at least {recent_weeks + 4} are needed to compare windows.")
    else:
        ex = _excess_between(cl, base, reference, recent, reps=reps, seed=seed, level=level, min_cell=min_cell)
        if ex:
            checks.append(
                {
                    "metric": "Claimed resolutions that did not hold",
                    "unit": "share",
                    **ex,
                    "status": _status(ex["delta"], ex["ci_low"], ex["ci_high"], min_change, worse_is_up=True),
                }
            )
        if has_outcome:
            rr = _rate_between(conv, reference, recent, reps=reps, seed=seed + 7, level=level)
            if rr:
                checks.append(
                    {
                        "metric": "Conversations the AI resolved",
                        "unit": "share",
                        **rr,
                        "status": _status(rr["delta"], rr["ci_low"], rr["ci_high"], 0.03, worse_is_up=False),
                    }
                )
        vol_ref = conv[conv["_week"].isin(reference)].groupby("_week").size().mean()
        vol_rec = conv[conv["_week"].isin(recent)].groupby("_week").size().mean()
        checks.append(
            {
                "metric": "Conversations per week",
                "unit": "count",
                "before": float(vol_ref),
                "after": float(vol_rec),
                "delta": float(vol_rec - vol_ref),
                "ci_low": None,
                "ci_high": None,
                "status": "info",
            }
        )
        status = max((c["status"] for c in checks if c["status"] in ORDER), key=ORDER.get, default="insufficient")

    # Known release dates, one test each, Bonferroni across them ---------------------------------
    event_rows = []
    events = [e for e in (events or []) if weeks and weeks[0] < e[0] <= weeks[-1]]
    if events:
        level_each = 1 - (1 - level) / len(events)
        for d, name in events:
            start = d - timedelta(days=d.weekday())
            before = [w for w in weeks if w < start][-4:]
            after = [w for w in weeks if w >= start][:4]
            ex = None
            if len(before) >= 2 and len(after) >= 2:
                ex = _excess_between(
                    cl, base, before, after, reps=reps, seed=seed + 13, level=level_each, min_cell=min_cell
                )
            event_rows.append(
                {
                    "date": d,
                    "name": name,
                    **(ex or {"before": None, "after": None, "delta": None, "ci_low": None, "ci_high": None}),
                    "status": _status(ex["delta"], ex["ci_low"], ex["ci_high"], min_change, True)
                    if ex
                    else "insufficient",
                    "level": level_each,
                }
            )

    # Weekly series and the most likely change week (descriptive, not a test) -------------------
    weekly = []
    for w in weeks:
        c = cl[cl["_week"] == w].assign(_month="w")
        b = base[base["_week"] == w].assign(_month="w")
        all_w = conv[conv["_week"] == w]
        row = {
            "week": w,
            "claims": len(c),
            "conversations": len(all_w),
            "ai_return_rate": None,
            "baseline_return_rate": None,
            "failed_share": None,
        }
        if has_outcome:
            row["resolved_share"] = float(all_w["_resolved"].mean()) if len(all_w) else None
        if len(c) >= 20 and len(b) >= 20:
            r = _rates(c, b, min_cell)
            row.update(ai_return_rate=r["ai"], baseline_return_rate=r["base"], failed_share=r["failed"])
        weekly.append(row)
    change = None
    best = 0.0
    for k in range(4, len(weeks) - 3):
        after, before = weeks[k:], weeks[max(0, k - 12) : k]
        a = _rates(
            cl[cl["_week"].isin(after)].assign(_month="a"), base[base["_week"].isin(after)].assign(_month="a"), min_cell
        )
        b = _rates(
            cl[cl["_week"].isin(before)].assign(_month="b"),
            base[base["_week"].isin(before)].assign(_month="b"),
            min_cell,
        )
        jump = (a["failed"] or 0) - (b["failed"] or 0)
        if jump > best:
            best, change = jump, {"week": weeks[k], "before": b["failed"], "after": a["failed"], "jump": jump}
    if change and change["jump"] < min_change:
        change = None

    main = next((c for c in checks if c["metric"] == "Claimed resolutions that did not hold"), None)
    words = {
        "regression": "Regression",
        "watch": "Worth watching",
        "stable": "Stable",
        "improved": "Improved",
        "insufficient": "Not enough data",
    }
    if main:
        headline = (
            f"{words[status]}: in the last {len(recent)} weeks, {_pc(main['after'])} of the AI's claimed resolutions "
            f"did not hold, against {_pc(main['before'])} in the {len(reference)} weeks before "
            f"({_pp(main['delta'])}, {int(level * 100)}% range {_pp(main['ci_low'])} to {_pp(main['ci_high'])})."
        )
        rr = next((c for c in checks if c["metric"] == "Conversations the AI resolved"), None)
        if rr and rr["status"] in ("regression", "watch"):
            headline += f" The AI resolved {_pc(rr['after'])} of conversations, down from {_pc(rr['before'])}."
        if change and status in ("regression", "watch"):
            headline += f" Most likely change: the week of {change['week']}."
    else:
        headline = f"{words[status]}: " + (notes[-1] if notes else "too few claims to compare windows.")

    settings = {
        "window_days": window_days,
        "match": used_match,
        "recent_weeks": recent_weeks,
        "reference_weeks": reference_weeks,
        "min_change": min_change,
        "reps": reps,
        "seed": seed,
        "level": level,
    }
    return WatchResult(
        headline=headline,
        status=status,
        settings=settings,
        period={
            "first_week": weeks[0] if weeks else None,
            "last_week": weeks[-1] if weeks else None,
            "recent": [recent[0], recent[-1]] if recent else None,
            "reference": [reference[0], reference[-1]] if reference else None,
        },
        checks=checks,
        events=event_rows,
        weekly=weekly,
        change_point=change,
        program=program or {},
        lineage={
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code": code_version(),
            "settings_sha256": fingerprint(settings),
            "files": files or [],
        },
        notes=notes,
    )


def _pc(x: Any) -> str:
    return "n/a" if x is None or x != x else f"{x * 100:.0f}%"


def _pp(x: Any) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x * 100:+.0f} pts".replace("-", "−")


def load_events(path) -> list[tuple[date, str]]:
    """Vendor release dates: a CSV with a date column and, optionally, a name or note."""
    df = pd.read_csv(path, dtype=str)
    cols = {c.lower().strip(): c for c in df.columns}
    dcol = next((cols[c] for c in ("date", "release_date", "released", "release date") if c in cols), None)
    if dcol is None:
        raise AuditError("the events file needs a date column (date or release_date)")
    ncol = next((cols[c] for c in ("name", "release", "note", "version") if c in cols), None)
    out = []
    for _, row in df.iterrows():
        d = pd.to_datetime(row[dcol], errors="coerce")
        if pd.notna(d):
            out.append((d.date(), str(row[ncol]) if ncol and pd.notna(row[ncol]) else d.date().isoformat()))
    return out
