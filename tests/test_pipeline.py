import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from probative.cli import app
from probative.config import ProjectConfig
from probative.pipeline import analyze
from probative.report import write_pack
from probative.rollout import check_registration, lock_registration
from probative.simulate import SimSpec, demo_config, generate


def test_end_to_end_recovers_truth_and_writes_pack(small_project, tmp_path):
    cfg, truth = small_project
    res = analyze(cfg, key_file=str(tmp_path / "k.key")).to_dict()
    p = res["primary"]
    assert p["ci_low"] <= truth["effort_att_hours_per_ticket"] <= p["ci_high"]
    assert res["grade"]["grade"] in {"A", "B", "C"}
    assert res["design"]["design"] == "staggered"
    headline_res = next(e for e in res["effects"] if e["metric"] == "resolution_hours" and e["view"] == "headline")
    adjusted_res = next(e for e in res["effects"] if e["metric"] == "resolution_hours" and e["view"] == "mix-adjusted")
    assert adjusted_res["estimate"] < headline_res["estimate"]  # the deflection trap
    assert res["value"]["hours"]["p50"] > 0
    assert {g["key"] for g in res["guardrails"]} >= {"reopen_rate", "csat", "false_deflection"}
    json.dumps(res)  # JSON-safe
    paths = write_pack(res, tmp_path / "out")
    html = paths["html"].read_text()
    assert "Evidence pack" in html and "<svg" in html and "Method appendix" in html
    assert "## 12. Recommendation" in paths["markdown"].read_text()


def test_identifiers_are_pseudonymized(small_project, tmp_path):
    cfg, _ = small_project
    res = analyze(cfg, key_file=str(tmp_path / "k.key")).to_dict()
    blob = json.dumps(res)
    assert "USR0" not in blob and "AGT0" not in blob and "INC1" not in blob


def test_guardrail_breach_withholds_value(tmp_path):
    sim = generate(SimSpec(n_teams=24, n_weeks=52, wave_weeks=(26, 32, 38), reopen_effect=0.06, seed=5))
    sim.write(tmp_path / "data")
    raw = demo_config("data", sim.truth)
    raw["analysis"]["bootstrap_reps"] = 99
    (tmp_path / "probative.yaml").write_text(yaml.safe_dump(raw))
    res = analyze(ProjectConfig.load(tmp_path / "probative.yaml"), key_file=str(tmp_path / "k.key")).to_dict()
    reopen = next(g for g in res["guardrails"] if g["key"] == "reopen_rate")
    assert reopen["status"] == "breach"
    assert res["blocked"] and res["value_status"] == "withheld"
    assert res["recommendation"]["action"] == "Fix"


def test_no_comparison_means_no_number(tmp_path):
    sim = generate(SimSpec(n_teams=12, n_weeks=40, wave_weeks=(20,), never_treated_share=0.0, seed=3))
    sim.write(tmp_path / "data")
    raw = demo_config("data", sim.truth)
    raw["analysis"]["bootstrap_reps"] = 49
    (tmp_path / "probative.yaml").write_text(yaml.safe_dump(raw))
    res = analyze(ProjectConfig.load(tmp_path / "probative.yaml"), key_file=str(tmp_path / "k.key")).to_dict()
    assert res["grade"]["grade"] == "insufficient"
    assert res["value"] is None and res["headline"].startswith("No number")
    write_pack(res, tmp_path / "out")


def test_registration_lock_and_deviation(small_project, tmp_path):
    cfg, _ = small_project
    path = tmp_path / "registration.yaml"
    from probative.cli import _guardrails

    lock_registration(cfg, _guardrails(cfg), path)
    with pytest.raises(FileExistsError):
        lock_registration(cfg, _guardrails(cfg), path)
    status = check_registration(cfg, _guardrails(cfg), path, first_rollout=None)
    assert status.exists and status.intact and not status.deviations
    changed = cfg.model_copy(deep=True)
    changed.hypothesis.primary_metric = "resolution_hours"
    status = check_registration(changed, _guardrails(changed), path, first_rollout=None)
    assert any("primary_metric" in d for d in status.deviations)
    record = yaml.safe_load(path.read_text())
    record["content"]["hypothesis"] = "edited later"
    path.write_text(yaml.safe_dump(record))
    assert not check_registration(cfg, _guardrails(cfg), path, None).intact


def test_cli_init_health_and_plan(tmp_path):
    runner = CliRunner()
    r = runner.invoke(app, ["init", str(tmp_path)])
    assert r.exit_code == 0 and (tmp_path / "probative.yaml").exists()
    r = runner.invoke(app, ["simulate", str(tmp_path / "data"), "--teams", "16", "--weeks", "40"])
    assert r.exit_code == 0, r.output
    teams = tmp_path / "data" / "teams.csv"
    r = runner.invoke(
        app, ["plan", "waves", str(teams), "--start", "2026-11-02", "--seed", "1", "--out", str(tmp_path / "plan.csv")]
    )
    assert r.exit_code == 0, r.output
    r = runner.invoke(app, ["plan", "power", "--teams", "12,24", "--weeks-post", "8", "--sims", "40"])
    assert r.exit_code == 0 and "Power to detect" in r.output


def test_cli_demo(tmp_path):
    r = CliRunner().invoke(app, ["demo", str(tmp_path / "demo"), "--reps", "49"])
    assert r.exit_code == 0, r.output
    assert Path(tmp_path / "demo" / "out" / "evidence-pack.html").exists()
    assert (tmp_path / "demo" / "registration.yaml").exists()
