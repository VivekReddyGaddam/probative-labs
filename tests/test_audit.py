import json

import numpy as np
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from probative import schema as S
from probative.audit import AuditError, audit_resolutions, load_audit_inputs, run_audit
from probative.benchmark import contribution
from probative.cli import app
from probative.config import ProjectConfig
from probative.ingest import Pseudonymizer
from probative.report import write_audit_pack, write_plan_pack
from probative.rollout import build_plan
from probative.simulate import SimSpec, demo_config, generate

T0 = pd.Timestamp("2026-03-02 09:00")


def _tickets(rows):
    df = pd.DataFrame(rows, columns=[S.UNIT_ID, S.REQUESTER, S.OPENED, S.RESOLVED, S.WORK_TYPE])
    for c in (S.OPENED, S.RESOLVED):
        df[c] = pd.to_datetime(df[c])
    return df


def _scenario(n_claims=400, returned=100, n_human=1000, human_returned=50):
    """Claims and human-resolved tickets with known return counts, one person each."""
    claims, tickets = [], []
    for i in range(n_claims):
        t = T0 + pd.Timedelta(hours=i)
        claims.append({S.REQUESTER: f"c{i}", S.SESSION_STARTED: t, S.WORK_TYPE: "Password"})
        if i < returned:
            tickets.append((f"r{i}", f"c{i}", t + pd.Timedelta(days=3), t + pd.Timedelta(days=4), "Password"))
    for i in range(n_human):
        o = T0 + pd.Timedelta(hours=i % 300)
        tickets.append((f"h{i}", f"h{i}", o, o + pd.Timedelta(hours=2), "Password"))
        if i < human_returned:
            tickets.append((f"h{i}b", f"h{i}", o + pd.Timedelta(days=2), o + pd.Timedelta(days=3), "Password"))
    # Pad the export so every claim has a full week of follow-up.
    tickets.append(("pad", "nobody", T0 + pd.Timedelta(days=60), T0 + pd.Timedelta(days=61), "Other"))
    return pd.DataFrame(claims), _tickets(tickets)


def test_excess_return_rate_is_the_failed_share():
    claims, tickets = _scenario()
    res = audit_resolutions(claims, tickets, reps=199, price=1.5).to_dict()
    r, b = 100 / 400, 50 / 1000
    assert res["returns"]["ai_rate"] == pytest.approx(r)
    # Human returns open 2 days after the first ticket opens, so they count after its resolution too.
    assert res["returns"]["baseline_rate"] == pytest.approx(b, abs=0.01)
    f = res["failed_share"]
    assert f["estimate"] == pytest.approx((r - res["returns"]["baseline_rate"]) / (1 - res["returns"]["baseline_rate"]))
    assert f["ci_low"] < f["estimate"] < f["ci_high"] and f["excludes_zero"]
    assert res["billing"]["billed"] == pytest.approx(400 * 1.5)
    assert res["billing"]["paid_for_failed"]["estimate"] == pytest.approx(f["estimate"] * 400 * 1.5)
    assert res["held"]["estimate"] == pytest.approx(400 * (1 - f["estimate"]))
    json.dumps(res)


def test_recent_and_out_of_range_claims_are_not_checked():
    claims, tickets = _scenario()
    end = tickets[S.OPENED].max()
    extra = pd.DataFrame(
        {
            S.REQUESTER: ["late", "early", None],
            S.SESSION_STARTED: [end - pd.Timedelta(days=2), T0 - pd.Timedelta(days=30), T0],
            S.WORK_TYPE: ["Password"] * 3,
        }
    )
    res = audit_resolutions(pd.concat([claims, extra], ignore_index=True), tickets, reps=49).to_dict()
    c = res["claims"]
    assert c["too_recent"] == 1 and c["outside_ticket_dates"] == 1 and c["incomplete"] == 1
    assert c["checked"] == 400 and c["in_period"] == 401


def test_untyped_claims_match_on_the_person_only():
    claims, tickets = _scenario()
    res = audit_resolutions(claims.drop(columns=[S.WORK_TYPE]), tickets, reps=49).to_dict()
    assert res["settings"]["match"] == "any"
    assert any("no category" in n for n in res["notes"])


def test_unmatched_identities_lower_confidence():
    claims, tickets = _scenario()
    claims[S.REQUESTER] = "other-system-" + claims[S.REQUESTER]
    res = audit_resolutions(claims, tickets, reps=49).to_dict()
    assert res["coverage"]["id_overlap"] == 0
    assert res["confidence"]["level"] == "low"


def test_no_claims_in_range_is_an_error():
    claims, tickets = _scenario()
    claims[S.SESSION_STARTED] = T0 - pd.Timedelta(days=400)
    with pytest.raises(AuditError):
        audit_resolutions(claims, tickets, reps=49)


@pytest.mark.parametrize(("rate", "seed"), [(0.25, 3), (0.0, 4)])
def test_audit_recovers_planted_false_resolutions(rate, seed, tmp_path):
    sim = generate(SimSpec(n_teams=16, n_weeks=60, wave_weeks=(24, 30, 36), false_deflection_rate=rate, seed=seed))
    paths = sim.write(tmp_path / "data", "zendesk")
    inputs = load_audit_inputs(
        [paths["tickets"]], paths["sessions"], preset="zendesk", pseudonymizer=Pseudonymizer(b"k" * 32)
    )
    res = run_audit(inputs, reps=299).to_dict()
    f = res["failed_share"]
    # One seed per case; interval coverage over many seeds is checked in docs/validation.md.
    assert abs(f["estimate"] - rate) < 0.03
    assert f["ci_high"] - f["ci_low"] < 0.1
    assert res["coverage"]["id_overlap"] > 0.9
    blob = json.dumps(res)
    assert "USR0" not in blob  # people are pseudonymized
    out = write_audit_pack(res, tmp_path / "audit")
    assert "Resolution audit" in out["html"].read_text() and "## 2. Result" in out["markdown"].read_text()


@pytest.fixture(scope="module")
def demo_project(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    sim = generate(SimSpec(n_teams=16, n_weeks=60, wave_weeks=(24, 30, 36), seed=9))
    sim.write(d / "data")
    raw = demo_config("data", sim.truth)
    raw["analysis"]["bootstrap_reps"] = 49
    (d / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return d


def test_cli_audit_plan_and_benchmark(demo_project):
    runner = CliRunner()
    cfg = str(demo_project / "probative.yaml")
    r = runner.invoke(app, ["audit", "-c", cfg, "--reps", "99"])
    assert r.exit_code == 0, r.output
    assert "did not hold" in r.output and "$1.00 per resolution" in r.output
    audit_json = demo_project / "out" / "audit" / "audit.json"
    assert audit_json.exists()

    r = runner.invoke(app, ["plan", "pack", "-c", cfg, "--sims", "30", "--weeks-post", "8"])
    assert r.exit_code == 0, r.output
    plan = json.loads((demo_project / "out" / "plan" / "plan.json").read_text())
    assert len(plan["rollout"]["waves"]) == 3 and plan["comparison"]
    assert plan["expected_grade"]["grade"] == "B"  # randomized, but go-live is in the past and nothing was registered

    out = demo_project / "contribution.json"
    r = runner.invoke(app, ["benchmark", "--audit", str(audit_json), "--platform", "servicenow", "--out", str(out)])
    assert r.exit_code == 0, r.output
    body = json.loads(out.read_text())
    assert body["resolution_audit"]["failed_share"] is not None and "effect" not in body


def test_plan_without_a_comparison_group(demo_project, tmp_path):
    raw = yaml.safe_load((demo_project / "probative.yaml").read_text())
    rollout = tmp_path / "rollout.csv"
    pd.DataFrame({"team_id": [f"T{i}" for i in range(10)], "start_date": "2027-01-04", "kind": "wave"}).to_csv(
        rollout, index=False
    )
    raw["data"]["exposure"] = str(rollout)
    raw["data"]["work_units"] = str(tmp_path / "none.csv")
    cfg = ProjectConfig.from_dict(raw, tmp_path)
    plan = build_plan(cfg, [], sims=20)
    assert plan["expected_grade"]["grade"] == "insufficient" and not plan["comparison"]
    assert not plan["detectability"]["rows"]
    paths = write_plan_pack(plan, tmp_path / "plan")
    assert "nothing to compare against" in paths["html"].read_text()


def test_benchmark_contribution_has_no_names_or_money(small_project, tmp_path):
    from probative.pipeline import analyze

    cfg, _ = small_project
    res = analyze(cfg, key_file=str(tmp_path / "k.key")).to_dict()
    body = contribution(res, platform="servicenow")
    blob = json.dumps({k: v for k, v in body.items() if k != "excluded"})
    assert cfg.program.name not in blob and "DEMO-2025-001" not in blob and "SD-" not in blob
    assert "roi" not in blob.lower() and "cost" not in blob.lower()
    assert body["effect"]["grade"] == res["grade"]["grade"]
    assert body["effect"]["teams_band"] in {"8–19", "20–49"}
    assert len(body["sha256"]) == 64
    assert np.isfinite(body["effect"]["relative"])


def _bot_scenario(channel: bool, bot_creates: int = 150, fast_phone: int = 0):
    """400 claims; ``bot_creates`` of them open an incident in the conversation itself (2 minutes in), as
    ServiceNow's Create Incident topic does. One in 20 people comes back three days later, the same rate as after
    1000 human-resolved tickets: so nothing failed."""
    claims, tickets, conversations = [], [], []
    for i in range(400):
        t = T0 + pd.Timedelta(hours=i)
        claims.append({S.REQUESTER: f"c{i}", S.SESSION_STARTED: t})
        conversations.append({S.REQUESTER: f"c{i}", S.SESSION_STARTED: t})
        if i < bot_creates:
            tickets.append((f"va{i}", f"c{i}", t + pd.Timedelta(minutes=2), t + pd.Timedelta(hours=5), "Virtual Agent"))
        elif i < bot_creates + fast_phone:
            tickets.append((f"ph{i}", f"c{i}", t + pd.Timedelta(minutes=10), t + pd.Timedelta(hours=5), "Phone"))
        if i % 20 == 0:
            tickets.append((f"r{i}", f"c{i}", t + pd.Timedelta(days=3), t + pd.Timedelta(days=4), "Phone"))
    for i in range(1000):
        o = T0 + pd.Timedelta(hours=i % 300)
        tickets.append((f"h{i}", f"h{i}", o, o + pd.Timedelta(hours=2), "Phone"))
        if i % 20 == 0:
            tickets.append((f"h{i}b", f"h{i}", o + pd.Timedelta(days=2), o + pd.Timedelta(days=3), "Email"))
    tickets.append(("pad", "nobody", T0 + pd.Timedelta(days=60), T0 + pd.Timedelta(days=61), "Phone"))
    df = pd.DataFrame(tickets, columns=[S.UNIT_ID, S.REQUESTER, S.OPENED, S.RESOLVED, S.CHANNEL])
    for c in (S.OPENED, S.RESOLVED):
        df[c] = pd.to_datetime(df[c])
    if not channel:
        df = df.drop(columns=[S.CHANNEL])
    return pd.DataFrame(claims), df, pd.DataFrame(conversations)


def test_a_ticket_the_ai_agent_created_is_a_handoff_not_a_return():
    claims, tickets, convs = _bot_scenario(channel=True)
    res = audit_resolutions(claims, tickets, conversations=convs, reps=199).to_dict()
    # The 150 conversations that opened an incident themselves are left out, not counted as failures.
    assert res["claims"]["handed_off"] == 150 and res["claims"]["checked"] == 250
    assert res["returns"]["ai_rate"] == pytest.approx(12 / 250)
    assert abs(res["failed_share"]["estimate"]) < 0.03 and not res["failed_share"]["excludes_zero"]
    assert any("created a ticket themselves" in n for n in res["notes"])
    every = [s for s in res["sensitivity"] if s["check"].startswith("Every ticket counts")]
    assert every and every[0]["failed_share"] > 0.3  # what the old count would have called a failure


def test_without_a_channel_the_first_minutes_do_not_count_on_either_side():
    claims, tickets, convs = _bot_scenario(channel=False)
    res = audit_resolutions(claims, tickets, conversations=convs, reps=199).to_dict()
    assert res["claims"]["handed_off"] == 0 and res["claims"]["checked"] == 400
    assert res["returns"]["ai_rate"] == pytest.approx(20 / 400)  # the incidents opened 2 minutes in don't count
    assert abs(res["failed_share"]["estimate"]) < 0.03
    assert any("no channel" in n for n in res["notes"])
    assert res["settings"]["channel_known"] is False


def test_a_fast_return_through_another_channel_still_counts():
    claims, tickets, convs = _bot_scenario(channel=True, bot_creates=0, fast_phone=100)
    res = audit_resolutions(claims, tickets, conversations=convs, reps=199).to_dict()
    assert res["claims"]["handed_off"] == 0
    # 100 phone tickets 10 minutes after the chat, plus the 3-day returns among the other 300.
    assert res["returns"]["ai_rate"] == pytest.approx((100 + 15) / 400)
    assert res["failed_share"]["excludes_zero"]
    assert any("Phone (" in n for n in res["notes"])  # the fast tickets' channels are shown


def test_a_ticket_from_a_later_conversation_still_counts_against_an_earlier_claim():
    claims, tickets, convs = _bot_scenario(channel=True, bot_creates=0)
    # c1 comes back to the bot 10 minutes later in a conversation that ends with an incident.
    later = T0 + pd.Timedelta(hours=1, minutes=10)
    convs = pd.concat([convs, pd.DataFrame({S.REQUESTER: ["c1"], S.SESSION_STARTED: [later]})], ignore_index=True)
    extra = pd.DataFrame(
        {
            S.UNIT_ID: ["va-late"],
            S.REQUESTER: ["c1"],
            S.OPENED: [later + pd.Timedelta(minutes=3)],
            S.RESOLVED: [later + pd.Timedelta(hours=2)],
            S.CHANNEL: ["virtual_agent"],
        }
    )
    res = audit_resolutions(claims, pd.concat([tickets, extra], ignore_index=True), conversations=convs, reps=49)
    res = res.to_dict()
    assert res["claims"]["handed_off"] == 0  # the incident came from the later conversation, not the claim
    assert res["returns"]["ai_rate"] == pytest.approx(21 / 400)
