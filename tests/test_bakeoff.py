from datetime import date, timedelta

import pandas as pd
import yaml
from typer.testing import CliRunner

from probative.bakeoff import CONTROL, decide, plan_bakeoff, run_bakeoff
from probative.cli import app
from probative.config import ProjectConfig
from probative.simulate import SimSpec, demo_config, generate

START = date(2025, 1, 6) + timedelta(weeks=30)


def bakeoff_project(tmp, effects: dict[str, float], seed: int = 3, reps: int = 99) -> ProjectConfig:
    """A simulated desk with no AI, then a 10-week trial where each vendor's teams get its planted effect."""
    sim = generate(
        SimSpec(n_teams=30, n_weeks=40, never_treated_share=1.0, effort_effect=0.0, deflection=False, seed=seed)
    )
    paths = sim.write(tmp / "data")
    plan = plan_bakeoff(
        sim.teams[["team_id"]], list(effects), first_start=START, weeks=10, control_share=0.34, seed=seed
    )
    plan.to_csv(tmp / "bakeoff.csv", index=False)
    t = pd.read_csv(paths["tickets"])
    arm = t["assignment_group"].map(dict(zip(plan["team_id"], plan["arm"], strict=True)))
    after = pd.to_datetime(t["opened_at"], errors="coerce") >= pd.Timestamp(START)
    for vendor, effect in effects.items():
        m = after & (arm == vendor)
        t.loc[m, "time_worked"] = (t.loc[m, "time_worked"] * (1 + effect)).round()
    t.to_csv(paths["tickets"], index=False)
    raw = demo_config("data", sim.truth)
    raw["data"]["self_service"] = None
    raw["analysis"]["bootstrap_reps"] = reps
    raw["bakeoff"] = {
        "assignment": "bakeoff.csv",
        "start": START.isoformat(),
        "end": (START + timedelta(weeks=10, days=-1)).isoformat(),
        "vendors": [{"name": v, "annual_price": 30000} for v in effects],
    }
    (tmp / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return ProjectConfig.load(tmp / "probative.yaml")


def test_lottery_assignment_is_balanced_and_recorded():
    teams = pd.DataFrame({"team_id": [f"T{i}" for i in range(30)]})
    plan = plan_bakeoff(teams, ["A", "B"], first_start=START, weeks=8, control_share=0.3, seed=1)
    counts = plan["arm"].value_counts()
    assert counts[CONTROL] == 9 and abs(counts["A"] - counts["B"]) <= 1
    assert plan["randomized"].all() and (plan["seed"] == 1).all()
    assert (plan.loc[plan["arm"] != CONTROL, "start_date"] == START.isoformat()).all()


def test_the_better_vendor_wins_clearly(tmp_path):
    cfg = bakeoff_project(tmp_path, {"Vendor A": -0.25, "Vendor B": -0.05})
    res = run_bakeoff(cfg, key_file=str(tmp_path / "k.key"))
    by = {v["vendor"]: v for v in res["vendors"]}
    assert by["Vendor A"]["relative"] < by["Vendor B"]["relative"]
    assert res["decision"]["winner"] == "Vendor A" and res["decision"]["clear"]


def test_decision_rules():
    def v(name, rel, lo, hi, net=None, grade="B", blocked=False):
        return {
            "vendor": name,
            "relative": rel,
            "relative_ci_low": lo,
            "relative_ci_high": hi,
            "grade": grade,
            "blocked": blocked,
            "net_annual_value": net,
        }

    assert decide([v("A", -0.2, -0.25, -0.15), v("B", -0.05, -0.1, 0.0)])["text"].startswith("A is the only")
    overlap = decide([v("A", -0.12, -0.18, -0.06, 10), v("B", -0.10, -0.15, -0.05, 20)])
    assert not overlap["clear"] and overlap["value_leader"] == "B" and "No clear winner" in overlap["text"]
    assert decide([v("A", -0.2, -0.25, -0.15, blocked=True)])["winner"] is None


def test_cli_bakeoff_plan(tmp_path):
    teams = tmp_path / "teams.csv"
    pd.DataFrame({"team_id": [f"T{i}" for i in range(24)]}).to_csv(teams, index=False)
    r = CliRunner().invoke(
        app,
        [
            "bakeoff",
            "plan",
            str(teams),
            "--vendors",
            "A,B",
            "--start",
            "2026-11-02",
            "--seed",
            "4",
            "--sims",
            "30",
            "--out",
            str(tmp_path / "plan.csv"),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "Chance of detecting" in r.output and (tmp_path / "plan.csv").exists()
