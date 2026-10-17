"""Every billing model, checked the same way: what was paid, what held, what to raise."""

import json

import pandas as pd
import pytest
from typer.testing import CliRunner

from probative import schema as S
from probative.audit import (
    check_seats,
    check_usage,
    load_audit_inputs,
    load_bill,
    load_usage,
    reconcile_bill,
    run_audit,
)
from probative.cli import app
from probative.ingest import Pseudonymizer
from probative.ingest.conversations import map_conversations, normalize_outcomes
from probative.simulate import SimSpec, generate

T0 = pd.Timestamp("2026-03-02 09:00")
KEY = Pseudonymizer(b"k" * 32)


@pytest.fixture(scope="module")
def desk(tmp_path_factory):
    sim = generate(SimSpec(n_teams=16, n_weeks=60, wave_weeks=(24, 30, 36), seed=3))
    paths = sim.write(tmp_path_factory.mktemp("desk"))
    generic = load_audit_inputs([paths["tickets"]], paths["sessions"], preset="servicenow", pseudonymizer=KEY)
    snow = load_audit_inputs(
        [paths["tickets"]], paths["interactions"], preset="servicenow", claims_preset="servicenow_va", pseudonymizer=KEY
    )
    return sim, paths, generic, snow


def test_servicenow_virtual_agent_export_becomes_outcomes():
    raw = pd.DataFrame(
        {
            "Number": ["IMS1", "IMS2", "IMS3", "IMS4", "IMS5", "IMS6", "IMS7"],
            "Opened for": ["ann", "bob", "cy", "di", "ed", "fay", "gus"],
            "Opened": ["2026-03-02 09:00"] * 7,
            "Type": ["chat"] * 7,
            "Virtual agent": ["true", "true", "true", "true", "true", "true", "false"],
            "Agent chat": ["false", "false", "true", "false", "false", "false", "true"],
            "State": [
                "Closed Complete",
                "closed_complete",
                "Closed Complete",
                "Closed Abandoned",
                "Faulted",
                "In Progress",
                "Closed Complete",
            ],
            "State reason": [
                "Topic Complete",
                "no_user_response",
                "User Ended",
                "No Activity",
                "Error",
                "",
                "Agent Ended",
            ],
        }
    )
    out, notes = map_conversations(raw, "servicenow_va")
    assert len(out) == 6 and "without the Virtual agent flag" in notes[0]  # the live-agent-only chat is left out
    assert list(out[S.SESSION_OUTCOME]) == ["resolved", "resolved", "escalated", "abandoned", "abandoned", "open"]
    assert list(out[S.OUTCOME_DETAIL])[:3] == ["Topic Complete", "No user response", "Live agent joined"]
    assert out[S.REQUESTER].tolist()[0] == "ann" and out[S.SESSION_ID].tolist()[0] == "IMS1"


def test_several_values_can_mean_resolved():
    s = pd.Series(["Assumed resolution", "Confirmed resolution", "Escalated to team", "No response", "Routed"])
    got = normalize_outcomes(s, s.index, "Assumed resolution, Confirmed resolution")
    assert got.tolist() == ["resolved", "resolved", "escalated", "abandoned", "escalated"]


def test_audit_shows_how_the_platform_closed_each_claim(desk):
    sim, _, _, snow = desk
    res = run_audit(snow, reps=99).to_dict()
    resolved = sim.truth["billing_models"]["interactions"]["servicenow_resolved"]
    assert res["claims"]["in_period"] == pytest.approx(resolved, abs=5)  # less any after the last ticket
    by = {d["detail"]: d for d in res["by_detail"]}
    # People who walked away were planted to come back far more often than finished topics.
    assert by["No User Response"]["ai_return_rate"] > 2 * by["Topic Complete"]["ai_return_rate"]
    assert "Virtual agent flag" in " ".join(res["notes"])


def test_per_conversation_bill_finds_every_planted_error(desk):
    sim, paths, generic, _ = desk
    bill, _ = load_bill(paths["bill_per_conversation"])
    res = reconcile_bill(bill, generic.conversations, generic.tickets, model="conversation", reps=49).to_dict()
    truth = sim.truth["billing_models"]["per_conversation"]
    by = {r["code"]: r["count"] for r in res["reasons"]}
    assert by == {"duplicate": truth["duplicates"], "no_record": truth["unknown_ids"]}  # escalations are billable
    codes = {x["code"] for x in res["paid_for"]}
    assert {"held", "came_back", "escalated", "abandoned"} <= codes
    assert res["per_held"]["estimate"] > truth["price"]  # paying for every conversation costs more per held resolution
    assert "were handed to a person, abandoned, or came back" in res["headline"]
    json.dumps(res)


def test_billable_outcomes_from_the_contract_are_enforced(desk):
    _, paths, generic, _ = desk
    bill, _ = load_bill(paths["bill_per_conversation"])
    res = reconcile_bill(
        bill, generic.conversations, generic.tickets, model="conversation", billable="resolved", reps=49
    ).to_dict()
    by = {r["code"]: r["count"] for r in res["reasons"]}
    escalated_or_abandoned = int((generic.conversations[S.SESSION_OUTCOME] != "resolved").sum())
    assert by["not_billable"] == pytest.approx(escalated_or_abandoned, abs=5)  # less the few billed twice


def test_per_session_bill_by_day_finds_the_overcount(desk):
    sim, paths, generic, _ = desk
    bill, _ = load_bill(paths["bill_per_session"])
    res = reconcile_bill(bill, generic.conversations, generic.tickets, model="session", price=0.9, reps=49).to_dict()
    truth = sim.truth["billing_models"]["per_session"]
    assert res["billed"]["count"] == truth["billed"]
    assert {r["code"]: r["count"] for r in res["reasons"]} == {"above_records": truth["overcount"]}
    assert res["disputed"]["amount"] == pytest.approx(truth["overcount"] * 0.9)


def _small():
    conv = pd.DataFrame(
        {
            S.SESSION_ID: ["C1", "C2", "C3", "C4"],
            S.REQUESTER: ["p1", "p1", "p1", "p2"],
            S.SESSION_STARTED: [T0, T0 + pd.Timedelta(hours=3), T0 + pd.Timedelta(hours=30), T0],
            S.SESSION_OUTCOME: ["resolved", "escalated", "resolved", "abandoned"],
        }
    )
    tickets = pd.DataFrame(
        {
            S.UNIT_ID: ["t1", "t2"],
            S.REQUESTER: ["p1", "zz"],
            S.OPENED: [T0 + pd.Timedelta(hours=4), T0 + pd.Timedelta(days=40)],
            S.RESOLVED: [T0 + pd.Timedelta(hours=8), T0 + pd.Timedelta(days=41)],
        }
    )
    return conv, tickets


def test_a_second_item_inside_one_session_is_disputed():
    conv, tickets = _small()
    bill = pd.DataFrame({S.BILL_ITEM: ["C1", "C2", "C3", "C4"], S.BILLED_AT: [T0] * 4, S.AMOUNT: [0.5] * 4})
    res = reconcile_bill(bill, conv, tickets, model="session", period="2026-03", reps=19).to_dict()
    assert [(x["item_id"], x["reason_code"]) for x in res["lines"]] == [("C2", "same_session")]  # C3 is 30 h later


def test_included_allowance_charges_only_beyond_it():
    conv, tickets = _small()
    bill = pd.DataFrame({S.BILL_ITEM: ["C1", "C1", "C2", "C3", "C4"], S.BILLED_AT: [T0] * 5})
    res = reconcile_bill(
        bill, conv, tickets, model="conversation", period="2026-03", price=0.30, included=3, reps=19
    ).to_dict()
    assert res["billed"]["amount"] == pytest.approx(2 * 0.30)  # five billed, three included
    assert res["disputed"]["count"] == 1 and res["disputed"]["amount"] == pytest.approx(0.30)


def test_usage_check_follows_units_to_outcomes_and_testing(desk):
    sim, paths, _, snow = desk
    usage, files, _ = load_usage([paths["now_assist_usage"]], pseudonymizer=KEY)
    res = check_usage(
        usage, snow.conversations, snow.tickets, period="2026-01", unit_name="assists", unit_price=0.5, reps=49
    ).to_dict()
    raw = pd.read_csv(paths["now_assist_usage"], parse_dates=["sys_created_on"])
    month = raw[(raw["sys_created_on"] >= "2026-01-01") & (raw["sys_created_on"] < "2026-02-01")]
    assert res["usage"]["total"] == pytest.approx(month["assists"].sum())
    assert res["usage"]["non_production"] == pytest.approx(month.loc[month["instance"] != "acme", "assists"].sum())
    va = month[month["skill"] == "Now Assist in Virtual Agent"]["assists"].sum()
    assert res["usage"]["linked_units"] == pytest.approx(va)  # every Virtual Agent unit traced to its conversation
    assert {x["code"] for x in res["review"]} >= {"non_production", "not_delivered"}
    assert res["per_held"]["basis"] == "conversation units"
    json.dumps(res)


def test_pool_forecast_arithmetic():
    days = pd.date_range("2026-01-01", periods=100, freq="D")
    usage = pd.DataFrame({S.USED_AT: days, S.UNITS: 10.0, S.FEATURE: "skill", "_nonprod": False})
    res = check_usage(
        usage, period="2026-01-01:2026-04-11", pool_units=1500, pool_price=750, term_start="2026-01-01"
    ).to_dict()
    p = res["pool"]
    assert p["used"] == 1000 and p["daily_rate"] == pytest.approx(10)
    assert p["runs_out_on"] == "2026-05-30"  # 500 left at 10 a day after 10 April
    assert p["overage_units"] == pytest.approx(10 * (365 - 100) - 500)
    assert res["spend"]["amount"] == pytest.approx(1000 * 0.5)


def test_seat_check_prorates_the_fee_and_counts_active_users():
    conv, tickets = _small()
    res = check_seats(conv, tickets, fee=36_500, seats=10, term_start="2026-01-01", period="2026-03", reps=19).to_dict()
    assert res["spend"]["amount"] == pytest.approx(36_500 * 31 / 365)
    assert res["seats"]["active"] == 2 and res["seats"]["idle"] == 8
    assert res["review"][0]["code"] == "idle_seats" and "only 20%" in res["attention"][0]


def test_cli_checks_every_billing_model(desk, tmp_path, monkeypatch):
    _, paths, _, _ = desk
    monkeypatch.chdir(tmp_path)
    common = ["--claims", str(paths["sessions"]), "--tickets", str(paths["tickets"]), "--preset", "servicenow"]
    snow = ["--claims", str(paths["interactions"]), "--claims-preset", "servicenow_va"]
    runs = [
        ["reconcile", str(paths["bill_per_conversation"]), "--model", "conversation", *common, "--period", "2026-01"],
        ["reconcile", str(paths["bill_per_session"]), "--model", "session", "--price", "0.9", *common],
        [
            "usage",
            str(paths["now_assist_usage"]),
            *snow,
            "--tickets",
            str(paths["tickets"]),
            "--preset",
            "servicenow",
            "--unit",
            "assists",
            "--pool",
            "40000",
            "--pool-price",
            "20000",
            "--term-start",
            "2025-06-01",
        ],
        ["seats", "--fee", "120000", "--seats", "6000", "--seat-label", "employees", *common, "--period", "2026-01"],
        ["audit", "--tickets", str(paths["tickets"]), "--preset", "servicenow", *snow],
    ]
    for args in runs:
        r = CliRunner().invoke(app, [*args, "--reps", "49"])
        assert r.exit_code == 0, (args[0], r.output)
    assert (tmp_path / "out" / "usage" / "spend-check.html").exists()
    assert (tmp_path / "out" / "seats" / "spend-check.html").exists()
    assert "Live agent joined" not in (tmp_path / "out" / "audit" / "resolution-audit.html").read_text()
    assert "No User Response" in (tmp_path / "out" / "audit" / "resolution-audit.html").read_text()
