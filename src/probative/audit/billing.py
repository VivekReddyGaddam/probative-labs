"""Bill reconciliation: was every billed item real, and what did the money buy?

AI agents billed per resolution, per conversation or per session send a bill the buyer cannot
easily check. Each billed item is matched to the buyer's own conversation log and ticket system:

* billed more than once;
* no matching conversation in the buyer's records;
* the conversation was not resolved (escalated or abandoned);
* the conversation falls outside the billing period;
* the resolution did not hold: the person opened a ticket about the same type of issue
  within the window;
* per session: more than one item billed for one person within a session (usually 24 hours);
* per conversation or session: an outcome the contract does not bill (when it says so).

Whatever the model, the reconciliation also shows what the billed items bought (resolved and
held, came back, handed to a person, abandoned) and what each resolution that held cost.

When the contract defines a resolution that way (see docs/outcome-terms.md), every item
that did not hold is disputable. Without such a definition only billing errors are, and
the resolution audit's excess over the human baseline is shown as a negotiation figure.
The output is a dispute pack: a summary for both parties and a line-by-line CSV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from probative import schema as S
from probative.audit.resolutions import AuditError, Match, audit_resolutions, choose_match, next_ticket
from probative.audit.spend import (
    ITEM_NOUNS,
    NOT_DELIVERED,
    classify_conversations,
    cost_per_held,
    parse_period,
    session_keys,
    summarize_status,
)
from probative.ingest import apply_mapping, read_table, resolve_mapping
from probative.ingest.conversations import RESOLVED, normalize_outcomes
from probative.lineage import code_version, fingerprint

BILL_PRESET: dict[str, str | list[str]] = {
    S.BILL_ITEM: [
        "item_id",
        "Conversation ID",
        "conversation_id",
        "Resolution ID",
        "resolution_id",
        "Session ID",
        "session_id",
        "Ticket ID",
        "ID",
    ],
    S.BILLED_AT: ["billed_at", "Resolved at", "Resolution date", "Date", "date", "Created", "Timestamp"],
    S.AMOUNT: ["amount", "Amount", "Charge", "Price", "Cost"],
    S.QUANTITY: [
        "quantity",
        "Quantity",
        "Count",
        "Resolutions",
        "Conversations",
        "Sessions",
        "Billed sessions",
        "Billed conversations",
        "Units",
    ],
}

REASONS = {
    "duplicate": "Billed more than once",
    "no_record": "No matching conversation in your records",
    "not_resolved": "The conversation was not resolved",
    "outside_period": "Conversation outside the billing period",
    "did_not_hold": "Did not hold under the contract's definition",
    "above_records": "More billed than recorded that day",
    "not_billable": "Outcome the contract does not bill",
    "same_session": "Billed more than once in one session",
}
ERROR_REASONS = (
    "duplicate",
    "no_record",
    "not_resolved",
    "outside_period",
    "above_records",
    "not_billable",
    "same_session",
)
SHORT = {
    "duplicate": "billed twice",
    "no_record": "with no matching conversation",
    "not_resolved": "not resolved",
    "outside_period": "outside the period",
    "above_records": "billed above your records",
    "did_not_hold": "did not hold under the contract's definition",
    "not_billable": "with an outcome the contract does not bill",
    "same_session": "billed again within one session",
}


def load_bill(path: Path, mapping: dict[str, Any] | None = None) -> tuple[pd.DataFrame, dict]:
    """Read a vendor bill: one line per billed item, or a date and a count per line."""
    raw, meta = read_table(path)
    bill, _ = apply_mapping(raw, resolve_mapping(BILL_PRESET, mapping or {}), S.BILL_COLUMNS)
    has_items = S.BILL_ITEM in bill.columns and bill[S.BILL_ITEM].notna().mean() >= 0.9
    has_counts = S.BILLED_AT in bill.columns and S.QUANTITY in bill.columns
    if not has_items and not has_counts:
        raise AuditError(
            "the bill needs an item ID on each line (for example 'Conversation ID'), or a date and a count on each "
            'line. Map yours with --bill-column item_id="Your column".'
        )
    return bill, meta.to_dict()


def period_bounds(period: str | None, bill: pd.DataFrame, conversations: pd.DataFrame):
    """The billing period as [start, end). ``period`` is YYYY-MM or YYYY-MM-DD:YYYY-MM-DD (end exclusive)."""
    bounds = parse_period(period)
    if bounds is not None:
        return bounds
    dates = bill[S.BILLED_AT].dropna() if S.BILLED_AT in bill.columns else pd.Series(dtype="datetime64[ns]")
    if dates.empty and S.SESSION_STARTED in conversations.columns:
        dates = conversations[S.SESSION_STARTED].dropna()
    if dates.empty:
        raise AuditError("give the billing period, e.g. --period 2026-09")
    start = dates.min().normalize().replace(day=1)
    end = dates.max().normalize().replace(day=1) + pd.offsets.MonthBegin(1)
    return start, end


@dataclass
class Reconciliation:
    headline: str
    settings: dict
    period: dict
    billed: dict
    reasons: list[dict]
    disputed: dict
    lines: list[dict]
    held: dict
    unbilled: dict
    audit: dict | None
    program: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    model: str = "resolution"
    paid_for: list[dict] = field(default_factory=list)
    per_held: dict | None = None
    kind: str = "bill_reconciliation"

    def to_dict(self) -> dict:
        from probative.pipeline import _clean

        return _clean(self.__dict__)


def reconcile_bill(  # noqa: C901 - one ordered story, like the other audits
    bill: pd.DataFrame,
    conversations: pd.DataFrame,
    tickets: pd.DataFrame,
    *,
    period: str | None = None,
    price: float | None = None,
    currency: str = "USD",
    window_days: int = 7,
    match: Match = "same_type",
    contractual: bool = False,
    definition_name: str = "Verified resolution",
    resolved_value: str = "resolved",
    model: str = "resolution",
    billable: str | None = None,
    session_hours: float = 24.0,
    included: int = 0,
    reps: int = 499,
    seed: int = 20261014,
    level: float = 0.95,
    program: dict | None = None,
    files: list[dict] | None = None,
) -> Reconciliation:
    """Reconcile a vendor's bill against the buyer's conversations and tickets.

    ``model`` is how the AI is billed: per ``resolution``, per ``conversation`` or per ``session``.
    ``billable`` lists the outcomes the contract bills (default: resolutions for per-resolution
    bills, everything otherwise). ``included`` items each period are free under the plan.
    """
    if model not in ITEM_NOUNS:
        raise AuditError(
            f"unknown billing model {model!r} for a bill: use resolution, conversation or session. "
            "Usage units and seats are checked with `probative usage` and `probative seats`."
        )
    noun = ITEM_NOUNS[model]
    notes: list[str] = []
    if contractual and model != "resolution":
        notes.append("A contract definition of a resolution applies to per-resolution bills only, so it was not used.")
        contractual = False
    window = pd.Timedelta(days=window_days)
    start, end = period_bounds(period, bill, conversations)
    tk = tickets.dropna(subset=[S.OPENED, S.REQUESTER])
    if tk.empty:
        raise AuditError("no tickets with a requester to check the billed resolutions against")
    tickets_end = tk[S.OPENED].max()

    conv = conversations.copy()
    has_outcome = S.SESSION_OUTCOME in conv.columns and conv[S.SESSION_OUTCOME].notna().any()
    outcome = normalize_outcomes(conv[S.SESSION_OUTCOME] if has_outcome else None, conv.index, resolved_value)
    conv["_resolved"] = outcome.eq(RESOLVED)
    if billable:
        allowed = {v.strip().lower() for v in billable.split(",") if v.strip()}
        conv["_billable"] = outcome.isin(allowed)
    else:
        conv["_billable"] = conv["_resolved"] if model == "resolution" else True
    in_period = (conv[S.SESSION_STARTED] >= start) & (conv[S.SESSION_STARTED] < end)
    # What happened to every conversation, so the bill can show what each billed item bought.
    status, status_notes = classify_conversations(
        conv, tickets, window_days=window_days, match=match, resolved_value=resolved_value
    )
    conv["_status"] = status
    if model == "session":
        conv["_session"] = session_keys(conv, session_hours)

    b = bill.reset_index(drop=True).copy()
    if period and S.BILLED_AT in b.columns and b[S.BILLED_AT].notna().any():
        inside = ((b[S.BILLED_AT] >= start) & (b[S.BILLED_AT] < end)).fillna(True)
        if (~inside).sum():
            notes.append(f"{int((~inside).sum()):,} bill lines dated outside the period were left out.")
        b = b[inside].reset_index(drop=True)
    unit = b[S.AMOUNT] if S.AMOUNT in b.columns else pd.Series(np.nan, index=b.index)
    if price is not None:
        unit = unit.fillna(price)
    if unit.isna().all():
        notes.append("The bill has no amounts and no price was given, so only counts are reconciled.")
    item_mode = S.BILL_ITEM in b.columns and b[S.BILL_ITEM].notna().mean() >= 0.9 and S.SESSION_ID in conv.columns

    lines: list[dict] = []
    counts = {k: 0 for k in REASONS}
    amounts = {k: 0.0 for k in REASONS}
    returned_count = too_recent_count = checked_count = 0
    held_ok = 0
    unbilled = 0
    if item_mode:
        b["_id"] = b[S.BILL_ITEM].astype("string").str.strip()
        c = conv.dropna(subset=[S.SESSION_ID]).copy()
        c["_id"] = c[S.SESSION_ID].astype("string").str.strip()
        c = c.drop_duplicates("_id")
        keep = ["_id", S.SESSION_STARTED, S.REQUESTER, "_resolved", "_billable", "_status"]
        keep += [S.WORK_TYPE] if S.WORK_TYPE in c.columns else []
        keep += ["_session"] if model == "session" else []
        m = b.merge(c[keep], on="_id", how="left", indicator=True)
        reason = pd.Series(pd.NA, index=m.index, dtype="string")
        reason[m["_id"].duplicated(keep="first") & m["_id"].notna()] = "duplicate"
        reason[reason.isna() & (m["_merge"] == "left_only")] = "no_record"
        if model == "resolution":
            reason[reason.isna() & ~m["_resolved"].fillna(False).astype(bool)] = "not_resolved"
        elif billable:
            reason[reason.isna() & ~m["_billable"].fillna(False).astype(bool)] = "not_billable"
        outside = (m[S.SESSION_STARTED] < start) | (m[S.SESSION_STARTED] >= end)
        reason[reason.isna() & outside.fillna(False)] = "outside_period"
        if model == "session":
            open_rows = reason.isna() & m["_session"].notna()
            again = m.loc[open_rows, "_session"].duplicated(keep="first")
            reason[again.index[again.to_numpy()]] = "same_session"
        paid = m[(m["_merge"] == "both") & ~reason.eq("duplicate").fillna(False)]
        paid_for = summarize_status(paid["_status"].astype("string"), unit.reindex(paid.index))

        candidate = reason.isna() & m["_resolved"].fillna(False).astype(bool)
        recent = candidate & (m[S.SESSION_STARTED] > tickets_end - window)
        checkable = candidate & ~recent & m[S.REQUESTER].notna()
        sub = m[checkable]
        used_match, _, match_notes, _, by = choose_match(sub, tk, match, noun="billed conversations")
        notes.extend(match_notes)
        nxt = next_ticket(sub, S.SESSION_STARTED, tk, by, window)
        back = pd.Series(pd.NaT, index=m.index, dtype="datetime64[ns]")
        back[checkable] = nxt.to_numpy()
        came_back = back.notna()
        if contractual:
            reason[checkable & came_back] = "did_not_hold"
        returned_count = int((checkable & came_back).sum())
        too_recent_count = int(recent.sum())
        checked_count = int(checkable.sum())
        held_ok = checked_count - returned_count

        for i in m.index:
            r = reason[i]
            if pd.isna(r):
                continue
            amt = float(unit[i]) if unit[i] == unit[i] else float("nan")
            counts[r] += 1
            amounts[r] += 0.0 if amt != amt else amt
            detail = ""
            if r == "did_not_hold":
                days = (back[i] - m.loc[i, S.SESSION_STARTED]).total_seconds() / 86400
                detail = f"Repeat ticket {days:.1f} days later" + (
                    ", same type of issue" if used_match == "same_type" else ""
                )
            elif r == "outside_period":
                detail = f"Conversation on {m.loc[i, S.SESSION_STARTED].date()}"
            elif r == "same_session":
                detail = f"Same person within {session_hours:g} hours of an item already billed"
            lines.append(
                {
                    "line": int(i) + 1,
                    "item_id": None if pd.isna(m.loc[i, "_id"]) else str(m.loc[i, "_id"]),
                    "billed_at": None
                    if S.BILLED_AT not in m.columns or pd.isna(m.loc[i, S.BILLED_AT])
                    else m.loc[i, S.BILLED_AT],
                    "amount": amt,
                    "reason_code": r,
                    "reason": REASONS[r],
                    "detail": detail,
                }
            )
        billed_ids = set(b["_id"].dropna())
        unbilled = int((c["_resolved"] & in_period.reindex(c.index).fillna(False) & ~c["_id"].isin(billed_ids)).sum())
    else:
        used_match = match
        notes.append(
            f"The bill has no item IDs, so it was reconciled by day: billed counts against {noun} in your records."
        )
        qty = b[S.QUANTITY].fillna(1) if S.QUANTITY in b.columns else pd.Series(1.0, index=b.index)
        day_bill = pd.DataFrame({"day": b[S.BILLED_AT].dt.normalize(), "n": qty, "amt": unit * 1.0})
        day_bill = day_bill[(day_bill["day"] >= start) & (day_bill["day"] < end)]
        per_day = day_bill.groupby("day").agg(n=("n", "sum"), unit=("amt", "mean"))
        billable_p = conv[conv["_billable"].astype(bool) & in_period]
        if model == "session":
            firsts = billable_p.dropna(subset=["_session"]).sort_values(S.SESSION_STARTED, kind="stable")
            firsts = firsts.drop_duplicates("_session")
            rec = firsts.groupby(firsts[S.SESSION_STARTED].dt.normalize()).size()
        else:
            rec = billable_p.groupby(billable_p[S.SESSION_STARTED].dt.normalize()).size()
        paid_for = summarize_status(billable_p["_status"].astype("string"))
        for day, row in per_day.iterrows():
            extra = int(max(0, row["n"] - rec.get(day, 0)))
            if extra:
                amt = extra * row["unit"] if row["unit"] == row["unit"] else float("nan")
                counts["above_records"] += extra
                amounts["above_records"] += 0.0 if amt != amt else amt
                lines.append(
                    {
                        "line": None,
                        "item_id": None,
                        "billed_at": day,
                        "amount": amt,
                        "reason_code": "above_records",
                        "reason": REASONS["above_records"],
                        "detail": f"{int(row['n'])} billed, {int(rec.get(day, 0))} recorded",
                    }
                )

    billed_items = int(b[S.QUANTITY].fillna(1).sum()) if not item_mode and S.QUANTITY in b.columns else len(b)
    billed_amount = float((unit * (b[S.QUANTITY].fillna(1) if not item_mode and S.QUANTITY in b.columns else 1)).sum())
    has_amounts = bool(unit.notna().any())
    disputed_n = sum(counts.values())
    # Plans that include some items each month charge only the rest, so an error costs money only beyond them.
    priced_here = S.AMOUNT not in b.columns or b[S.AMOUNT].isna().all()
    if included and price is not None and priced_here:
        months = max(1, round((end - start).days / 30.4))
        chargeable = max(0, billed_items - included * months)
        billed_amount = chargeable * price
        scale = min(disputed_n, chargeable) / disputed_n if disputed_n else 0.0
        amounts = {k: v * scale for k, v in amounts.items()}
        notes.append(
            f"The plan includes {included * months:,} {noun} in this period, so only the {chargeable:,} beyond "
            "that are charged, and an error costs money only when it pushes the total past the allowance."
        )
    errors = sum(counts[k] for k in ERROR_REASONS)
    error_amount = sum(amounts[k] for k in ERROR_REASONS)
    disputed_amt = sum(amounts.values())

    # The resolution audit over the period's claims: what held, the cost of each held resolution and,
    # without a contract definition, a negotiation figure.
    audit = None
    claims = conv[conv["_resolved"] & in_period].drop(
        columns=[c for c in ("_resolved", "_billable", "_status", "_session") if c in conv.columns]
    )
    try:
        res = audit_resolutions(
            claims, tickets, window_days=window_days, match=match, reps=reps, seed=seed, level=level
        ).to_dict()
        f = res["failed_share"]
        audit = {
            "failed_share": f,
            "ai_rate": res["returns"]["ai_rate"],
            "baseline_rate": res["returns"]["baseline_rate"],
            "confidence": res["confidence"]["level"],
            "checked": res["claims"]["checked"],
            "claims": res["claims"]["in_period"],
            "held": res["held"],
            "standardized": res["returns"]["standardized"],
        }
        if model == "resolution" and not contractual and billed_amount:
            base_amt = billed_amount - error_amount
            audit["negotiation"] = {
                "estimate": max(f["estimate"] or 0.0, 0.0) * base_amt,
                "ci_low": max(f["ci_low"] or 0.0, 0.0) * base_amt,
                "ci_high": max(f["ci_high"] or 0.0, 0.0) * base_amt,
            }
    except AuditError as exc:
        notes.append(f"The resolution audit could not run for this period: {exc}")
    per_held = cost_per_held(billed_amount if has_amounts else None, audit)
    for n in status_notes:
        if n not in notes:
            notes.append(n)

    from probative.report.pack import money, pct
    from probative.report.pack import unit_price as price_text

    sym_amt = (lambda x: money(x, currency)) if billed_amount == billed_amount and billed_amount else (lambda x: "")
    parts = [f"{counts[k]:,} {SHORT[k]}" for k in (*ERROR_REASONS, "did_not_hold") if counts[k]]
    headline = f"Of {billed_items:,} billed {noun}"
    headline += f" ({sym_amt(billed_amount)})" if sym_amt(billed_amount) else ""
    headline += f" for {start.date()} to {(end - pd.Timedelta(days=1)).date()}, {disputed_n:,} are disputable"
    headline += f" ({sym_amt(disputed_amt)})" if sym_amt(disputed_amt) else ""
    if parts:
        headline += ": " + "; ".join(parts)
    headline += "."
    if audit and audit.get("negotiation") and audit["failed_share"]["ci_low"] and audit["failed_share"]["ci_low"] > 0:
        n = audit["negotiation"]
        headline += (
            f" Beyond that, compared with your agents' resolutions, about {sym_amt(n['estimate'])} "
            f"({sym_amt(n['ci_low'])} to {sym_amt(n['ci_high'])}) was paid for resolutions that did not hold."
        )
    paid_total = sum(x["count"] for x in paid_for)
    not_delivered = sum(x["count"] for x in paid_for if x["code"] in NOT_DELIVERED)
    if model != "resolution" and paid_total:
        what = f"the {noun} you paid for" if item_mode else f"your records' {noun} for the period"
        headline += (
            f" Of {what}, {pct(not_delivered / paid_total)} were handed to a person, abandoned, or came back as a "
            "ticket."
        )
    if per_held:
        headline += (
            f" Each resolution that held cost {price_text(per_held['estimate'], currency)} (range "
            f"{price_text(per_held['ci_low'], currency)} to {price_text(per_held['ci_high'], currency)})."
        )
    if not item_mode:
        headline += " Reconciled by day, because the bill has no item IDs."

    settings = {
        "model": model,
        "window_days": window_days,
        "match": used_match,
        "contractual": contractual,
        "definition_name": definition_name,
        "price": price,
        "currency": currency,
        "item_level": item_mode,
        "billable": billable,
        "session_hours": session_hours if model == "session" else None,
        "included": included,
        "reps": reps,
        "seed": seed,
    }
    return Reconciliation(
        headline=headline,
        settings=settings,
        period={"start": start.date(), "end": (end - pd.Timedelta(days=1)).date()},
        billed={"count": billed_items, "amount": billed_amount if has_amounts else None},
        reasons=[
            {"code": k, "reason": REASONS[k], "count": counts[k], "amount": amounts[k]} for k in REASONS if counts[k]
        ],
        disputed={
            "count": disputed_n,
            "amount": disputed_amt if has_amounts else None,
            "billing_errors": errors,
            "billing_error_amount": error_amount if has_amounts else None,
            "billing_error_share": errors / billed_items if billed_items else None,
        },
        lines=lines,
        held={
            "checked": checked_count,
            "came_back": returned_count,
            "held": held_ok,
            "too_recent": too_recent_count,
        },
        unbilled={"resolved_not_billed": unbilled},
        audit=audit,
        program=program or {},
        lineage={
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code": code_version(),
            "settings_sha256": fingerprint(settings),
            "files": files or [],
        },
        notes=notes,
        model=model,
        paid_for=paid_for,
        per_held=per_held,
    )
