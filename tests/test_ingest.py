import pandas as pd
import pytest

from truelift import schema as S
from truelift.config import MappingEntry
from truelift.ingest import MappingError, Pseudonymizer, apply_mapping, check_work_units, resolve_mapping
from truelift.ingest.health import censored_tail
from truelift.ingest.mapping import _duration_hours


def test_servicenow_preset_maps_and_types():
    raw = pd.DataFrame(
        {
            "number": ["INC1", "INC2"],
            "assignment_group": ["SD-1", "SD-2"],
            "opened_at": ["2026-01-05 09:00:00", "2026-01-06 10:30:00"],
            "resolved_at": ["2026-01-05 12:00:00", ""],
            "time_worked": ["3600", "01:30:00"],
            "escalation": ["0", "2"],
            "secret_column": ["x", "y"],
        }
    )
    mapping = resolve_mapping("servicenow", {})
    df, rep = apply_mapping(raw, mapping, S.WORK_UNIT_COLUMNS, required=S.REQUIRED_WORK_UNIT_COLUMNS)
    assert list(df[S.EFFORT]) == [1.0, 1.5]
    assert list(df[S.ESCALATED]) == [False, True]
    assert pd.isna(df.loc[1, S.RESOLVED])
    assert "secret_column" not in df.columns and "secret_column" in rep.dropped_source_columns


def test_missing_required_field_raises():
    with pytest.raises(MappingError, match="team_id"):
        apply_mapping(
            pd.DataFrame({"number": ["1"], "opened_at": ["2026-01-01"]}),
            resolve_mapping("servicenow", {}),
            S.WORK_UNIT_COLUMNS,
            required=S.REQUIRED_WORK_UNIT_COLUMNS,
        )


def test_override_with_transform():
    m = resolve_mapping("canonical", {S.EFFORT: {"source": "minutes", "transform": "minutes_to_hours"}})
    assert m[S.EFFORT] == MappingEntry(source="minutes", transform="minutes_to_hours")


@pytest.mark.parametrize(
    ("value", "hours"),
    [
        ("7200", 2.0),
        ("02:30:00", 2.5),
        ("1 Day 2 Hours", 26.0),
        ("45 Minutes", 0.75),
        ("1970-01-02 01:30:00", 25.5),
        ("", float("nan")),
    ],
)
def test_duration_parsing(value, hours):
    got = _duration_hours(pd.Series([value]))[0]
    assert (pd.isna(got) and pd.isna(hours)) or got == pytest.approx(hours)


def test_pseudonymizer_is_keyed_and_stable(tmp_path):
    p1 = Pseudonymizer(b"k" * 32)
    p2 = Pseudonymizer(b"j" * 32)
    assert p1.hash("alice") == p1.hash("alice") != p2.hash("alice")
    assert p1.hash(None) is None
    df = p1.apply(pd.DataFrame({S.REQUESTER: ["alice", None, "bob"]}), [S.REQUESTER])
    assert df[S.REQUESTER].iloc[0].startswith("p_") and pd.isna(df[S.REQUESTER].iloc[1])
    keyed = Pseudonymizer.from_env_or_file(tmp_path / "k.key")
    assert (tmp_path / "k.key").stat().st_mode & 0o777 == 0o600
    assert Pseudonymizer.from_env_or_file(tmp_path / "k.key").hash("x") == keyed.hash("x")


def test_health_excludes_bad_rows():
    df = pd.DataFrame(
        {
            S.UNIT_ID: ["a", "b", "b", "c", "d"],
            S.TEAM: ["t", "t", "t", None, "t"],
            S.OPENED: pd.to_datetime(["2026-01-05", "2026-01-05", "2026-01-05", "2026-01-06", "2026-01-07"]),
            S.RESOLVED: pd.to_datetime(["2026-01-06", "2026-01-04", "2026-01-06", "2026-01-07", "2026-01-08"]),
        }
    )
    clean, rep = check_work_units(df, now=pd.Timestamp("2026-02-01"))
    assert list(clean[S.UNIT_ID]) == ["a", "d"]
    assert rep.exclusions == {"missing team_id": 1, "duplicate ID": 1, "resolved before opened": 1}
    assert rep.ok


def test_censored_tail_finds_open_recent_weeks():
    df = pd.DataFrame(
        {
            "period": [0, 0, 1, 1, 2, 2],
            S.RESOLVED: pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-01", "2026-01-01", None, "2026-01-01"]),
        }
    )
    cut, _ = censored_tail(df, "period", 0.05)
    assert cut == 2
