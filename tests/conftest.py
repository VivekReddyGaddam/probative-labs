from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from probative.config import ProjectConfig
from probative.engine.panel import Panel
from probative.simulate import SimSpec, demo_config, generate


def make_panel(
    effect: float = -0.5, seed: int = 1, n_teams: int = 40, n_periods: int = 60, noise: float = 0.3
) -> Panel:
    """Staggered panel with additive team and week effects and a constant effect."""
    rng = np.random.default_rng(seed)
    cohort = np.full(n_teams, np.nan)
    cohort[:10], cohort[10:20], cohort[20:30] = 30, 38, 46
    team = np.repeat(np.arange(n_teams), n_periods)
    period = np.tile(np.arange(n_periods), n_teams)
    a = rng.normal(5, 1, n_teams)
    b = np.sin(np.arange(n_periods) / 5)
    c = cohort[team]
    with np.errstate(invalid="ignore"):
        treated = ~np.isnan(c) & (period >= c)
    y = a[team] + b[period] + rng.normal(0, noise, team.size) + effect * treated
    return Panel(team, period, y, np.full(y.size, 20.0), np.zeros_like(y), cohort, n_periods, "y")


@pytest.fixture
def panel() -> Panel:
    return make_panel()


@pytest.fixture(scope="session")
def small_project(tmp_path_factory) -> tuple[ProjectConfig, dict]:
    """A small simulated service desk with a planted effect, written to disk like a customer export."""
    d: Path = tmp_path_factory.mktemp("project")
    sim = generate(SimSpec(n_teams=24, n_weeks=52, wave_weeks=(26, 32, 38), seed=11))
    sim.write(d / "data")
    raw = demo_config("data", sim.truth)
    raw["analysis"]["bootstrap_reps"] = 99
    raw["analysis"]["min_teams"] = 8
    (d / "probative.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return ProjectConfig.load(d / "probative.yaml"), sim.truth
