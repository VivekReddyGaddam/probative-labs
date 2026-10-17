"""Spend checks: whatever the billing model, what was paid and what held.

AI for service desks is billed in different units: per resolution, per conversation, per
session, by usage units (assists, credits, actions) or per seat. Each model hides waste in a
different place, but every one of them can be brought to the same measure: what each
resolution that held cost. "Held" comes from the resolution audit: the AI's claimed
resolutions, less the share that came back beyond the normal rate after your agents' own
resolutions.

Bills itemized per resolution, conversation or session are reconciled line by line in
``probative.audit.billing``. This module adds what those share (the status of each
conversation, billing sessions, the cost per held resolution) and the two models without
line items: usage units and seats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from probative import schema as S
from probative.audit.resolutions import AuditError, Match, audit_resolutions, choose_match, next_ticket
from probative.ingest import apply_mapping, read_table, resolve_mapping
from probative.ingest.conversations import RESOLVED, normalize_outcomes
from probative.lineage import code_version, fingerprint

MODELS = {
    "resolution": "Per resolution",
    "conversation": "Per conversation",
    "session": "Per session",
    "usage": "Usage units (assists, credits or actions)",
    "seat": "Per seat or per employee",
}
ITEM_NOUNS = {"resolution": "resolutions", "conversation": "conversations", "session": "sessions"}

STATUS = {
    "held": "Resolved, and the person did not come back",
    "came_back": "Resolved, but the person came back",
    "too_recent": "Resolved, too recent to check",
    "no_person": "Resolved, person not recorded",
    "escalated": "Handed to a person",
    "abandoned": "Abandoned or unanswered",
    "open": "Still open",
    "other": "Other outcome",
}
NOT_DELIVERED = ("came_back", "escalated", "abandoned")

# Usage exports: ServiceNow's Gen AI usage log (sys_gen_ai_usage_log), Copilot Studio and
# Agentforce credit reports, or any daily usage CSV. First match wins.
USAGE_PRESET: dict[str, str | list[str]] = {
    S.USED_AT: [
        "used_at",
        "sys_created_on",
        "Created",
        "Date",
        "date",
        "Usage date",
        "Day",
        "Timestamp",
        "Billing date",
        "Month",
    ],
    S.FEATURE: [
        "feature",
        "Feature",
        "skill_config_id",
        "Skill Config ID",
        "skill_config",
        "Skill config",
        "Skill/Agent Name",
        "skill",
        "Skill",
        "Agent name",
        "Agent",
        "Feature type",
        "Action",
        "Meter",
        "Capability",
        "Product",
    ],
    S.UNITS: [
        "units",
        "Units",
        "assists",
        "Assists",
        "Total assists",
        "Billed Copilot Credits",
        "Copilot Credits",
        "Billed credits",
        "Flex Credits",
        "Credits",
        "Consumed",
        "Quantity",
    ],
    S.ACTIONS: ["actions", "Actions", "Action count", "Executions"],
    S.UNIT_RATIO: ["unit_ratio", "assist_ratio", "Assist ratio", "Credits per action"],
    S.ENVIRONMENT: ["environment", "Environment", "instance", "Instance", "Environment name", "Org", "Tenant"],
    S.SESSION_ID: [
        "session_id",
        "Session ID",
        "conversation_id",
        "Conversation ID",
        "document_id",
        "Document",
        "Source record",
    ],
    S.USER: ["user_id", "user", "User", "User ID", "Email"],
}

NONPROD = re.compile(r"(?:dev|test|uat|qa|sandbox|sbx|stag|sub.?prod|non.?prod|train|demo|clone)", re.IGNORECASE)


# --- Shared pieces ------------------------------------------------------------------------------------


def parse_period(period: str | None) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """``YYYY-MM`` or ``YYYY-MM-DD:YYYY-MM-DD`` (end exclusive) as [start, end)."""
    if not period:
        return None
    if ":" in period:
        a, b = period.split(":", 1)
        return pd.Timestamp(a), pd.Timestamp(b)
    start = pd.Timestamp(f"{period}-01")
    return start, start + pd.offsets.MonthBegin(1)


def months_covering(dates: pd.Series) -> tuple[pd.Timestamp, pd.Timestamp]:
    d = dates.dropna()
    if d.empty:
        raise AuditError("give the period to check, e.g. --period 2026-09")
    return d.min().normalize().replace(day=1), d.max().normalize().replace(day=1) + pd.offsets.MonthBegin(1)


def latest_full_month(conversations: pd.DataFrame | None, tickets: pd.DataFrame | None, window_days: int):
    """The latest month whose conversations can all be checked for returns."""
    if conversations is None or conversations.empty:
        raise AuditError("give the period to check, e.g. --period 2026-09")
    last = conversations[S.SESSION_STARTED].max()
    if tickets is not None and not tickets.empty:
        last = min(last, tickets[S.OPENED].max() - pd.Timedelta(days=window_days))
    month = last.normalize().replace(day=1)
    following = month + pd.offsets.MonthBegin(1)
    if last >= following - pd.Timedelta(days=1):  # the month holding ``last`` is complete
        return month, following
    return month - pd.offsets.MonthBegin(1), month


def classify_conversations(
    conv: pd.DataFrame,
    tickets: pd.DataFrame,
    *,
    window_days: int = 7,
    match: Match = "same_type",
    resolved_value: str = "resolved",
) -> tuple[pd.Series, list[str]]:
    """What happened to each conversation, as one of the STATUS codes, and notes on matching."""
    has_outcome = S.SESSION_OUTCOME in conv.columns and conv[S.SESSION_OUTCOME].notna().any()
    outcome = normalize_outcomes(conv[S.SESSION_OUTCOME] if has_outcome else None, conv.index, resolved_value)
    status = outcome.astype("string").copy()
    resolved = outcome.eq(RESOLVED)
    status[resolved] = "held"
    no_time = conv[S.SESSION_STARTED].isna()
    status[no_time] = "other"
    no_person = resolved & ~no_time & conv[S.REQUESTER].isna()
    status[no_person] = "no_person"
    tk = tickets.dropna(subset=[S.OPENED, S.REQUESTER])
    if tk.empty:
        raise AuditError("no tickets with a requester to check the conversations against")
    window = pd.Timedelta(days=window_days)
    recent = resolved & ~no_time & ~no_person & (conv[S.SESSION_STARTED] > tk[S.OPENED].max() - window)
    status[recent] = "too_recent"
    check = resolved & ~no_time & ~no_person & ~recent
    notes: list[str] = []
    if check.any():
        sub = conv[check]
        _, _, notes, _, by = choose_match(sub, tk, match, noun="conversations")
        back = next_ticket(sub, S.SESSION_STARTED, tk, by, window)
        status[back.index[back.notna().to_numpy()]] = "came_back"
    return status, notes


def summarize_status(status: pd.Series, amounts: pd.Series | None = None) -> list[dict]:
    """Counts (and amounts) by status, in STATUS order."""
    out = []
    total = len(status)
    for code, label in STATUS.items():
        hit = status.eq(code)
        n = int(hit.sum())
        if not n:
            continue
        row = {"code": code, "label": label, "count": n, "share": n / total if total else None}
        if amounts is not None:
            row["amount"] = float(amounts[hit].sum())
        out.append(row)
    return out


def session_keys(conv: pd.DataFrame, hours: float = 24.0) -> pd.Series:
    """The billing session each conversation falls in: one person's conversations within ``hours`` of the
    session's first conversation count as one session (how per-session billing usually counts)."""
    keys = pd.Series(pd.NA, index=conv.index, dtype="string")
    ok = conv[S.REQUESTER].notna() & conv[S.SESSION_STARTED].notna()
    d = conv.loc[ok, [S.REQUESTER, S.SESSION_STARTED]].sort_values([S.REQUESTER, S.SESSION_STARTED], kind="stable")
    span = np.timedelta64(int(hours * 3600), "s")
    req = d[S.REQUESTER].astype(str).to_numpy()
    ts = d[S.SESSION_STARTED].to_numpy(dtype="datetime64[ns]")
    out = np.empty(len(d), dtype=object)
    cur_req: str | None = None
    cur_start = np.datetime64("NaT")
    for i in range(len(d)):
        if req[i] != cur_req or ts[i] - cur_start >= span:
            cur_req, cur_start = req[i], ts[i]
        out[i] = f"{cur_req}|{cur_start}"
    keys[d.index] = out
    return keys


def period_audit(
    conv: pd.DataFrame,
    tickets: pd.DataFrame,
    *,
    window_days: int,
    match: Match,
    resolved_value: str,
    reps: int,
    seed: int,
    level: float,
) -> tuple[dict | None, list[str]]:
    """The resolution audit of a period's claimed resolutions: the share that did not hold, and how many held."""
    has_outcome = S.SESSION_OUTCOME in conv.columns and conv[S.SESSION_OUTCOME].notna().any()
    resolved = normalize_outcomes(conv[S.SESSION_OUTCOME] if has_outcome else None, conv.index, resolved_value)
    claims = conv[resolved.eq(RESOLVED)]
    if claims.empty:
        return None, ["The AI claimed no resolutions in this period."]
    try:
        res = audit_resolutions(
            claims, tickets, window_days=window_days, match=match, reps=reps, seed=seed, level=level
        ).to_dict()
    except AuditError as exc:
        return None, [f"The resolution audit could not run for this period: {exc}"]
    return {
        "failed_share": res["failed_share"],
        "held": res["held"],
        "ai_rate": res["returns"]["ai_rate"],
        "baseline_rate": res["returns"]["baseline_rate"],
        "confidence": res["confidence"]["level"],
        "checked": res["claims"]["checked"],
        "claims": res["claims"]["in_period"],
        "by_detail": res.get("by_detail") or [],
        "standardized": res["returns"]["standardized"],
    }, []


def cost_per_held(spend: float | None, audit: dict | None) -> dict | None:
    """What each resolution that held cost, with the range from the audit's held estimate."""
    if spend is None or spend != spend or not audit or not audit.get("held"):
        return None
    h = audit["held"]
    if not h.get("estimate"):
        return None

    def per(n: float | None) -> float | None:
        return spend / n if n else None

    return {
        "estimate": per(h["estimate"]),
        "ci_low": per(h.get("ci_high")),
        "ci_high": per(h.get("ci_low")),
        "spend": spend,
        "held": h["estimate"],
        "held_low": h.get("ci_low"),
        "held_high": h.get("ci_high"),
        "claims": h.get("of"),
    }


def in_period(conv: pd.DataFrame | None, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
    if conv is None:
        return None
    return conv[(conv[S.SESSION_STARTED] >= start) & (conv[S.SESSION_STARTED] < end)]


@dataclass
class SpendCheck:
    model: str
    headline: str
    settings: dict
    period: dict
    spend: dict
    outcomes: list[dict]
    audit: dict | None
    per_held: dict | None
    review: list[dict]
    attention: list[str]
    usage: dict | None = None
    pool: dict | None = None
    seats: dict | None = None
    program: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    kind: str = "spend_check"

    def to_dict(self) -> dict:
        from probative.pipeline import _clean

        return _clean(self.__dict__)


def _lineage(settings: dict, files: list[dict] | None) -> dict:
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": code_version(),
        "settings_sha256": fingerprint(settings),
        "files": files or [],
    }


def _outcomes_and_audit(conv_p, tickets, *, window_days, match, resolved_value, reps, seed, level, notes):
    if conv_p is None or tickets is None:
        notes.append("No conversations and tickets were given, so what the AI delivered was not checked.")
        return [], None, None
    if conv_p.empty:
        notes.append("The AI has no conversations in this period.")
        return [], None, None
    status, match_notes = classify_conversations(
        conv_p, tickets, window_days=window_days, match=match, resolved_value=resolved_value
    )
    notes.extend(match_notes)
    audit, audit_notes = period_audit(
        conv_p,
        tickets,
        window_days=window_days,
        match=match,
        resolved_value=resolved_value,
        reps=reps,
        seed=seed,
        level=level,
    )
    notes.extend(audit_notes)
    return summarize_status(status), status, audit


# --- Usage units: assists, credits, actions -------------------------------------------------------------


def load_usage(
    paths: list[Path],
    *,
    nonprod: list[Path] | None = None,
    mapping: dict[str, Any] | None = None,
    pseudonymizer=None,
) -> tuple[pd.DataFrame, list[dict], list[str]]:
    """Read usage exports. Files passed as ``nonprod`` are usage from test or development instances."""
    frames, files, notes = [], [], []
    for path, is_nonprod in [*[(p, False) for p in paths], *[(p, True) for p in nonprod or []]]:
        raw, meta = read_table(path)
        u, _ = apply_mapping(raw, resolve_mapping(USAGE_PRESET, mapping or {}), S.USAGE_COLUMNS, required=(S.USED_AT,))
        if S.UNITS not in u.columns or u[S.UNITS].isna().all():
            if S.ACTIONS in u.columns and S.UNIT_RATIO in u.columns and u[S.ACTIONS].notna().any():
                u[S.UNITS] = u[S.ACTIONS] * u[S.UNIT_RATIO]
            elif S.ACTIONS in u.columns and u[S.ACTIONS].notna().any():
                u[S.UNITS] = u[S.ACTIONS]
                notes.append(f"{Path(path).name} records actions but no units, so each action counted as one unit.")
            else:
                raise AuditError(
                    f"{Path(path).name} needs a units column (for example Assists or Credits), or actions. "
                    'Map yours with --usage-column units="Your column".'
                )
        if is_nonprod and (S.ENVIRONMENT not in u.columns or u[S.ENVIRONMENT].isna().all()):
            u[S.ENVIRONMENT] = Path(path).stem
        env = u[S.ENVIRONMENT] if S.ENVIRONMENT in u.columns else pd.Series(pd.NA, index=u.index, dtype="string")
        u["_nonprod"] = is_nonprod | env.astype("string").str.contains(NONPROD).fillna(False).astype(bool)
        frames.append(u)
        files.append(meta.to_dict())
    usage = pd.concat(frames, ignore_index=True)
    if pseudonymizer is not None and S.USER in usage.columns:
        usage = pseudonymizer.apply(usage, (S.USER,))
    return usage, files, notes


def _pool_forecast(
    usage: pd.DataFrame,
    *,
    pool_units: float,
    price: float | None,
    overage_price: float | None,
    term_start: pd.Timestamp,
    term_months: int,
    end: pd.Timestamp,
    unit_name: str,
) -> dict:
    term_end = term_start + pd.DateOffset(months=term_months)
    used = usage[(usage[S.USED_AT] >= term_start) & (usage[S.USED_AT] < min(end, term_end))]
    last = min(used[S.USED_AT].max() if not used.empty else term_start, end - pd.Timedelta(seconds=1)).normalize()
    first = max(term_start, used[S.USED_AT].min().normalize()) if not used.empty else term_start
    recent_from = max(first, last - pd.Timedelta(days=29))
    recent = used[used[S.USED_AT] >= recent_from][S.UNITS].sum()
    days = max(1, (last - recent_from).days + 1)
    rate = float(recent) / days
    used_units = float(used[S.UNITS].sum())
    remaining = pool_units - used_units
    days_left = max(0, (term_end - last).days - 1)
    projected = rate * days_left
    out: dict[str, Any] = {
        "units": pool_units,
        "unit_name": unit_name,
        "term_start": term_start.date(),
        "term_end": (term_end - pd.Timedelta(days=1)).date(),
        "used": used_units,
        "used_share": used_units / pool_units if pool_units else None,
        "through": last.date(),
        "daily_rate": rate,
        "remaining": remaining,
        "runs_out_on": None,
        "overage_units": 0.0,
        "overage_cost": None,
        "unused_units": 0.0,
        "unused_value": None,
        "data_from": first.date(),
    }
    if remaining <= 0:
        out["runs_out_on"] = "used up"
        out["overage_units"] = -remaining + projected
    elif projected > remaining and rate > 0:
        out["runs_out_on"] = (last + pd.Timedelta(days=remaining / rate)).date()
        out["overage_units"] = projected - remaining
    else:
        out["unused_units"] = remaining - projected
    rate_for_overage = overage_price if overage_price is not None else price
    if out["overage_units"] and rate_for_overage is not None:
        out["overage_cost"] = out["overage_units"] * rate_for_overage
    if out["unused_units"] and price is not None:
        out["unused_value"] = out["unused_units"] * price
    return out


def check_usage(  # noqa: C901 - one ordered story, like the other audits
    usage: pd.DataFrame,
    conversations: pd.DataFrame | None = None,
    tickets: pd.DataFrame | None = None,
    *,
    period: str | None = None,
    unit_name: str = "units",
    unit_price: float | None = None,
    pool_units: float | None = None,
    pool_price: float | None = None,
    term_start: str | None = None,
    term_months: int = 12,
    overage_price: float | None = None,
    currency: str = "USD",
    window_days: int = 7,
    match: Match = "same_type",
    resolved_value: str = "resolved",
    reps: int = 499,
    seed: int = 20261014,
    level: float = 0.95,
    program: dict | None = None,
    files: list[dict] | None = None,
    notes: list[str] | None = None,
) -> SpendCheck:
    """Where usage-priced AI spent its units, what the conversations it ran delivered, and how the pool is going."""
    from probative.report.pack import money, num, pct
    from probative.report.pack import unit_price as price_text

    notes = list(notes or [])
    u = usage.dropna(subset=[S.USED_AT, S.UNITS])
    if u.empty:
        raise AuditError("the usage export has no rows with a date and a number of units")
    start, end = parse_period(period) or months_covering(u[S.USED_AT])
    up = u[(u[S.USED_AT] >= start) & (u[S.USED_AT] < end)].copy()
    if up.empty:
        raise AuditError(f"the usage export has no rows from {start.date()} to {(end - pd.Timedelta(days=1)).date()}")
    price = unit_price if unit_price is not None else (pool_price / pool_units if pool_price and pool_units else None)
    if price is None:
        notes.append("No price per unit or pool price was given, so units are reported without amounts.")
    cost = (lambda x: x * price) if price is not None else (lambda x: None)
    total = float(up[S.UNITS].sum())
    nonprod = float(up.loc[up["_nonprod"], S.UNITS].sum())
    prod = total - nonprod

    feature = up[S.FEATURE].astype("string").fillna("(not recorded)") if S.FEATURE in up.columns else None
    by_feature: list[dict] = []
    if feature is not None:
        table = (
            pd.DataFrame({"f": feature, "units": up[S.UNITS], "nonprod": up[S.UNITS].where(up["_nonprod"], 0.0)})
            .groupby("f")[["units", "nonprod"]]
            .sum()
            .sort_values("units", ascending=False)
        )
        for name, row in table.head(12).iterrows():
            by_feature.append(
                {
                    "feature": str(name),
                    "units": float(row["units"]),
                    "share": float(row["units"]) / total if total else None,
                    "amount": cost(float(row["units"])),
                    "nonprod_units": float(row["nonprod"]),
                }
            )
        if len(table) > 12:
            rest = table.iloc[12:]
            by_feature.append(
                {
                    "feature": f"({len(rest)} other features)",
                    "units": float(rest["units"].sum()),
                    "share": float(rest["units"].sum()) / total if total else None,
                    "amount": cost(float(rest["units"].sum())),
                    "nonprod_units": float(rest["nonprod"].sum()),
                }
            )
    environments: list[dict] = []
    if S.ENVIRONMENT in up.columns and up[S.ENVIRONMENT].notna().any():
        envs = up.assign(_e=up[S.ENVIRONMENT].astype("string").fillna("(not recorded)")).groupby("_e")
        for name, grp in envs:
            environments.append(
                {
                    "environment": str(name),
                    "units": float(grp[S.UNITS].sum()),
                    "non_production": bool(grp["_nonprod"].all()),
                }
            )
        environments.sort(key=lambda e: -e["units"])

    # What the AI's conversations in the period delivered, and the units each kind of outcome used.
    conv_p = in_period(conversations, start, end)
    outcomes, status, audit = _outcomes_and_audit(
        conv_p,
        tickets,
        window_days=window_days,
        match=match,
        resolved_value=resolved_value,
        reps=reps,
        seed=seed,
        level=level,
        notes=notes,
    )
    linked_units = 0.0
    linked: list[dict] = []
    if status is not None and S.SESSION_ID in up.columns and S.SESSION_ID in conv_p.columns:
        ids = conv_p[S.SESSION_ID].astype("string").str.strip()
        by_id = pd.Series(status.to_numpy(), index=ids.to_numpy())
        by_id = by_id[~by_id.index.duplicated()]
        pu = up[~up["_nonprod"]].copy()
        pu["_status"] = pu[S.SESSION_ID].astype("string").str.strip().map(by_id)
        hit = pu["_status"].notna()
        linked_units = float(pu.loc[hit, S.UNITS].sum())
        for code, label in STATUS.items():
            n = float(pu.loc[pu["_status"].eq(code), S.UNITS].sum())
            if n:
                linked.append({"code": code, "label": label, "units": n, "amount": cost(n)})
    if status is not None and not linked_units:
        notes.append(
            "The usage export does not link units to conversations, so the cost per resolution that held divides "
            "all production units by the resolutions that held: an upper bound, because some units go to features "
            "for your agents (their value is measured by the evidence pack)."
        )

    pool = None
    if pool_units:
        if term_start:
            pool = _pool_forecast(
                u,
                pool_units=pool_units,
                price=price,
                overage_price=overage_price,
                term_start=pd.Timestamp(term_start),
                term_months=term_months,
                end=end,
                unit_name=unit_name,
            )
        else:
            notes.append("Give the contract start (--term-start) to forecast when the pool runs out.")

    basis_units = linked_units or prod
    per_held = cost_per_held(cost(basis_units), audit)
    if per_held is not None:
        per_held["basis"] = "conversation units" if linked_units else "all production units (upper bound)"

    # What to raise with the vendor, or fix at home.
    review: list[dict] = []
    attention: list[str] = []
    if nonprod:
        names = ", ".join(e["environment"] for e in environments if e["non_production"]) or "test instances"
        review.append(
            {
                "code": "non_production",
                "title": f"{unit_name.capitalize()} used outside production",
                "units": nonprod,
                "amount": cost(nonprod),
                "detail": f"{pct(nonprod / total, 1)} of the period's {unit_name} ran in {names}. Ask whether testing "
                "should draw from the production pool, and test with smaller samples.",
            }
        )
        if nonprod / total >= 0.10:
            attention.append(f"{pct(nonprod / total)} of {unit_name} were used outside production")
    wasted = sum(x["units"] for x in linked if x["code"] in NOT_DELIVERED)
    if wasted:
        review.append(
            {
                "code": "not_delivered",
                "title": f"{unit_name.capitalize()} spent on conversations that did not resolve",
                "units": wasted,
                "amount": cost(wasted),
                "detail": f"{pct(wasted / linked_units, 0)} of the {unit_name} linked to conversations went to ones "
                "that were handed to a person, abandoned, or came back as a ticket.",
            }
        )
    if pool:
        if pool["overage_units"]:
            when = "is already used up" if pool["runs_out_on"] == "used up" else f"runs out on {pool['runs_out_on']}"
            review.append(
                {
                    "code": "pool_overage",
                    "title": "The pool runs out before the term ends",
                    "units": pool["overage_units"],
                    "amount": pool["overage_cost"],
                    "detail": f"At the last 30 days' pace ({num(pool['daily_rate'], 0)} a day), the "
                    f"{num(pool_units, 0)}-{unit_name} pool {when}, before the term ends on {pool['term_end']}.",
                }
            )
            attention.append(f"the {unit_name} pool {when}")
        elif pool["unused_units"] and pool["unused_units"] >= 0.25 * pool_units:
            review.append(
                {
                    "code": "pool_unused",
                    "title": "Paid for, and unlikely to be used",
                    "units": pool["unused_units"],
                    "amount": pool["unused_value"],
                    "detail": f"At the last 30 days' pace, {pct(pool['unused_units'] / pool_units)} of the pool will "
                    "be left at the end of the term. Size the next pool on measured use.",
                }
            )
            attention.append(f"{pct(pool['unused_units'] / pool_units)} of the pool will go unused")

    label_period = f"{start.date()} to {(end - pd.Timedelta(days=1)).date()}"
    headline = f"From {label_period}, the AI used {num(total, 0)} {unit_name}"
    headline += f" ({money(cost(total), currency)})" if price is not None else ""
    if nonprod:
        headline += f", {pct(nonprod / total)} of them outside production"
    headline += "."
    if wasted and linked_units:
        headline += (
            f" Of the {unit_name} its conversations used, {pct(wasted / linked_units)} went to conversations that "
            "were handed to a person, abandoned or came back as a ticket."
        )
    if per_held:
        headline += (
            f" Each resolution that held cost {price_text(per_held['estimate'], currency)} "
            f"(range {price_text(per_held['ci_low'], currency)} to {price_text(per_held['ci_high'], currency)})"
            + (", counting all production units." if not linked_units else ".")
        )
    if pool and pool["overage_units"]:
        when = "is already used up" if pool["runs_out_on"] == "used up" else f"runs out on {pool['runs_out_on']}"
        headline += f" At this pace the pool {when}, before the term ends."
    elif pool and pool["unused_units"] >= 0.25 * pool_units:
        headline += f" At this pace {pct(pool['unused_units'] / pool_units)} of the pool will be left unused."

    settings = {
        "model": "usage",
        "unit_name": unit_name,
        "unit_price": price,
        "pool_units": pool_units,
        "pool_price": pool_price,
        "term_start": term_start,
        "term_months": term_months,
        "overage_price": overage_price,
        "currency": currency,
        "window_days": window_days,
        "match": match,
        "reps": reps,
        "seed": seed,
    }
    return SpendCheck(
        model="usage",
        headline=headline,
        settings=settings,
        period={"start": start.date(), "end": (end - pd.Timedelta(days=1)).date()},
        spend={
            "amount": cost(total),
            "currency": currency,
            "basis": f"{num(total, 0)} {unit_name} × {price_text(price, currency)}" if price is not None else None,
        },
        outcomes=outcomes,
        audit=audit,
        per_held=per_held,
        review=review,
        attention=attention,
        usage={
            "unit_name": unit_name,
            "total": total,
            "production": prod,
            "non_production": nonprod,
            "by_feature": by_feature,
            "environments": environments,
            "linked_units": linked_units,
            "linked": linked,
        },
        pool=pool,
        program=program or {},
        lineage=_lineage(settings, files),
        notes=notes,
    )


# --- Seats: per user, per agent or per employee ---------------------------------------------------------


def check_seats(  # noqa: C901 - one ordered story
    conversations: pd.DataFrame | None = None,
    tickets: pd.DataFrame | None = None,
    *,
    fee: float | None = None,
    fee_per_seat: float | None = None,
    seats: int | None = None,
    term_start: str | None = None,
    term_months: int = 12,
    period: str | None = None,
    usage: pd.DataFrame | None = None,
    seat_label: str = "licensed users",
    currency: str = "USD",
    window_days: int = 7,
    match: Match = "same_type",
    resolved_value: str = "resolved",
    reps: int = 499,
    seed: int = 20261014,
    level: float = 0.95,
    program: dict | None = None,
    files: list[dict] | None = None,
    notes: list[str] | None = None,
) -> SpendCheck:
    """A flat fee per seat or employee: what a period cost, who used it, and what each held resolution cost."""
    from probative.report.pack import money, num, pct
    from probative.report.pack import unit_price as price_text

    notes = list(notes or [])
    if fee is None and fee_per_seat is not None and seats:
        fee = fee_per_seat * seats
    if fee is None:
        raise AuditError("give the fee for the term (--fee), or the price per seat (--fee-per-seat) and --seats")
    bounds = parse_period(period)
    if bounds is None:
        bounds = latest_full_month(conversations, tickets, window_days)
        notes.append(f"No period was given, so the latest month that can be fully checked was used: {bounds[0]:%Y-%m}.")
    start, end = bounds
    if term_start:
        t0 = pd.Timestamp(term_start)
        term_days = ((t0 + pd.DateOffset(months=term_months)) - t0).days
    else:
        term_days = 365.25 * term_months / 12
    days = (end - start).days
    spend = fee * days / term_days

    conv_p = in_period(conversations, start, end)
    active = None
    source = None
    if usage is not None and S.USER in usage.columns and usage[S.USER].notna().any():
        test = usage["_nonprod"] if "_nonprod" in usage.columns else pd.Series(False, index=usage.index)
        up = usage[(usage[S.USED_AT] >= start) & (usage[S.USED_AT] < end) & ~test.astype(bool)]
        active, source = int(up[S.USER].nunique()), "usage log"
    elif conv_p is not None and not conv_p.empty:
        active, source = int(conv_p[S.REQUESTER].nunique()), "people with a conversation"
    seats_info = None
    review: list[dict] = []
    attention: list[str] = []
    if seats:
        util = active / seats if active is not None else None
        idle = max(0, seats - (active or 0)) if active is not None else None
        seats_info = {
            "seats": seats,
            "label": seat_label,
            "active": active,
            "active_source": source,
            "utilization": util,
            "idle": idle,
            "idle_cost": spend * idle / seats if idle is not None else None,
        }
        if idle:
            review.append(
                {
                    "code": "idle_seats",
                    "title": f"Paid for, not used: {num(idle, 0)} of {num(seats, 0)} {seat_label}",
                    "units": idle,
                    "amount": seats_info["idle_cost"],
                    "detail": f"{pct(util)} of {seat_label} used it in the period ({source}). Count active users "
                    "before the next renewal, or move to a price that follows use.",
                }
            )
            if util is not None and util < 0.5:
                attention.append(f"only {pct(util)} of {seat_label} used it")

    outcomes, _, audit = _outcomes_and_audit(
        conv_p,
        tickets,
        window_days=window_days,
        match=match,
        resolved_value=resolved_value,
        reps=reps,
        seed=seed,
        level=level,
        notes=notes,
    )
    per_held = cost_per_held(spend, audit)
    if per_held is not None:
        per_held["basis"] = "the period's share of the fee"
    if conversations is None:
        notes.append(
            "For seats used by your agents (assist, summaries, drafting), the value is the time saved per ticket: "
            "measure it with the evidence pack and compare it with this cost."
        )

    label_period = f"{start.date()} to {(end - pd.Timedelta(days=1)).date()}"
    basis = (
        f"{days} days of a {money(fee, currency)} fee"
        + (f" for {num(seats, 0)} {seat_label}" if seats else "")
        + (f" from {term_start}" if term_start else "")
    )
    headline = f"From {label_period}, the AI cost about {money(spend, currency)} ({basis})."
    if seats_info and seats_info["active"] is not None:
        used = pct(seats_info["utilization"])
        headline += f" {num(seats_info['active'], 0)} of {num(seats, 0)} {seat_label} used it ({used})."
    if per_held and audit:
        headline += (
            f" It claimed {num(audit['claims'], 0)} resolutions and about {num(per_held['held'], 0)} held, so each "
            f"resolution that held cost {price_text(per_held['estimate'], currency)} (range "
            f"{price_text(per_held['ci_low'], currency)} to {price_text(per_held['ci_high'], currency)})."
        )

    settings = {
        "model": "seat",
        "fee": fee,
        "fee_per_seat": fee_per_seat,
        "seats": seats,
        "seat_label": seat_label,
        "term_start": term_start,
        "term_months": term_months,
        "currency": currency,
        "window_days": window_days,
        "match": match,
        "reps": reps,
        "seed": seed,
    }
    return SpendCheck(
        model="seat",
        headline=headline,
        settings=settings,
        period={"start": start.date(), "end": (end - pd.Timedelta(days=1)).date(), "days": days},
        spend={"amount": spend, "currency": currency, "basis": basis},
        outcomes=outcomes,
        audit=audit,
        per_held=per_held,
        review=review,
        attention=attention,
        seats=seats_info,
        program=program or {},
        lineage=_lineage(settings, files),
        notes=notes,
    )
