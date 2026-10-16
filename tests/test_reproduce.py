import json

import yaml
from typer.testing import CliRunner

from probative.benchmark import contribution
from probative.cli import app
from probative.simulate import SimSpec, demo_config, generate


def test_a_result_reproduces_and_tampering_is_caught(tmp_path):
    sim = generate(SimSpec(n_teams=16, n_weeks=56, wave_weeks=(24, 30, 36), seed=21))
    sim.write(tmp_path / "data")
    (tmp_path / "probative.yaml").write_text(yaml.safe_dump(demo_config("data", sim.truth), sort_keys=False))
    runner = CliRunner()
    cfg = str(tmp_path / "probative.yaml")
    assert runner.invoke(app, ["audit", "-c", cfg, "--reps", "99"]).exit_code == 0
    audit_json = tmp_path / "out" / "audit" / "audit.json"
    r = runner.invoke(app, ["reproduce", str(audit_json), "-c", cfg])
    assert r.exit_code == 0 and "Reproduced" in r.output, r.output

    tampered = json.loads(audit_json.read_text())
    tampered["failed_share"]["estimate"] = 0.01
    audit_json.write_text(json.dumps(tampered))
    r = runner.invoke(app, ["reproduce", str(audit_json), "-c", cfg])
    assert r.exit_code == 1 and "failed_share.estimate" in r.output


def test_contributions_carry_the_new_measures_without_money():
    closures = {
        "failed_share": {"estimate": 0.08, "ci_low": 0.05, "ci_high": 0.11},
        "confidence": {"level": "high"},
        "counts": {"ai_closures": 2400},
        "program": {"vendor": "V", "tool": "Agent"},
    }
    recon = {
        "disputed": {"billing_error_share": 0.02, "amount": 1234.0},
        "settings": {"contractual": True, "item_level": True},
    }
    body = contribution(closures=closures, reconciliation=recon, platform="servicenow")
    assert body["closure_audit"]["failed_share"] == 0.08 and body["closure_audit"]["closures_band"] == "1,000–9,999"
    assert body["billing"] == {"billing_error_share": 0.02, "contractual_definition": True, "item_level": True}
    assert "1234" not in json.dumps(body)
