"""Real exports: day-first dates, event logs reduced to incidents, and failures planted in real data."""

import pandas as pd
import pytest

from probative import schema as S
from probative.audit import load_audit_inputs
from probative.ingest import Pseudonymizer
from probative.ingest.mapping import _to_datetime
from probative.simulate import SimSpec, generate
from probative.validation_real import load_uci_incidents, real_baseline, run_real_audit_validation


def test_day_first_dates_are_read_by_the_clear_cases():
    day_first = _to_datetime(pd.Series(["5/3/2016 12:00", "29/2/2016 01:16", None]))
    assert day_first.tolist()[:2] == [pd.Timestamp("2016-03-05 12:00"), pd.Timestamp("2016-02-29 01:16")]
    month_first = _to_datetime(pd.Series(["5/3/2016 12:00", "2/29/2016 01:16"]))
    assert month_first.tolist() == [pd.Timestamp("2016-05-03 12:00"), pd.Timestamp("2016-02-29 01:16")]


def test_an_event_log_becomes_one_row_per_incident(tmp_path):
    rows = [
        # number, state, sys_mod_count, caller, opened, resolved, category
        ("INC1", "New", "0", "Caller 1", "29/2/2016 01:16", "?", "Category 5"),
        ("INC1", "Resolved", "2", "Caller 1", "29/2/2016 01:16", "1/3/2016 09:00", "Category 5"),
        ("INC1", "Closed", "3", "Caller 1", "29/2/2016 01:16", "1/3/2016 09:00", "Category 5"),
        ("INC2", "Active", "1", "Caller 2", "5/3/2016 10:00", "?", "Category 7"),
    ]
    head = "number,incident_state,sys_mod_count,caller_id,opened_at,resolved_at,category,assignment_group\n"
    body = "".join(",".join(r) + ",Group 1\n" for r in rows)
    path = tmp_path / "incident_event_log.csv"
    path.write_text(head + body)
    units, meta = load_uci_incidents(path)
    assert meta["events"] == 4 and meta["rows_kept"] == 2
    one = units.set_index(S.UNIT_ID)
    assert one.loc["INC1", S.STATE] == "Closed" and one.loc["INC1", S.RESOLVED] == pd.Timestamp("2016-03-01 09:00")
    assert one.loc["INC2", S.OPENED] == pd.Timestamp("2016-03-05 10:00") and pd.isna(one.loc["INC2", S.RESOLVED])


@pytest.fixture(scope="module")
def units(tmp_path_factory):
    sim = generate(SimSpec(n_teams=16, n_weeks=60, wave_weeks=(24, 30, 36), seed=4))
    paths = sim.write(tmp_path_factory.mktemp("real"))
    return load_audit_inputs(
        [paths["tickets"]], paths["sessions"], preset="servicenow", pseudonymizer=Pseudonymizer(b"k" * 32)
    ).tickets


def test_baseline_rates_are_reported(units):
    b = real_baseline(units, min_group=200)
    assert 0 < b["overall"]["same_type"] <= b["overall"]["any"] < 1 and b["by_category"]


def test_planted_failures_are_found_and_none_when_none_were_planted(units):
    df = run_real_audit_validation(units, levels=(0.0, 0.2), runs=2, claims=800, reps=49, seed=3)
    by = df.groupby("planted")["estimate"].mean()
    assert abs(by[0.0]) < 0.05 and abs(by[0.2] - 0.2) < 0.06
    assert df.loc[df["planted"] == 0.2, "flagged"].all()
