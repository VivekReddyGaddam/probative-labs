"""Platform export presets: column matching, value transforms and full runs on each export format."""

import pandas as pd
import pytest
import yaml

from probative import schema as S
from probative.config import ProjectConfig
from probative.ingest import MappingError, apply_mapping, resolve_mapping
from probative.ingest.mapping import PRESET_INFO, PRESETS, find_column
from probative.pipeline import analyze
from probative.simulate import EXPORT_FORMATS, SimSpec, demo_config, generate


def test_every_preset_is_described():
    assert set(PRESETS) == set(PRESET_INFO)
    assert set(EXPORT_FORMATS) <= set(PRESETS)


def test_column_matching_ignores_case_and_spacing():
    assert find_column(["Created  At", "x"], ["created at"]) == "Created  At"
    assert find_column(["Component/s", "Status"], ["Custom field (Team)", "Component/s"]) == "Component/s"
    assert find_column(["a"], ["b"]) is None


def test_missing_required_column_names_the_candidates():
    with pytest.raises(MappingError, match="Custom field \\(Team\\)"):
        apply_mapping(
            pd.DataFrame({"Issue key": ["ITSD-1"], "Created": ["14/Oct/26 9:14 AM"]}),
            resolve_mapping("jira_service_management", {}),
            S.WORK_UNIT_COLUMNS,
            required=S.REQUIRED_WORK_UNIT_COLUMNS,
        )


def test_jira_export_values():
    raw = pd.DataFrame(
        {
            "Issue key": ["ITSD-1", "ITSD-2"],
            "Component/s": ["Service Desk L1", "Service Desk L2"],
            "Created": ["14/Oct/26 9:14 AM", "02/Jan/26 11:05 PM"],
            "Resolved": ["14/Oct/26 1:14 PM", ""],
            "Time Spent": ["5400", ""],
        }
    )
    df, rep = apply_mapping(raw, resolve_mapping("jira_service_management", {}), S.WORK_UNIT_COLUMNS)
    assert rep.mapped[S.TEAM] == "Component/s"
    assert df[S.OPENED].tolist()[0] == pd.Timestamp("2026-10-14 09:14")
    assert df[S.OPENED].tolist()[1] == pd.Timestamp("2026-01-02 23:05")
    assert df[S.EFFORT].tolist()[0] == 1.5 and pd.isna(df[S.RESOLVED][1])


def test_zendesk_export_values():
    raw = pd.DataFrame(
        {
            "ID": ["1", "2", "3"],
            "Group": ["IT", "IT", "HR"],
            "Created at": ["2026-03-02 09:00:00"] * 3,
            "Satisfaction Score": ["good", "bad", "offered"],
            "Group stations": ["1", "3", ""],
        }
    )
    df, _ = apply_mapping(raw, resolve_mapping("zendesk", {}), S.WORK_UNIT_COLUMNS)
    assert df[S.CSAT].tolist()[:2] == [1.0, 0.0] and pd.isna(df[S.CSAT][2])
    assert df[S.REASSIGNMENTS].tolist()[:2] == [0.0, 2.0]


def test_freshservice_time_tracked_is_hours_and_minutes():
    raw = pd.DataFrame(
        {"Ticket Id": ["7"], "Group": ["IT"], "Created Date": ["2026-03-02 09:00:00"], "Time Tracked": ["01:30"]}
    )
    df, _ = apply_mapping(raw, resolve_mapping("freshservice", {}), S.WORK_UNIT_COLUMNS)
    assert df[S.EFFORT][0] == 1.5


@pytest.fixture(scope="module")
def sim():
    return generate(SimSpec(n_teams=24, n_weeks=52, wave_weeks=(26, 32, 38), seed=11))


@pytest.mark.parametrize("fmt", EXPORT_FORMATS)
def test_each_export_format_runs_end_to_end(fmt, sim, tmp_path):
    sim.write(tmp_path / "data", fmt)
    raw = demo_config("data", sim.truth, fmt)
    raw["analysis"]["bootstrap_reps"] = 49
    (tmp_path / "probative.yaml").write_text(yaml.safe_dump(raw))
    res = analyze(ProjectConfig.load(tmp_path / "probative.yaml"), key_file=str(tmp_path / "k.key")).to_dict()
    p = res["primary"]
    truth = sim.truth[
        "effort_att_hours_per_ticket" if p["metric"] == "effort_hours" else "resolution_att_hours_per_ticket"
    ]
    assert res["grade"]["grade"] in {"A", "B", "C"}
    assert p["ci_low"] <= truth <= p["ci_high"]
    assert res["deflection"]["verified"] > 0  # requester identifiers line up across tickets and sessions
