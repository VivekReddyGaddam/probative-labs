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
    regression_week: int | None = None  # from this week on, a silent vendor update changes the false rate
    false_deflection_rate_after: float | None = None
    reopen_effect: float = 0.0  # absolute change in reopen probability (plant a guardrail breach)
    ramp_weeks: int = 6
    extraction_lag_days: int = 7  # days between the window end and the export (drives right-censoring)
    tickets_per_team_week: tuple[float, float] = (12.0, 40.0)
    users: int = 6000
    ai_closure_share: float = 0.0  # share of AI-assisted easy tickets the AI agent closes by itself
    ai_closure_failure_rate: float = 0.0  # planted share of AI closures that get reopened
    bill_price: float = 1.0  # the vendor's price per claimed resolution
    bill_errors: tuple[float, float, float] = (0.01, 0.005, 0.02)  # duplicates, unknown IDs, escalations billed
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
    bill: pd.DataFrame = field(default_factory=pd.DataFrame)
    # The same desk under other billing models and exports, keyed by file name (see _billing_exports).
    extras: dict[str, pd.DataFrame] = field(default_factory=dict)

    def write(self, out_dir: str | Path, fmt: str = "servicenow") -> dict[str, Path]:
        """Write the files a customer would export, with tickets in ``fmt`` (see EXPORT_FORMATS)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "tickets": out / "incidents.csv",
            "rollout": out / "rollout.csv",
            "sessions": out / "virtual_agent.csv",
            "teams": out / "teams.csv",
            "bill": out / "billing.csv",
            "truth": out / "truth.json",
        }
        export_tickets(self.tickets, fmt).to_csv(paths["tickets"], index=False)
        self.rollout.to_csv(paths["rollout"], index=False)
        sessions = self.sessions.copy()
        sessions["requester_id"] = _requester(sessions["requester_id"], fmt)
        sessions.to_csv(paths["sessions"], index=False)
        self.teams.to_csv(paths["teams"], index=False)
        if self.bill.empty:
            del paths["bill"]
        else:
            self.bill.to_csv(paths["bill"], index=False)
        for name, frame in self.extras.items():
            frame = frame.copy()
            for col in ("opened_for", "requester_id"):
                if col in frame.columns:
                    frame[col] = _requester(frame[col], fmt)
            path = out / f"{name}.csv"
            frame.to_csv(path, index=False)
            paths[name] = path
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
    false_rate = np.full(n, spec.false_deflection_rate)
    if spec.regression_week is not None and spec.false_deflection_rate_after is not None:
        false_rate = np.where(week >= spec.regression_week, spec.false_deflection_rate_after, false_rate)
    false_back = deflected & (rng.random(n) < false_rate)
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

    closures = _plant_ai_closures(tickets, spec)

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
            "_false": false_back[sess_mask],
        }
    )
    sessions = sessions[pd.to_datetime(sessions["started_at"]) < window_end].reset_index(drop=True)
    false_flags = sessions.pop("_false").to_numpy(dtype=bool)

    bill, bill_truth = _vendor_bill(sessions, spec)

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
        "bill": bill_truth,
        "ai_closures": closures,
    }
    extras, billing_truth = _billing_exports(sessions, false_flags, tickets, spec)
    truth["billing_models"] = billing_truth
    return SimData(
        tickets=tickets, rollout=rollout, sessions=sessions, teams=teams, truth=truth, bill=bill, extras=extras
    )


def _plant_ai_closures(tickets: pd.DataFrame, spec: SimSpec) -> dict:
    """Mark tickets the AI agent closed by itself ("resolved_by"), and reopen a planted share of them.

    Uses its own random stream and changes nothing unless ``ai_closure_share`` is set.
    """
    if spec.ai_closure_share <= 0:
        return {}
    rng = np.random.default_rng(spec.seed + 9_001)
    easy = {t.category for t in spec.types if t.deflect_rate > 0}
    candidate = (
        (tickets["u_ai_assisted"] == "true") & tickets["category"].isin(easy) & (tickets["resolved_at"] != "")
    ).to_numpy()
    closed = candidate & (rng.random(len(tickets)) < spec.ai_closure_share)
    failed = closed & (rng.random(len(tickets)) < spec.ai_closure_failure_rate)
    reopen = tickets["reopen_count"].to_numpy()
    tickets["reopen_count"] = np.where(failed & (reopen == 0), 1, reopen)
    tickets["resolved_by"] = np.where(closed, "AI Agent", tickets["assigned_to"])
    return {"closed_by_ai": int(closed.sum()), "planted_failure_rate": spec.ai_closure_failure_rate}


def _vendor_bill(sessions: pd.DataFrame, spec: SimSpec) -> tuple[pd.DataFrame, dict]:
    """The vendor's per-resolution bill, with known errors: duplicates, unknown IDs and billed escalations.

    Uses its own random stream, so adding the bill leaves every other simulated file unchanged.
    """
    rng = np.random.default_rng(spec.seed + 7_777)
    dup_share, unknown_share, escalated_share = spec.bill_errors
    resolved = sessions[sessions["outcome"] == "resolved"]
    others = sessions[sessions["outcome"] != "resolved"]
    if resolved.empty:
        return pd.DataFrame(), {}
    dups = resolved[rng.random(len(resolved)) < dup_share]
    escalated = others[rng.random(len(others)) < escalated_share]
    n_unknown = int(round(len(resolved) * unknown_share))
    unknown_dates = resolved["started_at"].to_numpy()[rng.integers(0, len(resolved), n_unknown)]
    unknown = pd.DataFrame(
        {"session_id": [f"VX{i:08d}" for i in rng.integers(0, 10**8, n_unknown)], "started_at": unknown_dates}
    )
    lines = pd.concat([resolved[["session_id", "started_at"]], dups, escalated, unknown], ignore_index=True)[
        ["session_id", "started_at"]
    ]
    lines = lines.iloc[rng.permutation(len(lines))].sort_values("started_at", kind="stable")
    bill = pd.DataFrame(
        {"Conversation ID": lines["session_id"], "Resolved at": lines["started_at"], "Amount": spec.bill_price}
    ).reset_index(drop=True)
    truth = {
        "lines": len(bill),
        "duplicates": len(dups),
        "unknown_ids": n_unknown,
        "escalations_billed": len(escalated),
        "price": spec.bill_price,
    }
    return bill, truth


# How ServiceNow closes Virtual Agent conversations (Interactions table, state and state reason).
_REASONS = ("Topic Complete", "Auto Closed", "No User Response", "User Ended", "Left for search")
_REASONS_HELD = (0.42, 0.30, 0.13, 0.12, 0.03)
_REASONS_FALSE = (0.20, 0.08, 0.42, 0.22, 0.08)  # people who come back mostly left without finishing


def _sessions_by_24h(requester: np.ndarray, started: np.ndarray) -> np.ndarray:
    """A session number per conversation: one person's conversations within 24 hours of the session's first."""
    order = np.lexsort((started, requester))
    out = np.empty(len(order), dtype=np.int64)
    day = np.timedelta64(24, "h")
    current, cur_req, cur_start = -1, None, None
    for i in order:
        if requester[i] != cur_req or started[i] - cur_start >= day:
            current, cur_req, cur_start = current + 1, requester[i], started[i]
        out[i] = current
    return out


def _billing_exports(
    sessions: pd.DataFrame, false_flags: np.ndarray, tickets: pd.DataFrame, spec: SimSpec
) -> tuple[dict[str, pd.DataFrame], dict]:
    """The same desk as other platforms would export and bill it, with known errors planted.

    * ``interactions``: ServiceNow Virtual Agent conversations as the Interactions table exports them.
      ServiceNow closes a chat the person walked away from as "Closed Complete, No User Response", so
      some abandoned chats look resolved, and people who came back mostly left that way.
    * ``bill_per_conversation``: every conversation billed (as per-conversation pricing does), with
      duplicates and IDs that are not in the buyer's records.
    * ``bill_per_session``: sessions billed per day, counted per calendar day instead of per 24 hours.
    * ``now_assist_usage``: assists by skill and instance, with Virtual Agent conversations linked by
      their interaction number and a share of use from development and test instances.

    Uses its own random stream, so every other simulated file is unchanged.
    """
    rng = np.random.default_rng(spec.seed + 4_242)
    if sessions.empty:
        return {}, {}
    n = len(sessions)
    outcome = sessions["outcome"].to_numpy()
    started = pd.to_datetime(sessions["started_at"])

    # ServiceNow Virtual Agent interactions ------------------------------------------------------
    state = np.empty(n, dtype=object)
    reason = np.empty(n, dtype=object)
    agent = np.zeros(n, dtype=bool)
    res = outcome == "resolved"
    held_idx, false_idx = np.where(res & ~false_flags)[0], np.where(res & false_flags)[0]
    state[res] = "Closed Complete"
    reason[held_idx] = rng.choice(_REASONS, size=len(held_idx), p=_REASONS_HELD)
    reason[false_idx] = rng.choice(_REASONS, size=len(false_idx), p=_REASONS_FALSE)
    e_idx = np.where(outcome == "escalated")[0]
    left = rng.random(len(e_idx)) < 0.15
    state[e_idx] = np.where(left, "Closed Abandoned", "Closed Complete")
    reason[e_idx] = np.where(
        left, "Left Before Engagement", np.where(rng.random(len(e_idx)) < 0.7, "Agent Ended", "User Ended")
    )
    agent[e_idx] = ~left
    a_idx = np.where(outcome == "abandoned")[0]
    r = rng.random(len(a_idx))
    state[a_idx] = np.where(r < 0.5, "Closed Complete", np.where(r < 0.95, "Closed Abandoned", "Faulted"))
    reason[a_idx] = np.where(r < 0.5, "No User Response", np.where(r < 0.95, "No Activity", "Error"))
    numbers = np.array([f"IMS{i + 1:07d}" for i in range(n)])
    interactions = pd.DataFrame(
        {
            "number": numbers,
            "opened_for": sessions["requester_id"].to_numpy(),
            "opened_at": sessions["started_at"].to_numpy(),
            "type": "chat",
            "virtual_agent": "true",
            "agent_chat": np.where(agent, "true", "false"),
            "state": state,
            "state_reason": reason,
        }
    )
    live_n = max(1, n // 20)  # live-agent chats in the same table, left out by the Virtual agent flag
    live = pd.DataFrame(
        {
            "number": [f"IMS{n + i + 1:07d}" for i in range(live_n)],
            "opened_for": sessions["requester_id"].sample(live_n, random_state=spec.seed, replace=True).to_numpy(),
            "opened_at": sessions["started_at"].sample(live_n, random_state=spec.seed + 1, replace=True).to_numpy(),
            "type": "chat",
            "virtual_agent": "false",
            "agent_chat": "true",
            "state": "Closed Complete",
            "state_reason": "Agent Ended",
        }
    )
    interactions = pd.concat([interactions, live], ignore_index=True).sort_values("opened_at", kind="stable")
    snow_claimed = state == "Closed Complete"
    snow_claimed &= ~agent

    # Billed per conversation ---------------------------------------------------------------------
    conv_price = 2.0
    base = sessions[["session_id", "started_at"]]
    dups = base[rng.random(n) < 0.01]
    n_unknown = max(1, int(round(n * 0.004)))
    unknown = pd.DataFrame(
        {
            "session_id": [f"VZ{i:08d}" for i in rng.integers(0, 10**8, n_unknown)],
            "started_at": base["started_at"].to_numpy()[rng.integers(0, n, n_unknown)],
        }
    )
    lines = pd.concat([base, dups, unknown], ignore_index=True)
    lines = lines.iloc[rng.permutation(len(lines))].sort_values("started_at", kind="stable")
    bill_conv = pd.DataFrame(
        {"Conversation ID": lines["session_id"], "Date": lines["started_at"], "Amount": conv_price}
    ).reset_index(drop=True)

    # Billed per session, counted by calendar day instead of 24 hours -----------------------------
    req = sessions["requester_id"].to_numpy().astype(str)
    true_session = _sessions_by_24h(req, started.to_numpy(dtype="datetime64[ns]"))
    day = started.dt.normalize()
    session_start = pd.Series(started.to_numpy()).groupby(true_session).min()
    true_per_day = session_start.dt.normalize().value_counts()
    vendor = pd.DataFrame({"r": req, "d": day}).drop_duplicates()
    billed_per_day = vendor.groupby("d").size()
    # On about one day in five the vendor's meter also runs a few percent above the sessions it served.
    bump = (rng.random(len(billed_per_day)) < 0.2) * np.ceil(billed_per_day.to_numpy() * rng.uniform(0.03, 0.12))
    billed_per_day = billed_per_day + bump.astype(int)
    bill_sess = pd.DataFrame({"Date": billed_per_day.index.strftime("%Y-%m-%d"), "Sessions": billed_per_day.to_numpy()})

    # Now Assist usage, in assists ----------------------------------------------------------------
    rows = []
    va_assists = np.where(res | (state == "Closed Complete") & ~agent, rng.integers(2, 5, n), 0)
    va_assists = np.where(outcome == "escalated", rng.integers(4, 9, n), va_assists)
    va_assists = np.where(va_assists == 0, rng.integers(1, 3, n), va_assists)
    rows.append(
        pd.DataFrame(
            {
                "sys_created_on": sessions["started_at"].to_numpy(),
                "instance": "acme",
                "skill": "Now Assist in Virtual Agent",
                "assists": va_assists,
                "document_id": numbers,
            }
        )
    )
    ai = tickets[tickets["u_ai_assisted"] == "true"]
    for skill, share, assists in (
        ("Incident summarization", 0.35, 1),
        ("Resolution notes generation", 0.25, 1),
        ("Chat reply recommendation", 0.06, 5),
        ("AI agent: password reset", 0.02, 25),
    ):
        used = ai[rng.random(len(ai)) < share]
        per_day = pd.to_datetime(used["opened_at"]).dt.normalize().value_counts().sort_index()
        rows.append(
            pd.DataFrame(
                {
                    "sys_created_on": per_day.index.strftime("%Y-%m-%d 12:00:00"),
                    "instance": "acme",
                    "skill": skill,
                    "assists": per_day.to_numpy() * assists,
                    "document_id": "",
                }
            )
        )
    prod = pd.concat(rows, ignore_index=True)
    test = prod[prod["skill"] != "Now Assist in Virtual Agent"].sample(frac=0.9, random_state=spec.seed)
    test = test.assign(
        instance=np.where(rng.random(len(test)) < 0.6, "acme-dev", "acme-test"),
        assists=np.maximum(1, np.round(test["assists"].to_numpy() * rng.uniform(0.2, 0.6, len(test)))).astype(int),
        document_id="",
    )
    usage = pd.concat([prod, test], ignore_index=True).sort_values("sys_created_on", kind="stable")

    truth = {
        "interactions": {
            "virtual_agent": n,
            "live_agent_only": live_n,
            "servicenow_resolved": int(snow_claimed.sum()),
            "no_user_response_resolved": int((snow_claimed & (reason == "No User Response")).sum()),
        },
        "per_conversation": {
            "lines": len(bill_conv),
            "duplicates": len(dups),
            "unknown_ids": n_unknown,
            "price": conv_price,
        },
        "per_session": {
            "sessions_24h": int(true_session.max() + 1),
            "billed": int(billed_per_day.sum()),
            "overcount": int(billed_per_day.sum() - true_per_day.sum()),
        },
        "usage": {
            "assists": int(usage["assists"].sum()),
            "non_production": int(test["assists"].sum()),
            "virtual_agent": int(prod.loc[prod["skill"] == "Now Assist in Virtual Agent", "assists"].sum()),
        },
    }
    extras = {
        "interactions": interactions,
        "bill_per_conversation": bill_conv,
        "bill_per_session": bill_sess,
        "now_assist_usage": usage,
    }
    return extras, truth


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
