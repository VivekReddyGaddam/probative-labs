from datetime import date, timedelta

import pytest
import yaml
from typer.testing import CliRunner

from probative.audit import load_audit_inputs, watch
from probative.cli import app
from probative.ingest import Pseudonymizer
from probative.simulate import SimSpec, demo_config, generate

START = date(2025, 1, 6)


def _inputs(tmp_path, regression: bool, seed: int = 5):
    spec = SimSpec(
        n_teams=30,
        n_weeks=60,
        wave_weeks=(30, 36, 42),
        regression_week=52 if regression else None,
        false_deflection_rate_after=0.30 if regression else None,
        seed=seed,
    )
    sim = generate(spec)
    paths = sim.write(tmp_path / "data")
    return (
        sim,
        paths,
        load_audit_inputs(
            [paths["tickets"]], paths["sessions"], preset="servicenow", pseudonymizer=Pseudonymizer(b"k" * 32)
        ),
    )


def test_a_planted_regression_is_caught_and_dated(tmp_path):
    _, _, inp = _inputs(tmp_path, regression=True)
    release = START + timedelta(weeks=52)
    res = watch(inp.conversations, inp.tickets, reps=199, events=[(release, "Model update")]).to_dict()
    assert res["status"] == "regression"
    main = res["checks"][0]
    assert main["metric"] == "Claimed resolutions that did not hold" and main["ci_low"] > 0
    assert res["change_point"]["week"] == release.isoformat()
    assert res["events"][0]["status"] == "regression"


def test_no_regression_raises_no_alarm(tmp_path):
    _, _, inp = _inputs(tmp_path, regression=False)
    res = watch(inp.conversations, inp.tickets, reps=199).to_dict()
    assert res["status"] != "regression"


def test_too_few_weeks_is_insufficient(tmp_path):
    _, _, inp = _inputs(tmp_path, regression=False)
    late = inp.conversations[inp.conversations["started_at"] >= "2026-01-20"]
    res = watch(late, inp.tickets, reps=49).to_dict()
    assert res["status"] == "insufficient" and "weeks of claims" in res["notes"][-1]


@pytest.mark.parametrize("regression", [True, False])
def test_cli_watch_exit_code(tmp_path, regression):
    sim, _, _ = _inputs(tmp_path, regression=regression)
    raw = demo_config("data", sim.truth)
    (tmp_path / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    r = CliRunner().invoke(app, ["watch", "-c", str(tmp_path / "probative.yaml"), "--reps", "99"])
    assert r.exit_code == (3 if regression else 0), r.output
    assert (tmp_path / "out" / "watch" / "regression-watch.html").exists()
