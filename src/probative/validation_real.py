"""Validation on real ServiceNow data.

Simulated desks show that the method recovers what was planted in them. They cannot show that the
engine copes with a real export, or what real return rates look like. This module uses a public
log from a real ServiceNow instance: the incident management event log in the UCI Machine Learning
Repository (Amaral, Fantinato and Peres, 2018; CC BY 4.0), the audit trail of an IT company's
service desk. It predates AI agents, so it cannot show an AI effect. It shows three things:

1. The engine reads the real export. The event log is reduced to each incident's last state and
   mapped with the ServiceNow preset; the health check reports what is wrong with it.
2. Real return rates after people's resolutions: how often the same caller opened another
   incident within 7 days, overall, by channel and by category.
3. Recovery on real data. Real resolved incidents are relabelled as an AI's claimed resolutions,
   so the claims carry real callers, categories and timing. For a planted share of them, a return
   incident is added; the audit should find that share, and find none when none was planted.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from probative import schema as S
from probative.audit.resolutions import audit_resolutions, next_ticket
from probative.config import TypingConfig
from probative.ingest import apply_mapping, check_work_units, resolve_mapping
from probative.worktypes import assign_work_types

UCI_TITLE = "Incident management process enriched event log"
UCI_CITATION = (
    "Amaral, C., Fantinato, M., & Peres, S. (2018). Incident management process enriched event log "
    "[Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C57S4H"
)
UCI_URL = "https://archive.ics.uci.edu/dataset/498/incident+management+process+enriched+event+log"
UCI_LICENSE = "CC BY 4.0"


def load_uci_incidents(path: str | Path) -> tuple[pd.DataFrame, dict]:
    """The UCI event log as one canonical row per incident, at its last recorded state."""
    raw = pd.read_csv(path, dtype=str, na_values=["?"], keep_default_na=True)
    events = len(raw)
    raw["_mod"] = pd.to_numeric(raw["sys_mod_count"], errors="coerce")
    last = raw.sort_values(["number", "_mod"], kind="stable").drop_duplicates("number", keep="last")
    last = last.drop(columns="_mod").reset_index(drop=True)
    mapping = resolve_mapping("servicenow", {S.STATE: "incident_state"})
    units, report = apply_mapping(last, mapping, S.WORK_UNIT_COLUMNS, required=(S.UNIT_ID, S.OPENED, S.REQUESTER))
    units[S.TEAM] = units[S.TEAM].astype("string").fillna("(unassigned)")
    units, health = check_work_units(units)
    units[S.WORK_TYPE] = assign_work_types(
        units, TypingConfig(fields=["category", "subcategory"], rules=[], min_share=0.0)
    )
    h = health.to_dict()
    meta = {
        "events": events,
        "incidents": len(last),
        "rows_kept": len(units),
        "mapped": list(report.mapped),
        "missing_optional": list(report.missing_optional),
        "first_opened": units[S.OPENED].min().date(),
        "last_opened": units[S.OPENED].max().date(),
        "callers": int(units[S.REQUESTER].nunique()),
        "issues": [{"severity": i["severity"], "message": i["message"]} for i in h.get("issues", [])],
    }
    return units, meta


def _rates(res: pd.DataFrame, tickets: pd.DataFrame, window: pd.Timedelta) -> dict:
    any_back = next_ticket(res, S.RESOLVED, tickets, [S.REQUESTER], window).notna()
    same_back = next_ticket(res, S.RESOLVED, tickets, [S.REQUESTER, S.WORK_TYPE], window).notna()
    return {"resolved": int(len(res)), "any": float(any_back.mean()), "same_type": float(same_back.mean())}


def real_baseline(units: pd.DataFrame, window_days: int = 7, min_group: int = 300) -> dict:
    """How often a caller opened another incident within the window after one was resolved."""
    window = pd.Timedelta(days=window_days)
    tk = units.dropna(subset=[S.OPENED, S.REQUESTER])
    res = tk.dropna(subset=[S.RESOLVED])
    res = res[res[S.RESOLVED] <= tk[S.OPENED].max() - window]
    out = {"window_days": window_days, "overall": _rates(res, tk, window), "by_channel": [], "by_category": []}
    for col, key in ((S.CHANNEL, "by_channel"), (S.CATEGORY, "by_category")):
        if col not in res.columns:
            continue
        counts = res[col].value_counts()
        for name in counts[counts >= min_group].index[:8]:
            out[key].append({"name": str(name), **_rates(res[res[col] == name], tk, window)})
    return out


def run_real_audit_validation(
    units: pd.DataFrame,
    *,
    levels: tuple[float, ...] = (0.0, 0.10, 0.20),
    runs: int = 30,
    claims: int = 2000,
    reps: int = 199,
    window_days: int = 7,
    seed: int = 7,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Plant a known share of failed resolutions among real incidents and check the audit finds it.

    Each run relabels ``claims`` real resolved incidents, chosen at random, as an AI's claimed
    resolutions made when the incident was resolved; they leave the ticket data, as a deflected
    contact would. Before planting, they are the same kind of resolution as the rest, so the true
    share that did not hold is zero. For a planted share f, a return incident about the same type
    of issue is added within the window for a random f of the claims that did not come back, which
    makes the true excess exactly f.
    """
    rng = np.random.default_rng(seed)
    window = pd.Timedelta(days=window_days)
    tk = units.dropna(subset=[S.OPENED, S.REQUESTER]).reset_index(drop=True)
    first, last = tk[S.OPENED].min(), tk[S.OPENED].max()
    ok = tk[S.RESOLVED].notna() & (tk[S.RESOLVED] >= first) & (tk[S.RESOLVED] <= last - window)
    eligible = np.flatnonzero(ok.to_numpy())
    rows = []
    for level in levels:
        for run in range(runs):
            pick = rng.choice(eligible, size=min(claims, len(eligible)), replace=False)
            claimed = tk.iloc[pick]
            tickets = tk.drop(index=tk.index[pick])
            conv = pd.DataFrame(
                {
                    S.SESSION_ID: claimed[S.UNIT_ID].to_numpy(),
                    S.REQUESTER: claimed[S.REQUESTER].to_numpy(),
                    S.SESSION_STARTED: claimed[S.RESOLVED].to_numpy(),
                    S.SESSION_OUTCOME: "resolved",
                    S.CATEGORY: claimed[S.CATEGORY].to_numpy(),
                    S.SUBCATEGORY: claimed[S.SUBCATEGORY].to_numpy(),
                    S.WORK_TYPE: claimed[S.WORK_TYPE].to_numpy(),
                }
            )
            back = next_ticket(conv, S.SESSION_STARTED, tickets, [S.REQUESTER, S.WORK_TYPE], window).notna()
            quiet = np.flatnonzero(~back.to_numpy())
            n_plant = int(round(level * len(quiet)))
            if n_plant:
                p = conv.iloc[rng.choice(quiet, size=n_plant, replace=False)]
                gap = pd.to_timedelta(rng.uniform(0.5, window_days - 0.5, n_plant), unit="D")
                planted = pd.DataFrame(
                    {
                        S.UNIT_ID: [f"PLANTED{run:03d}-{i:05d}" for i in range(n_plant)],
                        S.TEAM: "(planted)",
                        S.REQUESTER: p[S.REQUESTER].to_numpy(),
                        S.OPENED: (p[S.SESSION_STARTED] + gap).to_numpy(),
                        S.CATEGORY: p[S.CATEGORY].to_numpy(),
                        S.SUBCATEGORY: p[S.SUBCATEGORY].to_numpy(),
                        S.WORK_TYPE: p[S.WORK_TYPE].to_numpy(),
                    }
                )
                tickets = pd.concat([tickets, planted], ignore_index=True)
            res = audit_resolutions(
                conv, tickets, window_days=window_days, match="same_type", reps=reps, seed=seed + run
            ).to_dict()
            f = res["failed_share"]
            rows.append(
                {
                    "planted": level,
                    "run": run,
                    "claims": res["claims"]["checked"],
                    "estimate": f["estimate"],
                    "ci_low": f["ci_low"],
                    "ci_high": f["ci_high"],
                    "covered": bool(f["ci_low"] <= level <= f["ci_high"]),
                    "flagged": bool(f["ci_low"] > 0),
                    "ai_rate": res["returns"]["ai_rate"],
                    "baseline_rate": res["returns"]["baseline_rate"],
                    "confidence": res["confidence"]["level"],
                }
            )
        if progress:
            progress(f"planted {level:.0%}: {runs} runs done")
    return pd.DataFrame(rows)


def summarize_real(df: pd.DataFrame) -> list[dict]:
    out = []
    for level, g in df.groupby("planted"):
        out.append(
            {
                "planted": float(level),
                "runs": int(len(g)),
                "claims": int(g["claims"].median()),
                "mean_estimate": float(g["estimate"].mean()),
                "mean_abs_error": float((g["estimate"] - level).abs().mean()),
                "coverage": float(g["covered"].mean()),
                "flagged": float(g["flagged"].mean()),
                "mean_width": float((g["ci_high"] - g["ci_low"]).mean()),
                "baseline_rate": float(g["baseline_rate"].mean()),
            }
        )
    return out


def real_validation_report(
    path: str | Path,
    *,
    runs: int = 30,
    claims: int = 2000,
    reps: int = 199,
    seed: int = 7,
    progress: Callable[[str], None] | None = None,
) -> tuple[dict, pd.DataFrame]:
    """Everything above, as one result for the report and the website."""
    from probative import __version__

    units, ingest = load_uci_incidents(path)
    if progress:
        progress(f"{ingest['events']:,} events read as {ingest['rows_kept']:,} incidents")
    baseline = real_baseline(units)
    df = run_real_audit_validation(units, runs=runs, claims=claims, reps=reps, seed=seed, progress=progress)
    report = {
        "dataset": {"title": UCI_TITLE, "citation": UCI_CITATION, "url": UCI_URL, "license": UCI_LICENSE},
        "ingest": ingest,
        "baseline": baseline,
        "recovery": summarize_real(df),
        "settings": {"runs": runs, "claims": claims, "reps": reps, "seed": seed, "window_days": 7},
        "engine": __version__,
    }
    return report, df
