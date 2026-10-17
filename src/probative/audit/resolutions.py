"""Resolution audit: did the resolutions an AI claims actually hold?

AI agents and virtual agents are judged, and increasingly paid, per resolution.
A claimed resolution is checked against what happened next: did the same person
open a ticket within a few days? Some people come back after any resolution, so
the return rate after the AI's claims is compared with the return rate after
human-resolved tickets of the same types in the same weeks. The excess, scaled to
the share that could have come back, is the share of claimed resolutions that did
not hold:

    failed share = (AI return rate - human return rate) / (1 - human return rate)

Uncertainty comes from resampling people (a Poisson bootstrap), because one
person's contacts are not independent of each other. No comparison group of
teams is needed, so the audit works even when everyone got the AI on the same day.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from probative import schema as S
from probative.config import TypingConfig
from probative.ingest import Pseudonymizer, apply_mapping, check_work_units, read_table, read_tables, resolve_mapping
from probative.ingest.conversations import GENERIC, RESOLVED, map_conversations, normalize_outcomes
from probative.lineage import code_version, fingerprint
from probative.worktypes import assign_work_types

Match = Literal["same_type", "any"]

# Typical column names in conversation or "automated resolution" exports (see probative.ingest.conversations).
CLAIMS_PRESET = GENERIC

OTHER_TYPES = "(other types)"
ALL_TYPES = "(all)"
MIN_TYPE_SHARE = 0.01  # claim types below this share are grouped for the baseline
# A ticket the AI agent opens itself, such as an incident from ServiceNow's Create Incident topic (which sets
# Contact type to Virtual Agent), is a handoff, not a return: a ticket through one of these channels, opened
# within LINK_MINUTES of the same person's latest conversation, was created in that conversation.
AI_CHANNELS = ("virtual agent", "ai agent", "chatbot", "bot")
LINK_MINUTES = 30
_NO_DELAY = pd.Timedelta(0)


class AuditError(RuntimeError):
    pass


@dataclass
class AuditInputs:
    claims: pd.DataFrame  # conversations the AI claimed as resolved
    tickets: pd.DataFrame
    conversations: pd.DataFrame  # every conversation, whatever its outcome
    files: list[dict]
    claims_rows: int
    not_resolutions: int
    typed_on: list[str]
    health: dict
    pseudonymized: bool
    notes: list[str] = field(default_factory=list)


def load_audit_inputs(
    tickets: list[Path],
    claims: Path,
    *,
    preset: str = "canonical",
    ticket_mapping: dict[str, Any] | None = None,
    claims_mapping: dict[str, Any] | None = None,
    typing_fields: list[str] | None = None,
    resolved_value: str = "resolved",
    pseudonymizer: Pseudonymizer | None = None,
    now: pd.Timestamp | None = None,
    claims_preset: str = "generic",
) -> AuditInputs:
    """Read, map, pseudonymize and type the ticket export and the AI's claimed resolutions.

    ``resolved_value`` may list several values separated by commas, for exports that record more
    than one kind of resolution (for example "Assumed resolution,Confirmed resolution").
    """
    raw, files, warnings = read_tables(tickets)
    mapping = resolve_mapping(preset, ticket_mapping or {})
    units, _ = apply_mapping(raw, mapping, S.WORK_UNIT_COLUMNS, required=(S.UNIT_ID, S.OPENED, S.REQUESTER))
    if pseudonymizer is not None:
        units = pseudonymizer.apply(units, (*S.PERSON_COLUMNS, *S.SOURCE_ID_COLUMNS))
    # Teams do not matter here, and a returning ticket counts even before it is assigned.
    if S.TEAM in units.columns:
        units[S.TEAM] = units[S.TEAM].astype("string").fillna("(unassigned)")
    else:
        units[S.TEAM] = ALL_TYPES
    units, health = check_work_units(units, now=now)
    for w in warnings:
        health.add("schema_change", "warning", w)

    c_raw, c_meta = read_table(claims)
    files.append(c_meta)
    cl, input_notes = map_conversations(c_raw, claims_preset, claims_mapping)
    rows = len(cl)
    if pseudonymizer is not None:
        cl = pseudonymizer.apply(cl, (S.REQUESTER,))

    fields = [
        f
        for f in (typing_fields or ["category", "subcategory"])
        if f in cl.columns and cl[f].notna().any() and f in units.columns and units[f].notna().any()
    ]
    if fields:
        tcfg = TypingConfig(fields=fields, rules=[], min_share=0.0)
        cl[S.WORK_TYPE] = assign_work_types(cl, tcfg)
        units[S.WORK_TYPE] = assign_work_types(units, tcfg)
    for df in (cl, units):
        if S.DESCRIPTION in df.columns:
            df.drop(columns=[S.DESCRIPTION], inplace=True)
    has_outcome = S.SESSION_OUTCOME in cl.columns and cl[S.SESSION_OUTCOME].notna().any()
    if has_outcome and (S.OUTCOME_DETAIL not in cl.columns or cl[S.OUTCOME_DETAIL].isna().all()):
        cl[S.OUTCOME_DETAIL] = cl[S.SESSION_OUTCOME].astype("string").str.strip()
    conversations = cl
    if has_outcome:
        is_claim = normalize_outcomes(cl[S.SESSION_OUTCOME], cl.index, resolved_value) == RESOLVED
        cl = cl[is_claim].reset_index(drop=True)
    return AuditInputs(
        claims=cl,
        tickets=units,
        conversations=conversations,
        files=[f.to_dict() for f in files],
        claims_rows=rows,
        not_resolutions=rows - len(cl),
        typed_on=fields,
        health=health.to_dict(),
        pseudonymized=pseudonymizer is not None,
        notes=input_notes,
    )


# ---------------------------------------------------------------------------------


def next_ticket(
    left: pd.DataFrame,
    time_col: str,
    tickets: pd.DataFrame,
    by: list[str],
    window: pd.Timedelta,
    after: pd.Timedelta = _NO_DELAY,
) -> pd.Series:
    """When the same person (and type) next opened a ticket in the ``window`` after ``time_col``; NaT if not.

    With ``after``, only tickets opened later than that after ``time_col`` count.
    """
    out = np.full(len(left), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    if left.empty:
        return pd.Series(out, index=left.index)
    lt = left[[*by, time_col]].copy()
    lt["_row"] = np.arange(len(lt))
    rt = tickets[[*by, S.OPENED]].dropna()
    for c in by:
        lt[c] = lt[c].astype("string")
        rt[c] = rt[c].astype("string")
    # Exports and Parquet files can store times at different precisions; compare them at one.
    lt[time_col] = lt[time_col].astype("datetime64[ns]") + after
    rt[S.OPENED] = rt[S.OPENED].astype("datetime64[ns]")
    rt = rt.rename(columns={S.OPENED: "_next"}).sort_values("_next")
    m = pd.merge_asof(
        lt.sort_values(time_col),
        rt,
        left_on=time_col,
        right_on="_next",
        by=by,
        direction="forward",
        tolerance=window - after,
        allow_exact_matches=False,
    )
    out[m["_row"].to_numpy()] = m["_next"].to_numpy(dtype="datetime64[ns]")
    return pd.Series(out, index=left.index)


def _returns(
    left: pd.DataFrame,
    time_col: str,
    tickets: pd.DataFrame,
    by: list[str],
    window: pd.Timedelta,
    after: pd.Timedelta = _NO_DELAY,
) -> np.ndarray:
    """True where the same person (and type) opened a ticket in the ``window`` after ``time_col``."""
    return next_ticket(left, time_col, tickets, by, window, after).notna().to_numpy()


def _channel_key(values: pd.Series) -> pd.Series:
    return values.astype("string").str.strip().str.lower().str.replace(r"[_\s]+", " ", regex=True)


def _channel_set(channels: tuple[str, ...]) -> set[str]:
    return {" ".join(str(c).lower().replace("_", " ").split()) for c in channels}


def created_in_conversation(
    tickets: pd.DataFrame, conversations: pd.DataFrame, link: pd.Timedelta, ai_channels: tuple[str, ...]
) -> pd.DataFrame:
    """Tickets the AI agent most likely created itself, each with the start of the conversation it came from.

    A ticket counts when it came through one of ``ai_channels`` within ``link`` after the same person's
    latest conversation started.
    """
    is_ai = _channel_key(tickets[S.CHANNEL]).isin(_channel_set(ai_channels)).fillna(False).astype(bool)
    t = tickets.loc[is_ai, [S.REQUESTER, S.OPENED]].dropna()
    c = conversations[[S.REQUESTER, S.SESSION_STARTED]].dropna()
    if t.empty or c.empty:
        return pd.DataFrame(columns=[S.REQUESTER, S.OPENED, "_conv_started"])
    t = t.astype({S.REQUESTER: "string", S.OPENED: "datetime64[ns]"})
    c = c.rename(columns={S.SESSION_STARTED: "_conv_started"}).astype(
        {S.REQUESTER: "string", "_conv_started": "datetime64[ns]"}
    )
    m = pd.merge_asof(
        t.sort_values(S.OPENED),
        c.sort_values("_conv_started"),
        left_on=S.OPENED,
        right_on="_conv_started",
        by=S.REQUESTER,
        direction="backward",
        tolerance=link,
        allow_exact_matches=True,
    )
    return m.dropna(subset=["_conv_started"])


def _fast_ticket_channels(
    claims: pd.DataFrame, tickets: pd.DataFrame, link: pd.Timedelta, ai_channels: tuple[str, ...]
) -> list[tuple[str, int]]:
    """Channels of tickets opened within ``link`` after a claimed resolution, other than the AI's own."""
    lt = claims[[S.REQUESTER, S.SESSION_STARTED]].dropna().astype({S.REQUESTER: "string"})
    lt[S.SESSION_STARTED] = lt[S.SESSION_STARTED].astype("datetime64[ns]")
    rt = tickets[[S.REQUESTER, S.OPENED, S.CHANNEL]].dropna(subset=[S.REQUESTER, S.OPENED])
    rt = rt.rename(columns={S.OPENED: "_opened"}).astype({S.REQUESTER: "string", "_opened": "datetime64[ns]"})
    m = pd.merge_asof(
        lt.sort_values(S.SESSION_STARTED),
        rt.sort_values("_opened"),
        left_on=S.SESSION_STARTED,
        right_on="_opened",
        by=S.REQUESTER,
        direction="forward",
        tolerance=link,
        allow_exact_matches=False,
    )
    ch = m.dropna(subset=["_opened"])[S.CHANNEL].astype("string").str.strip()
    ch = ch[~_channel_key(ch).isin(_channel_set(ai_channels)).fillna(False).astype(bool)]
    return [(str(k), int(v)) for k, v in ch.fillna("(blank)").value_counts().head(5).items()]


def _failed(r: float, b: float) -> float:
    return (r - b) / (1 - b) if b == b and b < 1 else float("nan")


def _cell_baselines(base: pd.DataFrame, min_cell: int) -> tuple[dict, dict, float]:
    by_cell = base.groupby(["_month", "_type"], observed=True)["_ret"].agg(["size", "sum"])
    by_type = base.groupby("_type", observed=True)["_ret"].agg(["size", "sum"])
    cells = {k: (int(g["size"]), float(g["sum"])) for k, g in by_cell.iterrows()}
    types = {k: (int(g["size"]), float(g["sum"])) for k, g in by_type.iterrows()}
    pooled = float(base["_ret"].mean()) if len(base) else float("nan")
    return cells, types, pooled


def _rates(cl: pd.DataFrame, base: pd.DataFrame, min_cell: int) -> dict[str, Any]:
    """Point estimates, with the human baseline standardized to the claims' mix of months and types.

    Each (month, type) cell of claims is compared with human-resolved tickets of the same type in the
    same month; sparse cells fall back to the same type in any month, then to all tickets.
    """
    cells, types, pooled = _cell_baselines(base, min_cell)
    nc = cl.groupby(["_month", "_type"], observed=True)["_ret"].agg(["size", "sum"])
    out_cells = []
    for (month, wtype), g in nc.iterrows():
        n_cell, r_cell = cells.get((month, wtype), (0, 0.0))
        n_type, r_type = types.get(wtype, (0, 0.0))
        if n_cell >= min_cell:
            b, src = r_cell / n_cell, "cell"
        elif n_type >= min_cell:
            b, src = r_type / n_type, "type"
        else:
            b, src = pooled, "pooled"
        out_cells.append((month, wtype, int(g["size"]), int(g["sum"]), b, src))
    n = sum(c[2] for c in out_cells)
    if n == 0:
        return {"ai": float("nan"), "base": float("nan"), "failed": float("nan"), "claims": 0, "rows": [], "cells": []}
    r = sum(c[3] for c in out_cells) / n
    b = sum(c[2] * c[4] for c in out_cells) / n
    rows = []
    for wtype in dict.fromkeys(c[1] for c in out_cells):
        mine = [c for c in out_cells if c[1] == wtype]
        k = sum(c[2] for c in mine)
        ret = sum(c[3] for c in mine)
        b_t = sum(c[2] * c[4] for c in mine) / k
        rows.append(
            {
                "work_type": str(wtype),
                "claims": k,
                "returned": ret,
                "ai_return_rate": ret / k,
                "baseline_tickets": types.get(wtype, (0, 0.0))[0],
                "baseline_return_rate": b_t,
                "baseline": "same type" if types.get(wtype, (0, 0.0))[0] >= min_cell else "all types",
                "failed_share": _failed(ret / k, b_t),
            }
        )
    return {
        "ai": r,
        "base": b,
        "failed": _failed(r, b),
        "claims": n,
        "baseline_tickets": len(base),
        "rows": rows,
        "cells": out_cells,
        "type_own": {t: v[0] >= min_cell for t, v in types.items()},
    }


def _bootstrap(cl: pd.DataFrame, base: pd.DataFrame, point: dict, reps: int, seed: int, chunk: int = 100) -> dict:
    """Poisson bootstrap over people: every person's claims and tickets move together."""
    from scipy import sparse

    people = pd.Index(pd.concat([cl[S.REQUESTER], base[S.REQUESTER]]).astype("string").unique())
    cells = [(c[0], c[1]) for c in point["cells"]]
    cell_own = np.array([c[5] == "cell" for c in point["cells"]])
    types = list(dict.fromkeys(c[1] for c in cells))
    type_own = np.array([point["type_own"].get(t, False) for t in types])
    cell_index = {c: i for i, c in enumerate(cells)}
    type_index = {t: i for i, t in enumerate(types)}
    cell_type = np.array([type_index[c[1]] for c in cells])
    n_people = len(people)

    def matrix(df: pd.DataFrame, keys: pd.Series, index: dict, width: int, values: np.ndarray):
        cols = keys.map(index)
        ok = cols.notna().to_numpy()
        rows = people.get_indexer(df[S.REQUESTER].astype("string"))[ok]
        return sparse.csr_matrix(
            (values[ok], (rows, cols[ok].astype(int).to_numpy())), shape=(n_people, max(width, 1))
        ).T.tocsr()

    def cell_keys(df: pd.DataFrame) -> pd.Series:
        return pd.Series(list(zip(df["_month"], df["_type"], strict=True)), index=df.index)

    rc_vals, rb_vals = cl["_ret"].to_numpy(float), base["_ret"].to_numpy(float)
    one_c, one_b = np.ones(len(cl)), np.ones(len(base))
    c = matrix(cl, cell_keys(cl), cell_index, len(cells), one_c)
    rc = matrix(cl, cell_keys(cl), cell_index, len(cells), rc_vals)
    h = matrix(base, cell_keys(base), cell_index, len(cells), one_b)
    rh = matrix(base, cell_keys(base), cell_index, len(cells), rb_vals)
    ht = matrix(base, base["_type"], type_index, len(types), one_b)
    rht = matrix(base, base["_type"], type_index, len(types), rb_vals)
    everyone = pd.Series(0, index=base.index)
    h_all = matrix(base, everyone, {0: 0}, 1, one_b)
    r_all = matrix(base, everyone, {0: 0}, 1, rb_vals)

    rng = np.random.default_rng(seed)
    ai, bl, fs = [], [], []
    for start in range(0, reps, chunk):
        w = rng.poisson(1.0, size=(n_people, min(chunk, reps - start))).astype(np.float64)
        nc, nrc, nh, nrh = (c @ w).T, (rc @ w).T, (h @ w).T, (rh @ w).T
        nht, nrht = (ht @ w).T[:, cell_type], (rht @ w).T[:, cell_type]
        with np.errstate(invalid="ignore", divide="ignore"):
            pooled = ((r_all @ w) / (h_all @ w)).ravel()
            by_type = np.where(type_own[cell_type][None, :] & (nht > 0), nrht / nht, pooled[:, None])
            bk = np.where(cell_own[None, :] & (nh > 0), nrh / nh, by_type)
            total = nc.sum(axis=1)
            r = nrc.sum(axis=1) / total
            b = (nc * bk).sum(axis=1) / total
            f = (r - b) / (1 - b)
        ai.append(r)
        bl.append(b)
        fs.append(f)
    return {"ai": np.concatenate(ai), "base": np.concatenate(bl), "failed": np.concatenate(fs)}


def _interval(draws: np.ndarray, level: float) -> tuple[float, float]:
    d = draws[np.isfinite(draws)]
    if len(d) < 10:
        return float("nan"), float("nan")
    a = (1 - level) / 2
    return float(np.quantile(d, a)), float(np.quantile(d, 1 - a))


def choose_match(eligible: pd.DataFrame, tickets: pd.DataFrame, match: Match, noun: str = "claims"):
    """Match returns on the same type of issue when both files are typed alike, else on the person only."""
    typed = S.WORK_TYPE in eligible.columns and S.WORK_TYPE in tickets.columns
    known = None
    if typed and len(eligible):
        known = float(eligible[S.WORK_TYPE].isin(set(tickets[S.WORK_TYPE].dropna())).mean())
    notes: list[str] = []
    used: Match = match
    if match == "same_type" and not typed:
        used = "any"
        notes.append(f"The {noun} carry no category, so a return was any ticket from the same person.")
    elif match == "same_type" and known is not None and known < 0.5:
        used = "any"
        notes.append(
            f"Only {known:.0%} of the {noun}' categories exist in the ticket data, so a return was any "
            "ticket from the same person. Map the category to the ticket category for a tighter check."
        )
    by = [S.REQUESTER] + ([S.WORK_TYPE] if used == "same_type" else [])
    return used, known, notes, typed, by


def type_standardizer(eligible: pd.DataFrame, typed: bool):
    """Types for standardizing the baseline; types under 1% of the AI's items are grouped."""
    if typed:
        shares = eligible[S.WORK_TYPE].value_counts(normalize=True)
        kept = set(shares[shares >= MIN_TYPE_SHARE].index)

        def std_type(s: pd.Series) -> pd.Series:
            return s.astype("string").where(s.isin(kept), OTHER_TYPES)

        return std_type

    def all_types(s: pd.Series) -> pd.Series:
        return pd.Series(ALL_TYPES, index=s.index, dtype="string")

    return all_types


def estimate_excess(
    cl: pd.DataFrame, base: pd.DataFrame, *, reps: int, seed: int, level: float, min_cell: int
) -> dict[str, Any]:
    """The excess return share with its range: people bootstrap, widened to a month jackknife if larger.

    ``cl`` holds the AI's items and ``base`` the human comparison, each with the person, ``_month``,
    ``_type`` and ``_ret`` (did it come back) columns.
    """
    point = _rates(cl, base, min_cell)
    draws = _bootstrap(cl, base, point, reps, seed)
    f_lo, f_hi = _interval(draws["failed"], level)
    # People are resampled, but months are shared by everyone in them: widen the range to a
    # leave-one-month-out jackknife when that is larger, as the main engine does for teams.
    f_point = point["failed"]
    months = sorted(cl["_month"].unique())
    interval = "people bootstrap"
    if len(months) >= 4 and f_point == f_point:
        jk = np.array([_rates(cl[cl["_month"] != m], base[base["_month"] != m], min_cell)["failed"] for m in months])
        jk = jk[np.isfinite(jk)]
        if len(jk) >= 3:
            jack_se = float(np.sqrt((len(jk) - 1) / len(jk) * np.sum((jk - jk.mean()) ** 2)))
            boot_draws = draws["failed"][np.isfinite(draws["failed"])]
            boot_se = float(np.std(boot_draws, ddof=1)) if len(boot_draws) > 1 else 0.0
            if jack_se > boot_se:
                from scipy.stats import norm

                z = float(norm.ppf(1 - (1 - level) / 2))
                f_lo, f_hi = min(f_lo, f_point - z * jack_se), max(f_hi, f_point + z * jack_se)
                interval = "people bootstrap, widened to a leave-one-month-out jackknife"
    ai_lo, ai_hi = _interval(draws["ai"], level)
    b_lo, b_hi = _interval(draws["base"], level)
    return {
        "point": point,
        "f_lo": f_lo,
        "f_hi": f_hi,
        "ai_lo": ai_lo,
        "ai_hi": ai_hi,
        "b_lo": b_lo,
        "b_hi": b_hi,
        "interval": interval,
        "draws": draws,
    }


def confidence_level(
    *,
    n_items: int,
    n_base: int,
    half_width: float,
    items: str,
    base: str,
    id_overlap: float | None = None,
    match_fallback: bool = False,
) -> dict[str, Any]:
    level_i = 2
    reasons: list[str] = []

    def lower(to: int, why: str) -> None:
        nonlocal level_i
        level_i = min(level_i, to)
        reasons.append(why)

    if n_items < 200:
        lower(0, f"Only {n_items:,} {items} could be checked; at least 200 are needed for a firm answer.")
    elif n_items < 500:
        lower(1, f"{n_items:,} {items} could be checked; 500 or more gives a tighter range.")
    if n_base < 300:
        lower(0, f"Only {n_base:,} {base} in the same weeks to compare with.")
    elif n_base < 1000:
        lower(1, f"{n_base:,} {base} in the same weeks to compare with; more history helps.")
    if id_overlap is not None and id_overlap < 0.3:
        lower(
            0,
            f"Only {id_overlap:.0%} of the people in the AI's records appear in the ticket data. The two systems "
            "may identify people differently; map the same identifier (email or employee ID) in both.",
        )
    elif id_overlap is not None and id_overlap < 0.6:
        lower(1, f"{id_overlap:.0%} of the people in the AI's records appear in the ticket data.")
    if match_fallback:
        lower(1, "Returns could not be matched by type of issue, so unrelated tickets count as returns.")
    if half_width == half_width and half_width > 0.15:
        lower(0, f"The range is wide (±{half_width:.0%}).")
    elif half_width == half_width and half_width > 0.08:
        lower(1, f"The range is fairly wide (±{half_width:.0%}).")
    if not reasons:
        reasons.append(f"Enough {items} and {base}, a tight range, and the data lines up across systems.")
    return {"level": LEVELS[level_i], "reasons": reasons}


def _band(n: int) -> str:
    for upper, label in ((200, "under 200"), (1000, "200–999"), (10000, "1,000–9,999"), (100000, "10,000–99,999")):
        if n < upper:
            return label
    return "100,000 or more"


LEVELS = ("low", "medium", "high")


# ---------------------------------------------------------------------------------


@dataclass
class ResolutionAudit:
    headline: str
    settings: dict
    period: dict
    claims: dict
    returns: dict
    failed_share: dict
    held: dict
    billing: dict | None
    by_type: list[dict]
    by_month: list[dict]
    sensitivity: list[dict]
    coverage: dict
    confidence: dict
    program: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    by_detail: list[dict] = field(default_factory=list)
    kind: str = "resolution_audit"

    def to_dict(self) -> dict:
        from probative.pipeline import _clean

        return _clean(self.__dict__)


def audit_resolutions(  # noqa: C901 - one ordered story, like the main analysis
    claims: pd.DataFrame,
    tickets: pd.DataFrame,
    *,
    window_days: int = 7,
    match: Match = "same_type",
    price: float | None = None,
    currency: str = "USD",
    reps: int = 999,
    seed: int = 20261014,
    level: float = 0.95,
    min_cell: int = 30,
    sensitivity_windows: tuple[int, ...] = (3, 7, 14),
    program: dict | None = None,
    files: list[dict] | None = None,
    health: dict | None = None,
    pseudonymized: bool = True,
    claims_rows: int | None = None,
    not_resolutions: int = 0,
    conversations: pd.DataFrame | None = None,
    link_minutes: int = LINK_MINUTES,
    ai_channels: tuple[str, ...] = AI_CHANNELS,
) -> ResolutionAudit:
    """Check the AI's claimed resolutions against what happened next.

    ``conversations`` holds every conversation, whatever its outcome, so a ticket the AI agent created is tied
    to the conversation it came from; without it, the claims are used.
    """
    if window_days < 1:
        raise AuditError("the return window must be at least one day")
    notes: list[str] = []
    window = pd.Timedelta(days=window_days)
    link = pd.Timedelta(minutes=max(int(link_minutes), 0))
    if link >= window:
        raise AuditError("the minutes for tickets the AI agent created must be shorter than the return window")
    tk = tickets.dropna(subset=[S.OPENED, S.REQUESTER]).copy()
    if tk.empty:
        raise AuditError("no tickets with a requester to check the claims against")
    data_start, data_end = tk[S.OPENED].min(), tk[S.OPENED].max()

    cl = claims.copy()
    resolved_rows = len(cl)
    incomplete = cl[S.REQUESTER].isna() | cl[S.SESSION_STARTED].isna()
    cl = cl[~incomplete]

    # Tickets the AI agent created itself. With the ticket's channel, they are found exactly, and the
    # conversations that created them are handoffs, left out of the claims. Without it, a ticket opened in the
    # first minutes could be either, so on both sides only tickets opened after ``link`` count as returns.
    channel_known = S.CHANNEL in tk.columns and bool(tk[S.CHANNEL].notna().any())
    handed_off = pd.Series(False, index=cl.index)
    if channel_known and link > pd.Timedelta(0):
        made = created_in_conversation(tk, conversations if conversations is not None else cl, link, ai_channels)
        if not made.empty:
            keys = set(zip(made[S.REQUESTER].astype(str), made["_conv_started"], strict=True))
            starts = cl[S.SESSION_STARTED].astype("datetime64[ns]")
            handed_off = pd.Series(
                [(str(p), t) in keys for p, t in zip(cl[S.REQUESTER], starts, strict=True)], index=cl.index, dtype=bool
            )
    after = pd.Timedelta(0) if channel_known else link
    n_handed_off = int(handed_off.sum())
    cl_all = cl
    cl = cl[~handed_off]
    outside = (cl[S.SESSION_STARTED] < data_start) | (cl[S.SESSION_STARTED] > data_end)
    too_recent = ~outside & (cl[S.SESSION_STARTED] > data_end - window)
    in_period = cl[~outside].copy()
    eligible = cl[~outside & ~too_recent].copy()
    if eligible.empty:
        raise AuditError(
            "none of the claimed resolutions fall inside the ticket export with "
            f"{window_days} days of follow-up; export tickets covering the same dates"
        )

    used_match, known_share, match_notes, typed, by = choose_match(eligible, tk, match)
    notes.extend(match_notes)
    std_type = type_standardizer(eligible, typed)

    def prepare(
        window_: pd.Timedelta,
        by_: list[str],
        people: set[str] | None = None,
        frame: pd.DataFrame | None = None,
        after_: pd.Timedelta | None = None,
    ):
        src = cl if frame is None else frame
        aft = after if after_ is None else after_
        out_ = (src[S.SESSION_STARTED] < data_start) | (src[S.SESSION_STARTED] > data_end)
        el = src[~out_ & (src[S.SESSION_STARTED] <= data_end - window_)].copy()
        el["_type"] = std_type(el[S.WORK_TYPE] if typed else el[S.REQUESTER])
        el["_month"] = el[S.SESSION_STARTED].dt.strftime("%Y-%m")
        el["_ret"] = _returns(el, S.SESSION_STARTED, tk, by_, window_, aft)
        lo, hi = el[S.SESSION_STARTED].min(), el[S.SESSION_STARTED].max()
        b = tk.dropna(subset=[S.RESOLVED])
        b = b[(b[S.RESOLVED] >= lo) & (b[S.RESOLVED] <= hi)].copy()
        if people is not None:
            b = b[b[S.REQUESTER].isin(people)]
        b["_type"] = std_type(b[S.WORK_TYPE] if typed else b[S.REQUESTER])
        b["_month"] = b[S.RESOLVED].dt.strftime("%Y-%m")
        b["_ret"] = _returns(b, S.RESOLVED, tk, by_, window_, aft)
        return el, b

    eligible, base = prepare(window, by)
    if base.empty:
        raise AuditError("no human-resolved tickets in the same weeks to compare with; include resolved dates")
    est = estimate_excess(eligible, base, reps=reps, seed=seed, level=level, min_cell=min_cell)
    point, rows = est["point"], est["point"]["rows"]
    f_lo, f_hi, interval = est["f_lo"], est["f_hi"], est["interval"]
    ai_lo, ai_hi, b_lo, b_hi = est["ai_lo"], est["ai_hi"], est["b_lo"], est["b_hi"]
    f = point["failed"]

    n_period = len(in_period)
    held = {
        "estimate": min(n_period, n_period * (1 - f)),
        "ci_low": min(n_period, n_period * (1 - f_hi)),
        "ci_high": min(n_period, n_period * (1 - f_lo)),
        "of": n_period,
    }
    billing = None
    if price is not None:
        billing = {
            "price": price,
            "currency": currency,
            "claims": n_period,
            "billed": n_period * price,
            "paid_for_failed": {
                "estimate": max(f, 0.0) * n_period * price,
                "ci_low": max(f_lo, 0.0) * n_period * price,
                "ci_high": max(f_hi, 0.0) * n_period * price,
            },
        }
        if too_recent.any():
            notes.append(
                f"{int(too_recent.sum()):,} claims from the last {window_days} days could not be checked yet; "
                "the billing estimate assumes they held at the same rate."
            )

    # Breakdowns and sensitivity -------------------------------------------------------------
    by_type = [
        r for r in sorted(rows, key=lambda r: -r["claims"]) if r["claims"] >= min_cell and r["work_type"] != ALL_TYPES
    ]
    by_month = []
    em = eligible[S.SESSION_STARTED].dt.to_period("M")
    bm = base[S.RESOLVED].dt.to_period("M")
    for month in sorted(em.unique()):
        e_m, b_m = eligible[em == month], base[bm == month]
        if len(e_m) < min_cell or b_m.empty:
            continue
        m = _rates(e_m, b_m, min_cell)
        by_month.append(
            {
                "month": str(month),
                "claims": m["claims"],
                "ai_return_rate": m["ai"],
                "baseline_return_rate": m["base"],
                "failed_share": m["failed"],
            }
        )

    sensitivity = []
    for w in sorted(set(sensitivity_windows) | {window_days}):
        e_w, b_w = (eligible, base) if w == window_days else prepare(pd.Timedelta(days=w), by)
        if e_w.empty or b_w.empty:
            continue
        s = _rates(e_w, b_w, min_cell)
        sensitivity.append(
            {
                "check": f"Return window {w} days",
                "failed_share": s["failed"],
                "claims": s["claims"],
                "main": w == window_days,
            }
        )
    if typed:
        alt = "any" if used_match == "same_type" else "same_type"
        e_a, b_a = prepare(window, [S.REQUESTER] + ([S.WORK_TYPE] if alt == "same_type" else []))
        s = _rates(e_a, b_a, min_cell)
        label = "Any ticket counts as a return" if alt == "any" else "Only the same type counts as a return"
        sensitivity.append({"check": label, "failed_share": s["failed"], "claims": s["claims"], "main": False})
    claim_people = set(eligible[S.REQUESTER].dropna().astype(str))
    e_p, b_p = prepare(window, by, people=claim_people)
    if len(b_p) >= min_cell:
        s = _rates(e_p, b_p, min_cell)
        sensitivity.append(
            {
                "check": "Baseline from the same people only",
                "failed_share": s["failed"],
                "claims": s["claims"],
                "main": False,
            }
        )
    if n_handed_off or not channel_known:
        e_x, b_x = prepare(window, by, frame=cl_all, after_=pd.Timedelta(0))
        if not e_x.empty and not b_x.empty:
            s = _rates(e_x, b_x, min_cell)
            sensitivity.append(
                {
                    "check": "Every ticket counts, even ones the AI agent may have created",
                    "failed_share": s["failed"],
                    "claims": s["claims"],
                    "main": False,
                }
            )

    if n_handed_off:
        notes.append(
            f"{n_handed_off:,} conversations the AI counted as resolved created a ticket themselves: a ticket "
            f"through the AI agent's channel within {link_minutes} minutes. They were handed to your team, so they "
            "are left out of the check instead of being counted as returns. If the AI's own reporting counts them "
            "as resolved, its resolution rate includes them."
        )
    if channel_known and link > pd.Timedelta(0):
        fast = _fast_ticket_channels(eligible, tk, link, ai_channels)
        if sum(n for _, n in fast) >= max(10, 0.01 * len(eligible)):
            notes.append(
                f"Tickets opened within {link_minutes} minutes of a claimed resolution came through "
                + ", ".join(f"{c} ({n:,})" for c, n in fast)
                + ". If the AI agent creates tickets through one of these channels, list it as an AI channel, so "
                "those tickets count as handoffs, not returns."
            )
    elif not channel_known and link > pd.Timedelta(0):
        notes.append(
            "The ticket export has no channel, so a ticket the AI agent created during a conversation can't be "
            f"told apart from a person coming straight back. Returns count from {link_minutes} minutes after each "
            "conversation or resolution, on both sides; add the channel (Contact type in ServiceNow) to count "
            "faster returns too."
        )

    # How the platform says each claim ended: a claim the person walked away from is not the same as one
    # where the topic finished. Raw return rates, with the human baseline for scale.
    by_detail = []
    if S.OUTCOME_DETAIL in eligible.columns and eligible[S.OUTCOME_DETAIL].nunique(dropna=True) >= 2:
        for detail, grp in eligible.groupby(S.OUTCOME_DETAIL, dropna=True):
            if len(grp) < min_cell:
                continue
            rate = float(grp["_ret"].mean())
            by_detail.append(
                {
                    "detail": str(detail),
                    "claims": int(len(grp)),
                    "share": len(grp) / len(eligible),
                    "returned": int(grp["_ret"].sum()),
                    "ai_return_rate": rate,
                    "failed_share": _failed(rate, point["base"]),
                }
            )
        by_detail.sort(key=lambda d: -d["claims"])

    # Coverage and confidence -------------------------------------------------------------------
    ticket_people = set(tk[S.REQUESTER].dropna().astype(str))
    id_overlap = float(eligible[S.REQUESTER].astype(str).isin(ticket_people).mean())
    half_width = (f_hi - f_lo) / 2 if f_hi == f_hi else float("nan")
    n_el, n_base = len(eligible), len(base)
    confidence = confidence_level(
        n_items=n_el,
        n_base=n_base,
        half_width=half_width,
        items="claimed resolutions",
        base="human-resolved tickets",
        id_overlap=id_overlap,
        match_fallback=match == "same_type" and used_match == "any",
    )

    # Headline ------------------------------------------------------------------------------------
    from probative.report.pack import money, unit_price

    rng_text = f"{int(level * 100)}% range {_p(f_lo)} to {_p(f_hi)}"
    compare = "human-resolved tickets of the same types" if typed else "human-resolved tickets"
    if f_lo > 0:
        headline = (
            f"Of {n_period:,} resolutions the AI claimed, an estimated {_p(f)} ({rng_text}) did not hold: "
            f"the person came back within {window_days} days, beyond the normal return rate after {compare}."
        )
    elif f_hi < 0:
        headline = (
            f"The AI's {n_period:,} claimed resolutions held better than {compare}: people came back "
            f"{_p(-f)} less often within {window_days} days ({rng_text})."
        )
    else:
        headline = (
            f"Of {n_period:,} resolutions the AI claimed, the share that did not hold ({_p(f)}, {rng_text}) is "
            f"not distinguishable from the normal return rate after {compare}."
        )
    if billing:
        pf = billing["paid_for_failed"]
        headline += (
            f" At {unit_price(price, currency)} per resolution, about {money(pf['estimate'], currency)} "
            f"({money(pf['ci_low'], currency)} to {money(pf['ci_high'], currency)}) was paid for resolutions "
            "that did not hold."
        )
    if n_handed_off:
        headline += f" {n_handed_off:,} more created a ticket themselves, so they were left out."
    headline += f" Audit confidence: {confidence['level']}."

    settings = {
        "window_days": window_days,
        "match_requested": match,
        "match": used_match,
        "price": price,
        "currency": currency,
        "reps": reps,
        "seed": seed,
        "level": level,
        "min_cell": min_cell,
        "interval": interval,
        "link_minutes": link_minutes,
        "ai_channels": list(ai_channels),
        "channel_known": channel_known,
    }
    return ResolutionAudit(
        headline=headline,
        settings=settings,
        period={
            "claims_from": eligible[S.SESSION_STARTED].min().date(),
            "claims_to": eligible[S.SESSION_STARTED].max().date(),
            "tickets_from": data_start.date(),
            "tickets_to": data_end.date(),
        },
        claims={
            "rows": claims_rows if claims_rows is not None else resolved_rows,
            "not_resolutions": not_resolutions,
            "resolutions": resolved_rows,
            "incomplete": int(incomplete.sum()),
            "handed_off": n_handed_off,
            "outside_ticket_dates": int(outside.sum()),
            "too_recent": int(too_recent.sum()),
            "checked": n_el,
            "in_period": n_period,
            "people": int(eligible[S.REQUESTER].nunique()),
            "band": _band(n_period),
        },
        returns={
            "ai_rate": point["ai"],
            "ai_ci_low": ai_lo,
            "ai_ci_high": ai_hi,
            "baseline_rate": point["base"],
            "baseline_ci_low": b_lo,
            "baseline_ci_high": b_hi,
            "baseline_tickets": n_base,
            "standardized": typed,
        },
        failed_share={
            "estimate": f,
            "ci_low": f_lo,
            "ci_high": f_hi,
            "excludes_zero": bool(f_lo > 0 or f_hi < 0),
        },
        held=held,
        billing=billing,
        by_type=by_type,
        by_month=by_month,
        sensitivity=sensitivity,
        coverage={
            "id_overlap": id_overlap,
            "type_overlap": known_share,
            "typed": typed,
        },
        confidence=confidence,
        program=program or {},
        lineage={
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code": code_version(),
            "settings_sha256": fingerprint(settings),
            "files": files or [],
            "pseudonymized": pseudonymized,
        },
        health=health or {},
        notes=notes,
        by_detail=by_detail,
    )


def _p(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x * 100:.0f}%".replace("-", "−")


def run_audit(inputs: AuditInputs, **kwargs: Any) -> ResolutionAudit:
    """Convenience wrapper: audit loaded inputs, carrying their lineage into the result."""
    result = audit_resolutions(
        inputs.claims,
        inputs.tickets,
        files=inputs.files,
        health=inputs.health,
        pseudonymized=inputs.pseudonymized,
        claims_rows=inputs.claims_rows,
        not_resolutions=inputs.not_resolutions,
        conversations=inputs.conversations,
        **kwargs,
    )
    result.notes = [*inputs.notes, *result.notes]
    return result


__all__ = [
    "AI_CHANNELS",
    "CLAIMS_PRESET",
    "LINK_MINUTES",
    "AuditError",
    "AuditInputs",
    "ResolutionAudit",
    "audit_resolutions",
    "created_in_conversation",
    "load_audit_inputs",
    "run_audit",
]
