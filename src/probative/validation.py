"""Validation on simulated data with planted effects, including zero.

A causal claim that is wrong destroys trust in the method, so the estimator is
checked the way it will be used: full pipeline, realistic messy data, known truth.
For each planted effect the harness reports bias, error, how often the 95% range
contains the truth (coverage) and how often a zero effect is wrongly declared
(false positive rate).

"Truth" is the planted expected change for the tickets AI teams handled
(``effort_att_expected``), which is what the team bootstrap targets. Coverage of
the realized change, which also contains ticket-level noise, is reported beside it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from probative.config import ProjectConfig
from probative.pipeline import analyze
from probative.simulate import SimSpec, demo_config, generate


def run_validation(
    *,
    effects: tuple[float, ...] = (-0.15, 0.0),
    runs: int = 10,
    reps: int = 99,
    seed: int = 1,
    n_teams: int = 30,
    n_weeks: int = 60,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    rows = []
    waves = (n_weeks // 2, n_weeks // 2 + 6, n_weeks // 2 + 12)
    with tempfile.TemporaryDirectory(prefix="probative-validate-") as tmp:
        for effect in effects:
            for i in range(runs):
                run_seed = seed * 100_003 + i * 7919 + int(abs(effect) * 1000)
                spec = SimSpec(
                    n_teams=n_teams,
                    n_weeks=n_weeks,
                    wave_weeks=waves,
                    effort_effect=effect,
                    resolution_effect=effect / 2,
                    seed=run_seed,
                    extraction_lag_days=365,  # no right-censoring, so the truth covers the analysed tickets
                )
                sim = generate(spec)
                d = Path(tmp) / f"run-{effect}-{i}"
                sim.write(d / "data")
                raw = demo_config("data", sim.truth)
                raw["analysis"]["bootstrap_reps"] = reps
                raw["analysis"]["seed"] = run_seed
                (d / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
                cfg = ProjectConfig.load(d / "probative.yaml")
                res = analyze(cfg, key_file=str(Path(tmp) / "validation.key")).to_dict()
                p = res["primary"]
                truth = sim.truth["effort_att_expected"]
                realized = sim.truth["effort_att_hours_per_ticket"]
                diag = {c["name"]: c["status"] for c in res["diagnostics"]}
                rows.append(
                    {
                        "planted_relative": effect,
                        "run": i,
                        "truth": truth,
                        "estimate": p["estimate"],
                        "ci_low": p["ci_low"],
                        "ci_high": p["ci_high"],
                        "realized": realized,
                        "covered": p["ci_low"] <= truth <= p["ci_high"],
                        "covered_realized": p["ci_low"] <= realized <= p["ci_high"],
                        "rejects_zero": bool(p["ci_low"] > 0 or p["ci_high"] < 0),
                        "grade": res["grade"]["grade"],
                        "pretrend": diag.get("Pre-trend test"),
                        "placebo_dates": diag.get("Placebo dates"),
                    }
                )
                if progress:
                    progress(
                        f"effect {effect:+.2f} run {i + 1}/{runs}: estimate {p['estimate']:+.4f} "
                        f"vs truth {truth:+.4f} ({res['grade']['grade']})"
                    )
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(error=df["estimate"] - df["truth"]).groupby("planted_relative")
    return pd.DataFrame(
        {
            "runs": g.size(),
            "mean_truth": g["truth"].mean(),
            "bias": g["error"].mean(),
            "rmse": g["error"].apply(lambda e: float(np.sqrt((e**2).mean()))),
            "coverage": g["covered"].mean(),
            "coverage_realized": g["covered_realized"].mean(),
            "rejects_zero": g["rejects_zero"].mean(),
            "pretrend_pass": g["pretrend"].apply(lambda s: float((s == "pass").mean())),
            "placebo_pass": g["placebo_dates"].apply(lambda s: float((s == "pass").mean())),
            "graded_A_to_C": g["grade"].apply(lambda s: float(s.isin(["A", "B", "C"]).mean())),
        }
    ).reset_index()


def run_audit_validation(
    *,
    rates: tuple[float, ...] = (0.0, 0.12, 0.3),
    runs: int = 30,
    reps: int = 199,
    seed: int = 1,
    n_teams: int = 30,
    n_weeks: int = 60,
    formats: tuple[str, ...] = ("servicenow", "zendesk"),
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Check the resolution audit against a planted share of false resolutions, including zero.

    Formats alternate between runs: ServiceNow exports type issues by category and subcategory,
    Zendesk exports by category only, so both ways of matching a return are exercised.
    """
    import secrets

    from probative.audit import load_audit_inputs, run_audit
    from probative.ingest import Pseudonymizer

    rows = []
    waves = (n_weeks // 2, n_weeks // 2 + 6, n_weeks // 2 + 12)
    key = Pseudonymizer(secrets.token_bytes(32))
    with tempfile.TemporaryDirectory(prefix="probative-validate-audit-") as tmp:
        for rate in rates:
            for i in range(runs):
                run_seed = seed * 100_003 + i * 7919 + int(rate * 1000) + 17
                fmt = formats[i % len(formats)]
                sim = generate(
                    SimSpec(
                        n_teams=n_teams, n_weeks=n_weeks, wave_weeks=waves, false_deflection_rate=rate, seed=run_seed
                    )
                )
                paths = sim.write(Path(tmp) / f"audit-{rate}-{i}", fmt)
                inputs = load_audit_inputs([paths["tickets"]], paths["sessions"], preset=fmt, pseudonymizer=key)
                res = run_audit(inputs, reps=reps, seed=run_seed).to_dict()
                f = res["failed_share"]
                t = sim.truth
                rows.append(
                    {
                        "planted_rate": rate,
                        "run": i,
                        "format": fmt,
                        "estimate": f["estimate"],
                        "ci_low": f["ci_low"],
                        "ci_high": f["ci_high"],
                        "realized": t["false_deflections"] / max(1, t["false_deflections"] + t["verified_deflections"]),
                        "covered": f["ci_low"] <= rate <= f["ci_high"],
                        "flags_excess": bool(f["ci_low"] > 0),
                        "confidence": res["confidence"]["level"],
                        "claims": res["claims"]["checked"],
                    }
                )
                if progress:
                    progress(
                        f"rate {rate:.0%} run {i + 1}/{runs} ({fmt}): estimate {f['estimate']:.3f} "
                        f"[{f['ci_low']:.3f}, {f['ci_high']:.3f}]"
                    )
    return pd.DataFrame(rows)


def summarize_audit(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(error=df["estimate"] - df["planted_rate"]).groupby("planted_rate")
    return pd.DataFrame(
        {
            "runs": g.size(),
            "mean_claims": g["claims"].mean(),
            "bias": g["error"].mean(),
            "rmse": g["error"].apply(lambda e: float(np.sqrt((e**2).mean()))),
            "coverage": g["covered"].mean(),
            "flags_excess": g["flags_excess"].mean(),
            "confidence_high": g["confidence"].apply(lambda s: float((s == "high").mean())),
        }
    ).reset_index()


def run_watch_validation(
    *,
    runs: int = 30,
    reps: int = 199,
    seed: int = 1,
    n_teams: int = 30,
    n_weeks: int = 60,
    before: float = 0.12,
    after: float = 0.24,
    weeks_since: int = 6,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """False alarms with no regression; detection and dating when the false rate jumps ``weeks_since`` weeks
    before the end of the data."""
    import secrets

    from probative.audit import load_audit_inputs, watch
    from probative.ingest import Pseudonymizer

    rows = []
    waves = (n_weeks // 2, n_weeks // 2 + 6, n_weeks // 2 + 12)
    key = Pseudonymizer(secrets.token_bytes(32))
    with tempfile.TemporaryDirectory(prefix="probative-validate-watch-") as tmp:
        for scenario in ("none", "regression"):
            for i in range(runs):
                run_seed = seed * 100_003 + i * 7919 + (31 if scenario == "regression" else 0)
                reg_week = n_weeks - weeks_since if scenario == "regression" else None
                spec = SimSpec(
                    n_teams=n_teams,
                    n_weeks=n_weeks,
                    wave_weeks=waves,
                    false_deflection_rate=before,
                    regression_week=reg_week,
                    false_deflection_rate_after=after if reg_week else None,
                    seed=run_seed,
                )
                sim = generate(spec)
                paths = sim.write(Path(tmp) / f"watch-{scenario}-{i}")
                inputs = load_audit_inputs(
                    [paths["tickets"]], paths["sessions"], preset="servicenow", pseudonymizer=key
                )
                res = watch(inputs.conversations, inputs.tickets, reps=reps, seed=run_seed).to_dict()
                cp = res.get("change_point") or {}
                truth_week = (spec.start + pd.Timedelta(weeks=reg_week)).isoformat() if reg_week else None
                weeks_off = (
                    abs((pd.Timestamp(cp["week"]) - pd.Timestamp(truth_week)).days) // 7 if cp and truth_week else None
                )
                rows.append(
                    {
                        "scenario": scenario,
                        "run": i,
                        "status": res["status"],
                        "alarm": res["status"] == "regression",
                        "dated_within_1_week": weeks_off is not None and weeks_off <= 1,
                    }
                )
                if progress:
                    progress(f"{scenario} run {i + 1}/{runs}: {res['status']}")
    return pd.DataFrame(rows)


def summarize_watch(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("scenario")
    return pd.DataFrame(
        {
            "runs": g.size(),
            "regression_alarm": g["alarm"].mean(),
            "watch_or_alarm": g["status"].apply(lambda s: float(s.isin(["regression", "watch"]).mean())),
            "dated_within_1_week": g["dated_within_1_week"].mean(),
        }
    ).reset_index()


def run_closure_validation(
    *,
    rates: tuple[float, ...] = (0.0, 0.1, 0.25),
    runs: int = 30,
    reps: int = 199,
    seed: int = 1,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Check the action audit against a planted share of AI closures that get reopened, including zero."""
    import secrets

    from probative.audit import audit_closures, load_closure_inputs
    from probative.ingest import Pseudonymizer

    rows = []
    key = Pseudonymizer(secrets.token_bytes(32))
    with tempfile.TemporaryDirectory(prefix="probative-validate-closures-") as tmp:
        for rate in rates:
            for i in range(runs):
                run_seed = seed * 100_003 + i * 7919 + int(rate * 1000) + 53
                sim = generate(
                    SimSpec(
                        n_teams=30,
                        n_weeks=60,
                        wave_weeks=(30, 36, 42),
                        ai_closure_share=0.4,
                        ai_closure_failure_rate=rate,
                        seed=run_seed,
                    )
                )
                paths = sim.write(Path(tmp) / f"closures-{rate}-{i}")
                inp = load_closure_inputs(
                    [paths["tickets"]], ai_resolvers=["AI Agent"], preset="servicenow", pseudonymizer=key
                )
                f = audit_closures(inp.tickets, reps=reps, seed=run_seed).to_dict()["failed_share"]
                rows.append(
                    {
                        "planted_rate": rate,
                        "run": i,
                        "estimate": f["estimate"],
                        "ci_low": f["ci_low"],
                        "ci_high": f["ci_high"],
                        "covered": f["ci_low"] <= rate <= f["ci_high"],
                        "flags_excess": bool(f["ci_low"] > 0),
                    }
                )
                if progress:
                    progress(f"rate {rate:.0%} run {i + 1}/{runs}: {f['estimate']:.3f}")
    return pd.DataFrame(rows)


def summarize_closures(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(error=df["estimate"] - df["planted_rate"]).groupby("planted_rate")
    return pd.DataFrame(
        {
            "runs": g.size(),
            "bias": g["error"].mean(),
            "rmse": g["error"].apply(lambda e: float(np.sqrt((e**2).mean()))),
            "coverage": g["covered"].mean(),
            "flags_excess": g["flags_excess"].mean(),
        }
    ).reset_index()


def run_bakeoff_validation(
    *,
    runs: int = 20,
    reps: int = 99,
    seed: int = 1,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    """Does the bake-off pick the better vendor, and decline to pick between equal ones?"""
    from datetime import date, timedelta

    from probative.bakeoff import plan_bakeoff, run_bakeoff

    scenarios = {
        "A better (-20% vs -5%)": {"Vendor A": -0.20, "Vendor B": -0.05},
        "equal (-10% vs -10%)": {"Vendor A": -0.10, "Vendor B": -0.10},
    }
    start = date(2025, 1, 6) + timedelta(weeks=30)
    rows = []
    with tempfile.TemporaryDirectory(prefix="probative-validate-bakeoff-") as tmp:
        for label, effects in scenarios.items():
            for i in range(runs):
                run_seed = seed * 100_003 + i * 7919 + len(label)
                d = Path(tmp) / f"bakeoff-{len(rows)}"
                sim = generate(
                    SimSpec(
                        n_teams=30,
                        n_weeks=40,
                        never_treated_share=1.0,
                        effort_effect=0.0,
                        deflection=False,
                        seed=run_seed,
                    )
                )
                paths = sim.write(d / "data")
                plan = plan_bakeoff(
                    sim.teams[["team_id"]],
                    list(effects),
                    first_start=start,
                    weeks=10,
                    control_share=0.34,
                    seed=run_seed,
                )
                plan.to_csv(d / "bakeoff.csv", index=False)
                t = pd.read_csv(paths["tickets"])
                arm = t["assignment_group"].map(dict(zip(plan["team_id"], plan["arm"], strict=True)))
                after = pd.to_datetime(t["opened_at"], errors="coerce") >= pd.Timestamp(start)
                for vendor, effect in effects.items():
                    m = after & (arm == vendor)
                    t.loc[m, "time_worked"] = (t.loc[m, "time_worked"] * (1 + effect)).round()
                t.to_csv(paths["tickets"], index=False)
                raw = demo_config("data", sim.truth)
                raw["data"]["self_service"] = None
                raw["analysis"]["bootstrap_reps"] = reps
                raw["bakeoff"] = {
                    "assignment": "bakeoff.csv",
                    "start": start.isoformat(),
                    "end": (start + timedelta(weeks=10, days=-1)).isoformat(),
                    "vendors": [{"name": v} for v in effects],
                }
                (d / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
                res = run_bakeoff(ProjectConfig.load(d / "probative.yaml"), key_file=str(Path(tmp) / "k.key"))
                dec = res["decision"]
                rows.append(
                    {
                        "scenario": label,
                        "run": i,
                        "clear": dec["clear"],
                        "winner": dec.get("winner"),
                        "leader": dec.get("leader") or dec.get("winner"),
                    }
                )
                if progress:
                    progress(f"{label} run {i + 1}/{runs}: {dec['text'][:70]}")
    return pd.DataFrame(rows)


def summarize_bakeoff(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("scenario")
    return pd.DataFrame(
        {
            "runs": g.size(),
            "clear_winner": g["clear"].mean(),
            "winner_is_A": g["winner"].apply(lambda s: float((s == "Vendor A").mean())),
            "wrong_clear_winner": g.apply(lambda x: float(((x["winner"] == "Vendor B") & x["clear"]).mean())),
        }
    ).reset_index()
