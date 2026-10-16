"""Synthetic service-desk data with planted, known effects.

Used for the demo, the validation suite and tests. The generator writes the
same files a customer would export from ServiceNow (incident list CSV, rollout
plan, virtual agent sessions, team list) plus ``truth.json`` with the effect
that was actually planted, so every estimate can be checked against the truth.

What it builds in on purpose, because real data has it:

* teams that differ in volume, speed and ticket mix;
* seasonality (September onboarding spike, year-end dip) shared by all teams;
* a staggered, randomized rollout in waves with a held-back group;
* a learning curve: the effect ramps up over the first weeks;
* deflection: self-service absorbs easy tickets, pushing the *headline* average
  up even though every ticket type got faster;
* false deflections that come back as tickets within a week;
* right-censoring (recent tickets still open at extraction), duplicate rows,
  impossible timestamps and missing assignment groups.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TicketType:
    key: str
    category: str
    subcategory: str
    share: float
    effort_hours: float
    resolution_hours: float
    effect_mult: float
    deflect_rate: float
    reopen_base: float
    descriptions: tuple[str, ...]


TYPES: tuple[TicketType, ...] = (
    TicketType(
        "password_reset",
        "Account Access",
        "Password Reset",
        0.18,
        0.25,
        3,
        1.2,
        0.60,
        0.02,
        ("Password reset needed", "Locked out of my account", "Reset VPN password"),
    ),
    TicketType(
        "access_request",
        "Account Access",
        "Permissions",
        0.14,
        0.5,
        10,
        1.1,
        0.30,
        0.03,
        ("Need access to shared drive", "Request access to finance app", "Add me to distribution list"),
    ),
    TicketType(
        "software_install",
        "Software",
        "Installation",
        0.14,
        0.8,
        14,
        1.0,
        0.05,
        0.04,
        ("Install Visio", "Need Python installed", "Software request: Tableau"),
    ),
    TicketType(
        "hardware",
        "Hardware",
        "Laptop",
        0.12,
        1.4,
        30,
        0.6,
        0.0,
        0.05,
        ("Laptop will not boot", "Screen flickering", "Replace broken keyboard"),
    ),
    TicketType(
        "email_issue",
        "Software",
        "Email",
        0.10,
        0.7,
        8,
        1.0,
        0.10,
        0.04,
        ("Outlook not syncing", "Cannot send email", "Mailbox full"),
    ),
    TicketType(
        "network",
        "Network",
        "Connectivity",
        0.08,
        1.2,
        16,
        0.8,
        0.0,
        0.06,
        ("Wi-Fi drops in building 3", "VPN keeps disconnecting", "Slow network on floor 2"),
    ),
    TicketType(
        "application_error",
        "Software",
        "Application Error",
        0.14,
        1.6,
        20,
        1.0,
        0.0,
        0.07,
        ("ERP throws error on save", "CRM page not loading", "Report fails to export"),
    ),
    TicketType(
        "inquiry_help",
        "Inquiry / Help",
        "General",
        0.10,
        0.4,
        5,
        1.3,
        0.40,
        0.02,
        ("How do I set up MFA?", "Question about printer setup", "Where do I find the VPN guide?"),
    ),
)

REGIONS = ("Americas", "EMEA", "APAC")


@dataclass(frozen=True)
class SimSpec:
    n_teams: int = 40
    n_weeks: int = 78
    start: date = date(2025, 1, 6)  # a Monday
    wave_weeks: tuple[int, ...] = (44, 52, 60)
    never_treated_share: float = 0.25
    effort_effect: float = -0.15  # relative change in agent hours per ticket at full ramp
    resolution_effect: float = -0.08
    deflection: bool = True
    false_deflection_rate: float = 0.12
    reopen_effect: float = 0.0  # absolute change in reopen probability (plant a guardrail breach)
    ramp_weeks: int = 6
    extraction_lag_days: int = 7  # days between the window end and the export (drives right-censoring)
    tickets_per_team_week: tuple[float, float] = (12.0, 40.0)
    users: int = 6000
    data_quality_issues: bool = True
    seed: int = 7
    types: tuple[TicketType, ...] = field(default=TYPES)


@dataclass
class SimData:
    tickets: pd.DataFrame
    rollout: pd.DataFrame
    sessions: pd.DataFrame
    teams: pd.DataFrame
    truth: dict

    def write(self, out_dir: str | Path, fmt: str = "servicenow") -> dict[str, Path]:
        """Write the files a customer would export, with tickets in ``fmt`` (see EXPORT_FORMATS)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "tickets": out / "incidents.csv",
            "rollout": out / "rollout.csv",
            "sessions": out / "virtual_agent.csv",
            "teams": out / "teams.csv",
            "truth": out / "truth.json",
        }
        export_tickets(self.tickets, fmt).to_csv(paths["tickets"], index=False)
        self.rollout.to_csv(paths["rollout"], index=False)
        sessions = self.sessions.copy()
        sessions["requester_id"] = _requester(sessions["requester_id"], fmt)
        sessions.to_csv(paths["sessions"], index=False)
        self.teams.to_csv(paths["teams"], index=False)
        paths["truth"].write_text(json.dumps({**self.truth, "export_format": fmt}, indent=2, default=str))
        return paths


EXPORT_FORMATS = ("servicenow", "jira_service_management", "zendesk", "freshservice", "bmc_helix", "ivanti")

# Platforms whose exports carry agent time worked; for the others the demo measures resolution time.
HAS_EFFORT = {"servicenow", "jira_service_management", "freshservice"}


def _requester(ids: pd.Series, fmt: str) -> pd.Series:
    """Requester identifiers as each platform exports them (emails for Freshservice)."""
    if fmt == "freshservice":
        return ids.astype("string").str.lower() + "@example.com"
    return ids


def _reformat_dates(col: pd.Series, fmt: str) -> pd.Series:
    ts = pd.to_datetime(col.replace("", None), errors="coerce")
    if fmt == "jira_service_management":
        out = ts.dt.strftime("%d/%b/%y %I:%M %p")
    elif fmt == "ivanti":
        out = ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        out = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
    return out.where(ts.notna(), "")


def export_tickets(tickets: pd.DataFrame, fmt: str) -> pd.DataFrame:
    """Re-express ServiceNow-style simulated tickets as another platform's CSV export."""
    if fmt == "servicenow":
        return tickets
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unknown export format {fmt!r}; choose from {EXPORT_FORMATS}")
    t = tickets.copy()
    for c in ("opened_at", "resolved_at", "closed_at"):
        t[c] = _reformat_dates(t[c], fmt)
    t["caller_id"] = _requester(t["caller_id"], fmt)
    seconds = pd.to_numeric(t["time_worked"], errors="coerce")
    number = t["number"].str.replace("INC", "", regex=False).str.lstrip("0")
    csat = pd.to_numeric(t["u_csat"], errors="coerce")
    if fmt == "jira_service_management":
        return pd.DataFrame(
            {
                "Summary": t["short_description"],
                "Issue key": "ITSD-" + number,
                "Issue Type": t["subcategory"],
                "Status": t["state"],
                "Priority": t["priority"],
                "Assignee": t["assigned_to"],
                "Reporter": t["caller_id"],
                "Created": t["opened_at"],
                "Resolved": t["resolved_at"],
                "Time Spent": seconds.round().astype("Int64"),
                "Custom field (Team)": t["assignment_group"],
                "Custom field (Request Type)": t["category"],
                "Custom field (Satisfaction)": csat,
            }
        )
    if fmt == "zendesk":
        rating = np.where(csat >= 4, "good", np.where(csat <= 2, "bad", np.where(csat.notna(), "offered", "unoffered")))
        return pd.DataFrame(
            {
                "ID": number,
                "Requester ID": t["caller_id"],
                "Assignee": t["assigned_to"],
                "Group": t["assignment_group"],
                "Subject": t["short_description"],
                "Status": t["state"],
                "Priority": t["priority"],
                "Via": t["contact_type"],
                "Ticket type": t["category"],
                "Created at": t["opened_at"],
                "Solved at": t["resolved_at"],
                "Satisfaction Score": rating,
                "Group stations": pd.to_numeric(t["reassignment_count"], errors="coerce") + 1,
                "Reopens": t["reopen_count"],
            }
        )
    if fmt == "freshservice":
        hhmm = seconds.map(lambda v: "" if pd.isna(v) else f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}")
        return pd.DataFrame(
            {
                "Ticket Id": number,
                "Subject": t["short_description"],
                "Status": t["state"],
                "Priority": t["priority"],
                "Source": t["contact_type"],
                "Agent": t["assigned_to"],
                "Group": t["assignment_group"],
                "Created Date": t["opened_at"],
                "Resolved Date": t["resolved_at"],
                "Closed Date": t["closed_at"],
                "Time Tracked": hhmm,
                "Category": t["category"],
                "Sub-Category": t["subcategory"],
                "Requester Email": t["caller_id"],
            }
        )
    if fmt == "bmc_helix":
        return pd.DataFrame(
            {
                "Incident Number": t["number"],
                "Assigned Group": t["assignment_group"],
                "Operational Categorization Tier 1": t["category"],
                "Operational Categorization Tier 2": t["subcategory"],
                "Priority": t["priority"],
                "Reported Source": t["contact_type"],
                "Reported Date": t["opened_at"],
                "Last Resolved Date": t["resolved_at"],
                "Closed Date": t["closed_at"],
                "Person ID": t["caller_id"],
                "Assignee Login ID": t["assigned_to"],
                "Status": t["state"],
                "Description": t["short_description"],
            }
        )
    return pd.DataFrame(
        {  # ivanti
            "IncidentNumber": number,
            "OwnerTeam": t["assignment_group"],
            "Category": t["category"],
            "SubCategory": t["subcategory"],
            "Priority": t["priority"],
            "Source": t["contact_type"],
            "CreatedDateTime": t["opened_at"],
            "ResolvedDateTime": t["resolved_at"],
            "ClosedDateTime": t["closed_at"],
            "ProfileLink_RecID": t["caller_id"],
            "Owner": t["assigned_to"],
            "Status": t["state"],
            "Subject": t["short_description"],
        }
    )


def _fmt(ts: pd.Series) -> pd.Series:
    return ts.dt.strftime("%Y-%m-%d %H:%M:%S").where(ts.notna(), "")


def generate(spec: SimSpec | None = None) -> SimData:
    spec = spec or SimSpec()
    rng = np.random.default_rng(spec.seed)
    types = spec.types
    K, T, W = len(types), spec.n_teams, spec.n_weeks
    start = pd.Timestamp(spec.start)

    # Teams ----------------------------------------------------------------------
    team_names = [f"SD-{REGIONS[i % 3][:4].upper()}-{i + 1:02d}" for i in range(T)]
    region = [REGIONS[i % 3] for i in range(T)]
    agents = rng.integers(6, 15, T)
    lam = rng.uniform(*spec.tickets_per_team_week, T)
    team_eff = rng.lognormal(0, 0.25, T)
    team_res = rng.lognormal(0, 0.25, T)
    base_share = np.array([t.share for t in types])
    team_mix = rng.dirichlet(base_share * 300, T)

    # Rollout: randomized wave order with a held-back group -----------------------
    order = rng.permutation(T)
    n_never = int(round(T * spec.never_treated_share))
    cohort = np.full(T, np.nan)
    for w, idx in enumerate(np.array_split(order[n_never:], len(spec.wave_weeks))):
        cohort[idx] = spec.wave_weeks[w]

    # Calendar effects --------------------------------------------------------------
    week_start = start + pd.to_timedelta(np.arange(W) * 7, unit="D")
    onboarding = np.asarray(week_start.month == 9)
    year_end = np.asarray((week_start.month == 12) & (week_start.day >= 18))
    vol_season = 1 + 0.25 * onboarding - 0.25 * year_end + rng.normal(0, 0.03, W)
    res_season = 1 + 0.15 * onboarding + 0.10 * year_end

    counts = rng.poisson(np.outer(lam, vol_season))
    team = np.repeat(np.repeat(np.arange(T), W), counts.ravel())
    week = np.repeat(np.tile(np.arange(W), T), counts.ravel())
    n = team.size

    probs = team_mix[team].copy()
    onboard_adj = np.ones(K)
    onboard_adj[[1, 3]] = 1.5  # access requests and laptops spike with new joiners
    probs[onboarding[week]] *= onboard_adj
    probs /= probs.sum(axis=1, keepdims=True)
    ktype = (rng.random(n)[:, None] > np.cumsum(probs, axis=1)).sum(axis=1)
    ktype = np.minimum(ktype, K - 1)

    day = rng.choice(7, size=n, p=[0.22, 0.21, 0.20, 0.19, 0.14, 0.02, 0.02])
    business = rng.random(n) < 0.85
    hour = np.where(business, rng.uniform(8, 18, n), (rng.uniform(18, 32, n)) % 24)
    opened = week_start[week] + pd.to_timedelta(day * 24 + hour, unit="h")
    opened = pd.Series(opened).dt.floor("s")

    c = cohort[team]
    with np.errstate(invalid="ignore"):
        treated = ~np.isnan(c) & (week >= c)
    e = np.where(treated, week - np.nan_to_num(c), -1)
    ramp = np.where(treated, np.clip((e + 1) / spec.ramp_weeks, 0, 1), 0.0)

    requester = rng.integers(1, spec.users + 1, n)

    # Deflection ---------------------------------------------------------------------
    deflect_rate = np.array([t.deflect_rate for t in types])[ktype]
    deflected = spec.deflection & treated & (rng.random(n) < deflect_rate * ramp)
    false_back = deflected & (rng.random(n) < spec.false_deflection_rate)
    verified = deflected & ~false_back
    session_time = opened - pd.to_timedelta(rng.uniform(5, 60, n), unit="m")
    # A false deflection comes back as a ticket 1-4 business days later at the same time of
    # day (weekend sessions come back on a weekend), so it does not change when work arrives.
    k = rng.integers(1, 5, n)
    wd = opened.dt.weekday.to_numpy()
    back_days = np.where(wd == 5, 1, np.where(wd == 6, 6, k + np.where(wd + k > 4, 2, 0)))
    opened = opened.where(~false_back, opened + pd.to_timedelta(back_days, unit="D"))

    # Outcomes -------------------------------------------------------------------------
    base_effort = np.array([t.effort_hours for t in types])[ktype]
    base_res = np.array([t.resolution_hours for t in types])[ktype]
    mult = np.array([t.effect_mult for t in types])[ktype]
    effort0 = base_effort * team_eff[team] * rng.lognormal(-0.125, 0.5, n)
    effort1 = effort0 * (1 + spec.effort_effect * mult * ramp)
    res0 = base_res * team_res[team] * res_season[week] * rng.lognormal(-0.245, 0.7, n)
    res1 = res0 * (1 + spec.resolution_effect * mult * ramp)
    res0 = np.maximum(res0, effort0 * 1.1)
    res1 = np.maximum(res1, effort1 * 1.1)

    p_reopen = np.array([t.reopen_base for t in types])[ktype] + spec.reopen_effect * ramp
    reopen = (rng.random(n) < np.clip(p_reopen, 0, 1)).astype(int)
    reopen += (reopen == 1) & (rng.random(n) < 0.1)
    reassign = rng.poisson(0.35 + 0.1 * (ktype >= 5), n)
    escalation = (rng.random(n) < 0.06).astype(int)
    csat = np.clip(np.round(4.3 + rng.normal(0, 0.7, n) - 0.8 * (reopen > 0)), 1, 5)
    csat = np.where(rng.random(n) < 0.22, csat, np.nan)
    ai_used = treated & (rng.random(n) < 0.5 + 0.3 * ramp)

    keep = ~verified
    window_end = start + pd.Timedelta(weeks=W)
    keep &= (opened < window_end).to_numpy()

    tt = keep & treated
    truth_effort_change = effort1[tt] - effort0[tt]
    # Expected change without ticket-level noise (E[noise] = 1): the estimand the team bootstrap targets.
    expected_effort_change = (base_effort * team_eff[team] * spec.effort_effect * mult * ramp)[tt]
    truth_res_change = res1[tt] - res0[tt]

    resolved = opened + pd.to_timedelta(res1, unit="h")
    extraction = window_end + pd.Timedelta(days=spec.extraction_lag_days)
    still_open = resolved > extraction
    resolved = resolved.where(~still_open)
    closed = (resolved + pd.Timedelta(days=3)).where(lambda s: s <= extraction)

    priority_labels = np.array(["1 - Critical", "2 - High", "3 - Moderate", "4 - Low"])
    priority = priority_labels[rng.choice(4, size=n, p=[0.03, 0.17, 0.55, 0.25])]
    channel = np.array(["Self-service", "Phone", "Email", "Chat"])[rng.choice(4, size=n, p=[0.45, 0.25, 0.2, 0.1])]
    descriptions = np.array([types[k].descriptions[i % len(types[k].descriptions)] for i, k in enumerate(ktype)])
    agent_idx = rng.integers(0, agents[team])
    state = np.where(still_open, "In Progress", np.where(closed.notna(), "Closed", "Resolved"))

    tickets = pd.DataFrame(
        {
            "opened_dt": opened,
            "assignment_group": np.array(team_names)[team],
            "category": np.array([t.category for t in types])[ktype],
            "subcategory": np.array([t.subcategory for t in types])[ktype],
            "priority": priority,
            "contact_type": channel,
            "opened_at": _fmt(opened),
            "resolved_at": _fmt(resolved),
            "closed_at": _fmt(closed),
            "time_worked": np.round(effort1 * 3600).astype(int),
            "reopen_count": reopen,
            "reassignment_count": reassign,
            "escalation": escalation,
            "caller_id": [f"USR{r:05d}" for r in requester],
            "assigned_to": [f"AGT{t + 1:02d}{a + 1:02d}" for t, a in zip(team, agent_idx, strict=True)],
            "state": state,
            "short_description": descriptions,
            "u_ai_assisted": np.where(ai_used, "true", "false"),
            "u_csat": csat,
        }
    )[keep].sort_values("opened_dt", kind="stable")
    tickets.insert(0, "number", [f"INC{1_000_000 + i:07d}" for i in range(len(tickets))])
    tickets = tickets.drop(columns="opened_dt").reset_index(drop=True)

    if spec.data_quality_issues and len(tickets) > 1000:
        m = len(tickets)
        bad = rng.choice(m, size=max(1, m // 700), replace=False)
        o = pd.to_datetime(tickets.loc[bad, "opened_at"])
        tickets.loc[bad, "resolved_at"] = (o - pd.Timedelta(hours=2)).dt.strftime("%Y-%m-%d %H:%M:%S").to_numpy()
        blank = rng.choice(m, size=max(1, m // 1000), replace=False)
        tickets.loc[blank, "assignment_group"] = ""
        dups = tickets.sample(n=max(1, m // 2000), random_state=spec.seed)
        tickets = pd.concat([tickets, dups], ignore_index=True)

    # Sessions: verified + false deflections, plus escalated and abandoned attempts ----
    attempt = treated & ~deflected & (deflect_rate > 0) & (rng.random(n) < 0.15 * ramp)
    sess_mask = deflected | attempt
    outcome = np.where(
        deflected[sess_mask], "resolved", np.where(rng.random(int(sess_mask.sum())) < 0.6, "escalated", "abandoned")
    )
    sessions = pd.DataFrame(
        {
            "session_id": [f"VA{i:08d}" for i in range(int(sess_mask.sum()))],
            "requester_id": [f"USR{r:05d}" for r in requester[sess_mask]],
            "started_at": _fmt(session_time[sess_mask]),
            "outcome": outcome,
            "category": np.array([t.category for t in types])[ktype[sess_mask]],
            "subcategory": np.array([t.subcategory for t in types])[ktype[sess_mask]],
        }
    )
    sessions = sessions[pd.to_datetime(sessions["started_at"]) < window_end].reset_index(drop=True)

    rollout_rows = []
    for i in range(T):
        if np.isnan(cohort[i]):
            rollout_rows.append(
                {"team_id": team_names[i], "start_date": "", "kind": "holdout", "wave": "", "randomized": "true"}
            )
        else:
            wave = spec.wave_weeks.index(int(cohort[i])) + 1
            rollout_rows.append(
                {
                    "team_id": team_names[i],
                    "start_date": (start + pd.Timedelta(weeks=int(cohort[i]))).date().isoformat(),
                    "kind": "wave",
                    "wave": wave,
                    "randomized": "true",
                }
            )
    rollout = pd.DataFrame(rollout_rows)
    teams = pd.DataFrame(
        {
            "team_id": team_names,
            "parent_unit": region,
            "function": "IT service management",
            "size_band": np.where(agents >= 10, "10-14", "6-9"),
            "cost_center": [f"CC-{4100 + i % 3}" for i in range(T)],
        }
    )

    truth = {
        "spec": {k: v for k, v in asdict(spec).items() if k != "types"},
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tickets_kept": int(keep.sum()),
        "treated_tickets": int(tt.sum()),
        "effort_att_hours_per_ticket": float(truth_effort_change.mean()) if tt.any() else 0.0,
        "effort_total_change_hours": float(truth_effort_change.sum()),
        "effort_att_expected": float(expected_effort_change.mean()) if tt.any() else 0.0,
        "effort_relative": float(truth_effort_change.sum() / effort0[tt].sum()) if tt.any() else 0.0,
        "resolution_att_hours_per_ticket": float(truth_res_change.mean()) if tt.any() else 0.0,
        "verified_deflections": int((verified & (opened < window_end).to_numpy()).sum()),
        "false_deflections": int(false_back.sum()),
        "first_rollout": (start + pd.Timedelta(weeks=int(np.nanmin(cohort)))).date().isoformat()
        if np.isfinite(cohort).any()
        else None,
        "window": {
            "start": spec.start.isoformat(),
            "end": (spec.start + timedelta(weeks=W) - timedelta(days=1)).isoformat(),
        },
    }
    return SimData(tickets=tickets, rollout=rollout, sessions=sessions, teams=teams, truth=truth)


def demo_config(data_dir: str = "data", truth: dict | None = None, fmt: str = "servicenow") -> dict:
    """A complete project configuration for simulated data, as a YAML-ready dict."""
    window = (truth or {}).get("window", {})
    return {
        "program": {
            "id": "ai-agent-assist",
            "name": "AI agent assist (simulated)",
            "vendor": "Example vendor",
            "tool": "Agent assist + virtual agent",
            "function": "it_service_management",
            "owner": "Head of IT Service Management",
            "contract_ref": "DEMO-2025-001",
        },
        "hypothesis": {
            "statement": "AI summaries and resolution suggestions cut agent hours per ticket by at least 10% "
            "without lowering quality; the virtual agent deflects password resets and simple requests.",
            "primary_metric": "effort_hours" if fmt in HAS_EFFORT else "resolution_hours",
            "expected_effect": -0.10,
        },
        "playbook": "service_desk",
        "data": {
            "work_units": f"{data_dir}/incidents.csv",
            "mapping_preset": fmt,
            "exposure": f"{data_dir}/rollout.csv",
            "self_service": f"{data_dir}/virtual_agent.csv",
            "teams": f"{data_dir}/teams.csv",
        },
        "analysis": {
            "start": window.get("start"),
            "end": window.get("end"),
            "bootstrap_reps": 499,
        },
        "value": {
            "currency": "USD",
            "loaded_hourly_cost": {"low": 52, "mid": 60, "high": 70},
            "realization_rate": {"low": 0.3, "mid": 0.5, "high": 0.7},
            "realized_savings": [],
        },
        "costs": [
            {
                "component": "license",
                "description": "AI add-on licence uplift (illustrative)",
                "amount": 48000,
                "basis": "annual",
                "source": "Order form (illustrative)",
            },
            {
                "component": "build_integration",
                "description": "Implementation partner (illustrative)",
                "amount": {"low": 8000, "mid": 10000, "high": 14000},
                "basis": "one_time",
            },
            {
                "component": "change_training",
                "description": "Agent training: 300 agents × 20 min × $60",
                "amount": 6000,
                "basis": "one_time",
            },
            {
                "component": "human_oversight",
                "description": "Knowledge-base upkeep and AI review time",
                "amount": {"low": 500, "mid": 1000, "high": 1500},
                "basis": "monthly",
            },
        ],
        "claimed": [
            {
                "source": "Vendor value dashboard (illustrative)",
                "hours_saved": 9000,
                "note": "Usage × assumed minutes saved per summary",
            }
        ],
        "audit": {"window_days": 7, "match": "same_type", "price_per_resolution": 1.0},
    }
