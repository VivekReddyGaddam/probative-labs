"""Measurement plan: everything decided before go-live, on pages a program owner and finance can sign.

Evidence is cheapest and strongest when it is designed in. The plan states who
gets the AI when (and who waits), how small an effect the rollout can detect,
which quality guardrails apply, which result leads to which decision, and the
registration that locks the question before any result exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from probative import schema as S
from probative.config import ProjectConfig
from probative.guardrails import GuardrailSpec
from probative.ingest import apply_mapping, check_work_units, read_table, read_tables, resolve_mapping
from probative.lineage import code_version, fingerprint
from probative.playbooks import get_playbook
from probative.rollout.power import NoiseModel, noise_from_units, power_table
from probative.rollout.prereg import check_registration

DECISION_RULES = [
    ("No valid comparison group", "Fix measurement: stage the next expansion in waves with a holdout."),
    ("A quality guardrail breaches its threshold", "Fix: faster but worse earns nothing; value is withheld."),
    ("A diagnostic fails (grade D)", "Fix: re-test with a cleaner comparison before renewing on this evidence."),
    (
        "No effect distinguishable from zero",
        "Fix or stop: check adoption and fit; stop or right-size if a re-test agrees.",
    ),
    ("Cash ROI positive even at the low end (P10)", "Renew and expand."),
    ("Cash ROI positive in the middle case (P50)", "Renew."),
    ("Only capacity ROI positive", "Renew with a realization plan: decide where the freed hours go."),
    ("Value below cost even as capacity", "Stop or renegotiate."),
]

DIAGNOSTICS = [
    "Pre-trend test: AI and comparison teams moved together before rollout",
    "Placebo date: a fake, earlier rollout shows no effect",
    "Leave one team out: no single team drives the result",
    "Category drift: a change in ticket categories cannot pose as an effect",
    "Power: the smallest effect the data could have detected is reported",
]

SIGN_OFF = [
    ("Program owner", "Owns the AI program and the decision"),
    ("Finance partner", "Agrees the unit values: loaded hourly cost and realization"),
    ("Data owner", "Confirms the export and its permissions"),
    ("Privacy or works council (if required)", "Confirms team-level reporting and pseudonymization"),
]


def _history(cfg: ProjectConfig, notes: list[str]) -> pd.DataFrame | None:
    paths = [cfg.resolve(p) for p in cfg.data.work_units]
    if not all(p.exists() for p in paths):
        notes.append("No ticket history yet, so detectability uses typical service-desk noise.")
        return None
    try:
        raw, _, _ = read_tables(paths)
        units, _ = apply_mapping(
            raw,
            resolve_mapping(cfg.data.mapping_preset, cfg.data.mapping),
            S.WORK_UNIT_COLUMNS,
            required=S.REQUIRED_WORK_UNIT_COLUMNS,
        )
        units, _ = check_work_units(units)
        return get_playbook(cfg.playbook).derive(units)
    except (ValueError, KeyError) as exc:
        notes.append(f"Ticket history could not be read ({exc}); detectability uses typical service-desk noise.")
        return None


def _rollout(cfg: ProjectConfig) -> dict[str, Any] | None:
    path = cfg.resolve(cfg.data.exposure)
    if not path.exists():
        return None
    raw, meta = read_table(path)
    exp, _ = apply_mapping(raw, resolve_mapping({c: c for c in S.EXPOSURE_COLUMNS}, {}), S.EXPOSURE_COLUMNS, (S.TEAM,))
    exp[S.TEAM] = exp[S.TEAM].astype("string").str.strip()
    exp = exp.dropna(subset=[S.TEAM]).reset_index(drop=True)
    kind = exp["kind"].astype("string").str.lower() if "kind" in exp.columns else pd.Series("", index=exp.index)
    hold = kind.eq("holdout").fillna(False)
    starts = exp["start_date"] if "start_date" in exp.columns else pd.Series(pd.NaT, index=exp.index)
    dated = ~hold & starts.notna()
    waves = [
        {"wave": i + 1, "start": pd.Timestamp(d).date(), "teams": int(n)}
        for i, (d, n) in enumerate(starts[dated].dt.normalize().value_counts().sort_index().items())
    ]
    randomized = bool(exp["randomized"].fillna(False).astype(bool).all()) if "randomized" in exp.columns else False
    seed = None
    if "seed" in raw.columns and raw["seed"].notna().any():
        seed = str(raw["seed"].dropna().iloc[0])
    release = starts[hold].min()
    return {
        "teams": len(exp),
        "waves": waves,
        "holdout_teams": int(hold.sum()),
        "holdout_release": pd.Timestamp(release).date() if pd.notna(release) else None,
        "never_teams": int((~hold & starts.isna()).sum()),
        "randomized": randomized,
        "seed": seed,
        "file": meta.to_dict(),
        "team_ids": set(exp[S.TEAM].tolist()),
    }


def build_plan(
    cfg: ProjectConfig,
    guardrails: list[GuardrailSpec],
    *,
    sims: int = 200,
    weeks_post: tuple[int, ...] = (8, 12, 16),
    today: date | None = None,
) -> dict[str, Any]:
    """Assemble the measurement plan for one AI program from its configuration and rollout file."""
    from probative.pipeline import _clean

    notes: list[str] = []
    pb = get_playbook(cfg.playbook)
    rollout = _rollout(cfg)
    if rollout is None:
        notes.append("No rollout file yet. Create one with `probative plan waves teams.csv`, then rebuild this plan.")
    units = _history(cfg, notes)

    metric = cfg.hypothesis.primary_metric
    expected = cfg.hypothesis.expected_effect
    if expected is None:
        expected = -0.10
        notes.append("No expected effect was stated; detectability assumes a 10% reduction.")

    # Who is compared with whom -------------------------------------------------------------------
    history_teams = set(units[S.TEAM].dropna().astype(str)) if units is not None and S.TEAM in units else set()
    comparison = False
    n_teams = n_treated = 0
    if rollout:
        n_treated = sum(w["teams"] for w in rollout["waves"])
        n_teams = len(rollout["team_ids"] | history_teams)
        comparison = len(rollout["waves"]) >= 2 or n_teams > n_treated
    first_wave = rollout["waves"][0]["start"] if rollout and rollout["waves"] else None
    last_wave = rollout["waves"][-1]["start"] if rollout and rollout["waves"] else None

    # Detectability -----------------------------------------------------------------------------------
    power_rows: list[dict] = []
    noise = NoiseModel()
    noise_source = "typical service-desk noise"
    if units is not None and metric in units.columns and units[metric].notna().sum() >= 100:
        try:
            noise = noise_from_units(units, metric)
            noise_source = "your own ticket history"
        except ValueError as exc:
            notes.append(f"Noise could not be calibrated from history ({exc}).")
    readout = None
    if rollout and comparison and n_teams >= 4:
        n_waves = max(1, len(rollout["waves"]))
        starts = [pd.Timestamp(w["start"]) for w in rollout["waves"]]
        gaps = pd.Series(starts).diff().dropna()
        spacing = int(round(gaps.median().days / 7)) if len(gaps) else 4
        if units is not None and first_wave is not None:
            weeks_pre = int(max(4, min(52, (pd.Timestamp(first_wave) - units[S.OPENED].min()).days // 7)))
        else:
            weeks_pre = 26
            notes.append("Assumed 26 weeks of history before the first wave.")
        try:
            rows = power_table(
                team_options=[n_teams],
                post_options=list(weeks_post),
                n_waves=n_waves,
                weeks_pre=weeks_pre,
                effect=expected * noise.mean,
                noise=noise,
                wave_spacing=max(1, spacing),
                holdout_share=max(0.0, min(0.9, (n_teams - n_treated) / n_teams)),
                sims=sims,
            )
            power_rows = [
                {"teams": r.n_teams, "weeks_after": r.weeks_post, "power": r.power, "mde_relative": r.mde_relative}
                for r in rows
            ]
            ok = [r for r in power_rows if r["power"] >= 0.8]
            if ok and last_wave is not None:
                readout = {
                    "weeks_after_last_wave": ok[0]["weeks_after"],
                    "date": (pd.Timestamp(last_wave) + pd.Timedelta(weeks=ok[0]["weeks_after"])).date(),
                }
        except ValueError as exc:
            notes.append(f"Detectability could not be simulated: {exc}")

    # Registration and expected grade ------------------------------------------------------------------
    reg = check_registration(cfg, guardrails, cfg.resolve(cfg.registration), first_wave).to_dict()
    registered_in_time = reg["exists"] and reg["intact"] and (first_wave is None or reg["before_rollout"])
    if not rollout or not comparison:
        grade = {
            "grade": "insufficient",
            "text": "No comparison group as planned: everyone would get the AI at once. Add waves or a holdout.",
        }
    elif rollout["randomized"] and registered_in_time:
        grade = {"grade": "A", "text": "Randomized wave order, registered before go-live."}
    elif rollout["randomized"] and (first_wave is None or first_wave > (today or date.today())):
        grade = {
            "grade": "A",
            "text": f"Randomized wave order: grade A once registered before {first_wave or 'go-live'} "
            "(`probative register`). Until then, B.",
        }
    elif rollout["randomized"]:
        grade = {
            "grade": "B",
            "text": "Randomized wave order, but not registered before go-live, so grade B at best. "
            "Register before the next wave to earn A for it.",
        }
    else:
        grade = {"grade": "B", "text": "Staggered rollout with comparison teams, if the diagnostics pass."}

    v = cfg.value
    plan = {
        "kind": "measurement_plan",
        "program": cfg.program.model_dump(mode="json"),
        "hypothesis": {
            "statement": cfg.hypothesis.statement,
            "primary_metric": metric,
            "metric_label": pb.metrics[metric].label if metric in pb.metrics else metric,
            "expected_effect": expected,
        },
        "rollout": {k: val for k, val in (rollout or {}).items() if k != "team_ids"} or None,
        "comparison": comparison,
        "teams_total": n_teams,
        "teams_treated": n_treated,
        "first_wave": first_wave,
        "last_wave": last_wave,
        "detectability": {"rows": power_rows, "noise": noise.to_dict(), "noise_source": noise_source, "sims": sims},
        "readout": readout,
        "expected_grade": grade,
        "guardrails": [{"label": g.label, "threshold": g.describe_threshold(), "level": g.level} for g in guardrails],
        "diagnostics": DIAGNOSTICS
        + [
            f"Placebo outcome: {pb.placebo_metrics[k].label}"
            for k in cfg.analysis.placebo_outcomes
            if k in pb.placebo_metrics
        ],
        "decision_rules": [{"if": a, "then": b} for a, b in DECISION_RULES],
        "value": {
            "currency": v.currency,
            "loaded_hourly_cost": v.loaded_hourly_cost.model_dump(),
            "realization_rate": v.realization_rate.model_dump(),
            "owner": v.owner,
            "agreed_on": v.agreed_on,
        },
        "costs": [
            {"component": c.component, "description": c.description, "basis": c.basis, "mid": c.amount.mid}
            for c in cfg.costs
        ],
        "audit": {
            "enabled": cfg.data.self_service is not None,
            "window_days": cfg.audit.window_days,
            "price": cfg.audit.price_per_resolution,
        },
        "registration": reg,
        "sign_off": [{"role": r, "why": w} for r, w in SIGN_OFF],
        "notes": notes,
        "lineage": {
            "created": (today or date.today()).isoformat(),
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code": code_version(),
            "config_sha256": fingerprint(cfg.model_dump(mode="json")),
        },
    }
    return _clean(plan)
