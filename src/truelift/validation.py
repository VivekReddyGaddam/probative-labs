"""Validation on simulated data with planted effects, including zero.

A causal claim that is wrong destroys trust in the method, so the estimator is
checked the way it will be used: full pipeline, realistic messy data, known truth.
For each planted effect the harness reports bias, error, how often the 95% range
contains the truth (coverage) and how often a zero effect is wrongly declared
(false positive rate).

"Truth" is the planted expected change for the tickets AI teams handled
(``effort_att_expected``), which is what the team bootstrap targets. Coverage of
the realized change, which also contains ticket-level noise, is reported beside it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from truelift.config import ProjectConfig
from truelift.pipeline import analyze
from truelift.simulate import SimSpec, demo_config, generate


def run_validation(
    *,
    effects: tuple[float, ...] = (-0.15, 0.0),
    runs: int = 10,
    reps: int = 99,
    seed: int = 1,
    n_teams: int = 30,
    n_weeks: int = 60,
    progress: Callable[[str], None] | None = None,
) -> pd.DataFrame:
    rows = []
    waves = (n_weeks // 2, n_weeks // 2 + 6, n_weeks // 2 + 12)
    with tempfile.TemporaryDirectory(prefix="truelift-validate-") as tmp:
        for effect in effects:
            for i in range(runs):
                run_seed = seed * 100_003 + i * 7919 + int(abs(effect) * 1000)
                spec = SimSpec(
                    n_teams=n_teams,
                    n_weeks=n_weeks,
                    wave_weeks=waves,
                    effort_effect=effect,
                    resolution_effect=effect / 2,
                    seed=run_seed,
                    extraction_lag_days=365,  # no right-censoring, so the truth covers the analysed tickets
                )
                sim = generate(spec)
                d = Path(tmp) / f"run-{effect}-{i}"
                sim.write(d / "data")
                raw = demo_config("data", sim.truth)
                raw["analysis"]["bootstrap_reps"] = reps
                raw["analysis"]["seed"] = run_seed
                (d / "truelift.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
                cfg = ProjectConfig.load(d / "truelift.yaml")
                res = analyze(cfg, key_file=str(Path(tmp) / "validation.key")).to_dict()
                p = res["primary"]
                truth = sim.truth["effort_att_expected"]
                realized = sim.truth["effort_att_hours_per_ticket"]
                diag = {c["name"]: c["status"] for c in res["diagnostics"]}
                rows.append(
                    {
                        "planted_relative": effect,
                        "run": i,
                        "truth": truth,
                        "estimate": p["estimate"],
                        "ci_low": p["ci_low"],
                        "ci_high": p["ci_high"],
                        "realized": realized,
                        "covered": p["ci_low"] <= truth <= p["ci_high"],
                        "covered_realized": p["ci_low"] <= realized <= p["ci_high"],
                        "rejects_zero": bool(p["ci_low"] > 0 or p["ci_high"] < 0),
                        "grade": res["grade"]["grade"],
                        "pretrend": diag.get("Pre-trend test"),
                        "placebo_dates": diag.get("Placebo dates"),
                    }
                )
                if progress:
                    progress(
                        f"effect {effect:+.2f} run {i + 1}/{runs}: estimate {p['estimate']:+.4f} "
                        f"vs truth {truth:+.4f} ({res['grade']['grade']})"
                    )
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(error=df["estimate"] - df["truth"]).groupby("planted_relative")
    return pd.DataFrame(
        {
            "runs": g.size(),
            "mean_truth": g["truth"].mean(),
            "bias": g["error"].mean(),
            "rmse": g["error"].apply(lambda e: float(np.sqrt((e**2).mean()))),
            "coverage": g["covered"].mean(),
            "coverage_realized": g["covered_realized"].mean(),
            "rejects_zero": g["rejects_zero"].mean(),
            "pretrend_pass": g["pretrend"].apply(lambda s: float((s == "pass").mean())),
            "placebo_pass": g["placebo_dates"].apply(lambda s: float((s == "pass").mean())),
            "graded_A_to_C": g["grade"].apply(lambda s: float(s.isin(["A", "B", "C"]).mean())),
        }
    ).reset_index()
