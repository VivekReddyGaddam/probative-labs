import json

import pytest
import yaml
from typer.testing import CliRunner

from probative.audit import AuditError, audit_closures, load_closure_inputs
from probative.cli import app
from probative.ingest import Pseudonymizer
from probative.simulate import SimSpec, demo_config, generate


@pytest.fixture(scope="module")
def sim_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("closures")
    sim = generate(
        SimSpec(
            n_teams=24, n_weeks=56, wave_weeks=(24, 30, 36), ai_closure_share=0.4, ai_closure_failure_rate=0.2, seed=12
        )
    )
    return sim, sim.write(d / "data"), d


def test_planted_reversals_are_recovered(sim_files):
    _, paths, _ = sim_files
    inp = load_closure_inputs(
        [paths["tickets"]], ai_resolvers=["ai agent"], preset="servicenow", pseudonymizer=Pseudonymizer(b"k" * 32)
    )
    res = audit_closures(inp.tickets, reps=299, flag_source=inp.flag_source).to_dict()
    f = res["failed_share"]
    assert abs(f["estimate"] - 0.2) < 0.04 and f["ci_low"] > 0.1
    assert res["reversals"]["ai_reopened"] > res["reversals"]["people_reopened"]
    blob = json.dumps(res)
    assert "AGT0" not in blob and "USR0" not in blob  # people stay pseudonymized


def test_the_ai_must_be_named(sim_files):
    _, paths, _ = sim_files
    with pytest.raises(AuditError, match="AI Agent"):  # the error lists the most common values
        load_closure_inputs([paths["tickets"]], preset="servicenow")


def test_cli_closures(sim_files):
    sim, _, d = sim_files
    raw = demo_config("data", sim.truth)
    raw["audit"]["ai_resolvers"] = ["AI Agent"]
    (d / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    r = CliRunner().invoke(app, ["closures", "-c", str(d / "probative.yaml"), "--reps", "99"])
    assert r.exit_code == 0, r.output
    assert "did not stick" in (d / "out" / "closures" / "closure-audit.html").read_text()
