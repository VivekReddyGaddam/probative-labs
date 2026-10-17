"""Conversation exports: what a virtual agent or AI agent says it did.

A conversation preset maps an export onto the canonical session columns. Where a platform
records how a conversation ended across several fields, the preset derives one outcome
(resolved, escalated or abandoned) and keeps the platform's own words as the outcome detail,
so a report can show exactly what the platform counted as resolved.

Outcome values that are not already canonical are sorted by their wording: values passed as
"resolved" count as resolutions, wording about hand-offs counts as escalated, and wording about
silence or abandonment counts as abandoned.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

import pandas as pd

from probative import schema as S
from probative.ingest.mapping import MappingError, PresetEntry, apply_mapping, find_column, resolve_mapping

# Any conversation or "automated resolution" export. First match wins.
GENERIC: dict[str, PresetEntry] = {
    S.SESSION_ID: ["session_id", "Session ID", "Conversation ID", "conversation_id", "number", "ID"],
    S.REQUESTER: [
        "requester_id",
        "Requester ID",
        "Requester",
        "User ID",
        "User",
        "caller_id",
        "Caller",
        "opened_for",
        "Opened for",
        "Requester Email",
        "User Email",
        "Email",
    ],
    S.SESSION_STARTED: [
        "started_at",
        "Started",
        "Start time",
        "opened_at",
        "Opened",
        "Created",
        "created_at",
        "Created at",
        "Timestamp",
    ],
    S.SESSION_OUTCOME: ["outcome", "Outcome", "Resolution state", "Deflection outcome", "Status"],
    S.OUTCOME_DETAIL: ["outcome_detail", "Outcome detail", "state_reason", "State reason"],
    S.CATEGORY: ["category", "Category", "Topic"],
    S.SUBCATEGORY: ["subcategory", "Subcategory", "Sub-Category"],
}

# ServiceNow Virtual Agent conversations, exported from the Interactions table (interaction.list)
# filtered to Virtual agent = true. CSV exports use field names; Excel exports use labels.
SERVICENOW_VA: dict[str, PresetEntry] = {
    S.SESSION_ID: ["number", "Number", "sys_id"],
    S.REQUESTER: ["opened_for", "Opened for", "opened_for.email", "opened_for.user_name", "consumer", "Consumer"],
    S.SESSION_STARTED: ["opened_at", "Opened", "sys_created_on", "Created"],
    S.SESSION_OUTCOME: ["outcome"],  # derived from state, state reason and agent chat below
    S.OUTCOME_DETAIL: ["outcome_detail"],
    S.CATEGORY: ["u_category", "category", "Category"],
    S.SUBCATEGORY: ["u_subcategory", "subcategory", "Subcategory"],
}

RESOLVED, ESCALATED, ABANDONED, OPEN, OTHER = "resolved", "escalated", "abandoned", "open", "other"


def _norm(s: pd.Series) -> pd.Series:
    """Lower-case words, so 'closed_complete' and 'Closed Complete' compare equal."""
    return s.astype("string").str.strip().str.lower().str.replace(r"[_\s]+", " ", regex=True).fillna("")


def _truthy(s: pd.Series) -> pd.Series:
    return _norm(s).isin({"true", "1", "yes", "y", "t"})


def _display(s: pd.Series) -> pd.Series:
    """The platform's wording, made readable when an export gives raw choice values."""
    text = s.astype("string").str.strip()
    raw = text.str.fullmatch(r"[a-z0-9_]+").fillna(False)
    pretty = text.str.replace("_", " ", regex=False).str.capitalize()
    return text.where(~raw, pretty)


def _servicenow_va(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Keep Virtual Agent conversations and derive each one's outcome from how ServiceNow closed it.

    A conversation the virtual agent closed without a live agent ("Closed Complete") is what
    ServiceNow's own reporting counts as handled, including those closed because the person
    stopped responding. A live agent in the chat means escalated; "Closed Abandoned" and errors
    mean abandoned.
    """
    notes: list[str] = []
    df = raw.copy()
    va = find_column(df.columns, ["virtual_agent", "Virtual agent"])
    if va is not None:
        keep = _truthy(df[va])
        if (~keep).sum():
            notes.append(f"{int((~keep).sum()):,} interactions without the Virtual agent flag were left out.")
        df = df[keep]
    kind = find_column(df.columns, ["type", "Type"])
    if kind is not None:
        chat = _norm(df[kind]).isin({"chat", ""})
        if (~chat).sum():
            notes.append(f"{int((~chat).sum()):,} interactions that were not chats were left out.")
        df = df[chat]
    state = find_column(df.columns, ["state", "State"])
    if state is None:
        raise MappingError(
            "the ServiceNow export needs the State column (state). Export the Interactions list with "
            "Number, Opened for, Opened, State, State reason and Agent chat."
        )
    reason = find_column(df.columns, ["state_reason", "State reason"])
    agent = find_column(df.columns, ["agent_chat", "Agent chat"])
    st = _norm(df[state])
    rs = _norm(df[reason]) if reason else pd.Series("", index=df.index)
    live = _truthy(df[agent]) if agent else pd.Series(False, index=df.index)

    outcome = pd.Series(OTHER, index=df.index, dtype="string")
    outcome[st.eq("closed complete")] = RESOLVED
    outcome[st.eq("closed abandoned") | rs.eq("no activity")] = ABANDONED
    outcome[st.eq("faulted") | rs.eq("error")] = ABANDONED
    outcome[live | rs.isin({"agent ended", "left before engagement"})] = ESCALATED
    outcome[st.isin({"in progress", "new", "work in progress"})] = OPEN
    df["outcome"] = outcome
    detail = _display(df[reason]) if reason else _display(df[state])
    df["outcome_detail"] = detail.where(~live, "Live agent joined")
    if agent is None:
        notes.append(
            "The export has no Agent chat column, so conversations handed to a live agent were recognised "
            "only by their state reason. Add Agent chat to the export for a complete count."
        )
    return df, notes


CONVERSATION_PRESETS: dict[str, tuple[Mapping[str, PresetEntry], Callable | None]] = {
    "generic": (GENERIC, None),
    "servicenow_va": (SERVICENOW_VA, _servicenow_va),
}

PRESET_LABELS = {
    "generic": "Any conversation or automated-resolution export",
    "servicenow_va": "ServiceNow Virtual Agent (Interactions table, Virtual agent = true)",
}


def map_conversations(
    raw: pd.DataFrame, preset: str = "generic", overrides: Mapping[str, PresetEntry] | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """Map a conversation export onto the canonical session columns."""
    if preset not in CONVERSATION_PRESETS:
        raise MappingError(f"unknown conversation preset {preset!r}; choose from {sorted(CONVERSATION_PRESETS)}")
    base, prepare = CONVERSATION_PRESETS[preset]
    notes: list[str] = []
    if prepare is not None:
        raw, notes = prepare(raw)
    out, _ = apply_mapping(
        raw, resolve_mapping(base, overrides or {}), S.SESSION_COLUMNS, required=(S.REQUESTER, S.SESSION_STARTED)
    )
    return out, notes


def resolved_values(value: str | None) -> set[str]:
    """The outcome values that mean resolved: one value, or several separated by commas."""
    vals = {re.sub(r"[_\s]+", " ", v).strip().lower() for v in (value or RESOLVED).split(",")}
    return {v for v in vals if v} or {RESOLVED}


def normalize_outcomes(outcome: pd.Series | None, index: pd.Index, resolved_value: str | None = None) -> pd.Series:
    """Each conversation as resolved, escalated, abandoned, open or other.

    A missing outcome column means every row is a resolution (an export of resolutions only).
    """
    if outcome is None:
        return pd.Series(RESOLVED, index=index, dtype="string")
    o = _norm(outcome)
    out = pd.Series(OTHER, index=index, dtype="string")
    out[o.str.contains(r"escalat|hand ?off|handed|transfer|routed|live agent|human", regex=True)] = ESCALATED
    out[o.str.contains(r"abandon|no response|no activity|timed? ?out|unanswered|dropped", regex=True)] = ABANDONED
    out[o.isin({OPEN, "in progress", "new", "active"})] = OPEN
    out[o.isin(resolved_values(resolved_value))] = RESOLVED
    out[o.eq("")] = OTHER
    return out
