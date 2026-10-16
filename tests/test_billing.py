import json

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from probative import schema as S
from probative.audit import load_audit_inputs, reconcile_bill
from probative.cli import app
from probative.ingest import Pseudonymizer
from probative.simulate import SimSpec, demo_config, generate

T0 = pd.Timestamp("2026-03-02 09:00")


def _data():
    conversations = pd.DataFrame(
        {
            S.SESSION_ID: ["C1", "C2", "C3", "C4", "C5", "C6"],
            S.REQUESTER: ["p1", "p2", "p3", "p4", "p5", "p6"],
            S.SESSION_STARTED: [T0, T0, T0, T0, T0 - pd.Timedelta(days=40), T0],
            S.SESSION_OUTCOME: ["resolved", "resolved", "escalated", "resolved", "resolved", "resolved"],
            S.WORK_TYPE: ["Password"] * 6,
        }
    )
    tickets = pd.DataFrame(
        {
            S.UNIT_ID: ["t1", "t2", "pad"],
            S.REQUESTER: ["p2", "p6", "nobody"],  # p2 came back about the same issue; p6 about something else
            S.OPENED: [T0 + pd.Timedelta(days=2), T0 + pd.Timedelta(days=1), T0 + pd.Timedelta(days=40)],
            S.RESOLVED: [T0 + pd.Timedelta(days=3), T0 + pd.Timedelta(days=2), T0 + pd.Timedelta(days=41)],
            S.WORK_TYPE: ["Password", "Hardware", "Other"],
        }
    )
    bill = pd.DataFrame(
        {
            S.BILL_ITEM: ["C1", "C2", "C3", "C4", "C4", "C5", "X9", "C6"],
            S.BILLED_AT: [T0] * 8,
            S.AMOUNT: [1.5] * 8,
        }
    )
    return bill, conversations, tickets


@pytest.mark.parametrize("contractual", [True, False])
def test_each_billing_error_is_found(contractual):
    bill, conv, tickets = _data()
    res = reconcile_bill(bill, conv, tickets, period="2026-03", contractual=contractual, reps=49).to_dict()
    by = {r["code"]: r["count"] for r in res["reasons"]}
    assert by["duplicate"] == 1 and by["no_record"] == 1 and by["not_resolved"] == 1 and by["outside_period"] == 1
    assert by.get("did_not_hold", 0) == (1 if contractual else 0)  # C2 came back about the same issue; C6 did not
    assert res["held"] == {"checked": 4, "came_back": 1, "held": 3, "too_recent": 0}  # C1, C2, C4 (once) and C6
    assert res["disputed"]["amount"] == pytest.approx(1.5 * (5 if contractual else 4))
    assert res["billed"] == {"count": 8, "amount": 12.0}
    dnh = [x for x in res["lines"] if x["reason_code"] == "did_not_hold"]
    assert (
        (dnh[0]["item_id"], dnh[0]["detail"]) == ("C2", "Repeat ticket 2.0 days later, same type of issue")
        if dnh
        else True
    )
    json.dumps(res)


def test_bills_without_item_ids_are_reconciled_by_day():
    _, conv, tickets = _data()
    bill = pd.DataFrame({S.BILLED_AT: [T0, T0 + pd.Timedelta(days=1)], S.QUANTITY: [9, 2]})
    res = reconcile_bill(bill, conv, tickets, period="2026-03", price=2.0, reps=49).to_dict()
    assert not res["settings"]["item_level"]
    # Four resolutions recorded on day one (C1, C2, C4, C6) against nine billed; none recorded on day two.
    assert res["disputed"]["count"] == 5 + 2 and res["disputed"]["amount"] == pytest.approx(14.0)


def test_simulated_bill_errors_are_all_found(tmp_path):
    sim = generate(SimSpec(n_teams=16, n_weeks=60, wave_weeks=(24, 30, 36), seed=3))
    paths = sim.write(tmp_path / "data")
    inputs = load_audit_inputs(
        [paths["tickets"]], paths["sessions"], preset="servicenow", pseudonymizer=Pseudonymizer(b"k" * 32)
    )
    from probative.audit import load_bill

    bill, _ = load_bill(paths["bill"])
    res = reconcile_bill(bill, inputs.conversations, inputs.tickets, period="2025-01-01:2026-03-01", reps=49).to_dict()
    by = {r["code"]: r["count"] for r in res["reasons"]}
    t = sim.truth["bill"]
    assert by["duplicate"] == t["duplicates"]
    assert by["no_record"] == t["unknown_ids"]
    assert by["not_resolved"] == t["escalations_billed"]


def test_cli_reconcile(tmp_path):
    sim = generate(SimSpec(n_teams=16, n_weeks=60, wave_weeks=(24, 30, 36), seed=4))
    sim.write(tmp_path / "data")
    raw = demo_config("data", sim.truth)
    (tmp_path / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    r = CliRunner().invoke(
        app,
        [
            "reconcile",
            str(tmp_path / "data" / "billing.csv"),
            "-c",
            str(tmp_path / "probative.yaml"),
            "--period",
            "2026-01",
            "--contractual",
            "--reps",
            "49",
        ],
    )
    assert r.exit_code == 0, r.output
    out = tmp_path / "out" / "reconcile"
    assert "did not hold under the contract" in (out / "dispute-pack.html").read_text()
    lines = (out / "disputed-items.csv").read_text().splitlines()
    assert lines[0].startswith("line,item_id") and len(lines) > 1
