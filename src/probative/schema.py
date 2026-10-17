"""Canonical data model shared by every function playbook.

A *work unit* (ticket, pull request, alert, invoice...) is done by a *team*,
possibly touched by an *AI program*, with outcomes and quality events attached.
Connectors and file mappings translate source systems into these columns;
everything downstream reads only these names.
"""

from __future__ import annotations

# Work units -----------------------------------------------------------------

UNIT_ID = "unit_id"
TEAM = "team_id"
WORK_TYPE = "work_type"
CATEGORY = "category"
SUBCATEGORY = "subcategory"
PRIORITY = "priority"
CHANNEL = "channel"
OPENED = "opened_at"
RESOLVED = "resolved_at"
CLOSED = "closed_at"
EFFORT = "effort_hours"
REOPENS = "reopen_count"
REASSIGNMENTS = "reassignment_count"
ESCALATED = "escalated"
CSAT = "csat"
AI_TOUCHED = "ai_touched"
REQUESTER = "requester_id"
ASSIGNEE = "assignee_id"
STATE = "state"
DESCRIPTION = "short_description"

WORK_UNIT_COLUMNS: dict[str, str] = {
    UNIT_ID: "string",
    TEAM: "string",
    CATEGORY: "string",
    SUBCATEGORY: "string",
    PRIORITY: "string",
    CHANNEL: "string",
    OPENED: "datetime",
    RESOLVED: "datetime",
    CLOSED: "datetime",
    EFFORT: "number",
    REOPENS: "number",
    REASSIGNMENTS: "number",
    ESCALATED: "bool",
    CSAT: "number",
    AI_TOUCHED: "bool",
    REQUESTER: "string",
    ASSIGNEE: "string",
    STATE: "string",
    DESCRIPTION: "string",
}

REQUIRED_WORK_UNIT_COLUMNS = (UNIT_ID, TEAM, OPENED)

# Identifiers that point at a person or a source record. They are replaced with
# keyed hashes at ingest, before anything is stored.
PERSON_COLUMNS = (REQUESTER, ASSIGNEE)
SOURCE_ID_COLUMNS = (UNIT_ID,)

# Free text is read only for work typing and dropped afterwards.
TEXT_COLUMNS = (DESCRIPTION,)

# Derived columns (added by the pipeline, never mapped from sources) ----------

PERIOD = "period"
RESOLUTION = "resolution_hours"
REOPENED = "reopened"
AFTER_HOURS = "after_hours"

# Exposure: which team had which AI from which date ---------------------------

EXPOSURE_COLUMNS: dict[str, str] = {
    TEAM: "string",
    "start_date": "datetime",
    "end_date": "datetime",
    "kind": "string",  # wave | licence | feature_switch | holdout
    "randomized": "bool",
}

# Self-service / virtual agent sessions (for verified deflection) -------------

SESSION_ID = "session_id"
SESSION_STARTED = "started_at"
SESSION_OUTCOME = "outcome"  # resolved | escalated | abandoned
OUTCOME_DETAIL = "outcome_detail"  # the platform's own words for how it ended, e.g. "No User Response"

SESSION_COLUMNS: dict[str, str] = {
    SESSION_ID: "string",
    REQUESTER: "string",
    SESSION_STARTED: "datetime",
    SESSION_OUTCOME: "string",
    OUTCOME_DETAIL: "string",
    CATEGORY: "string",
    SUBCATEGORY: "string",
    DESCRIPTION: "string",
}

# Teams --------------------------------------------------------------------------

TEAM_COLUMNS: dict[str, str] = {
    TEAM: "string",
    "parent_unit": "string",
    "function": "string",
    "size_band": "string",
    "cost_center": "string",
}

# Staffing fallback: agent hours per team-week when time worked is not recorded.
STAFFING_COLUMNS: dict[str, str] = {
    TEAM: "string",
    "week_start": "datetime",
    "hours": "number",
}

# Vendor bills (for outcome bill reconciliation) ------------------------------------

BILL_ITEM = "item_id"  # the vendor's identifier for the billed conversation or resolution
BILLED_AT = "billed_at"
AMOUNT = "amount"
QUANTITY = "quantity"

BILL_COLUMNS: dict[str, str] = {
    BILL_ITEM: "string",
    BILLED_AT: "datetime",
    AMOUNT: "number",
    QUANTITY: "number",
}

# Usage logs, for AI billed by consumption (assists, credits, actions) ------------------

USED_AT = "used_at"
FEATURE = "feature"  # the skill, agent or action that consumed the units
UNITS = "units"  # assists, credits or other billed units
ACTIONS = "actions"
UNIT_RATIO = "unit_ratio"  # units per action, when the log records actions
ENVIRONMENT = "environment"  # instance or environment, to tell production from testing
USER = "user_id"  # who used it, for active-user counts

USAGE_COLUMNS: dict[str, str] = {
    USED_AT: "datetime",
    FEATURE: "string",
    UNITS: "number",
    ACTIONS: "number",
    UNIT_RATIO: "number",
    ENVIRONMENT: "string",
    SESSION_ID: "string",
    USER: "string",
}
