"""End-to-end analysis of one AI program: source files in, graded evidence out."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd

from probative import schema as S
from probative.config import ProjectConfig, TypingConfig
from probative.engine import (
    Check,
    Estimate,
    EstimationError,
    Panel,
    TeamBootstrap,
    choose_design,
    count_panel,
    estimate,
    evidence_grade,
    mean_panel,
)
from probative.engine.diagnostics import (
    leave_one_team_out,
    placebo_date_check,
    placebo_outcome_check,
    power_check,
    pretrend_check,
)
from probative.governance import small_teams, team_sizes
from probative.guardrails import GuardrailResult, GuardrailSpec, blocked, evaluate_change, evaluate_level
from probative.ingest import Pseudonymizer, apply_mapping, check_work_units, read_table, read_tables, resolve_mapping
from probative.ingest.health import HealthReport, censored_tail
from probative.ingest.mapping import SESSION_PRESET
from probative.lineage import code_version, fingerprint
from probative.playbooks import get_playbook
from probative.rollout.prereg import check_registration
from probative.value import period_costs, simulate_roi, verified_deflections
from probative.value.roi import DAYS_PER_MONTH
from probative.worktypes import assign_work_types, before_after_table, category_drift, residualize

TEAM_CODE = "_team_code"


class AnalysisError(RuntimeError):
    def __init__(self, message: str, health: HealthReport | None = None):
        super().__init__(message)
        self.health = health


def _clean(obj: Any) -> Any:
    """Make results JSON-safe: NaN/inf → None, numpy → Python, dates → ISO strings."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    return obj


@dataclass
class AnalysisResult:
    program: dict
    hypothesis: dict
    playbook: dict
    window: dict
    registration: dict
    design: dict
    grade: dict
    headline: str
    effects: list[dict] = field(default_factory=list)
    primary: dict | None = None
    event_study: list[dict] = field(default_factory=list)
    by_type: list[dict] = field(default_factory=list)
    before_after: list[dict] = field(default_factory=list)
    guardrails: list[dict] = field(default_factory=list)
    blocked: bool = False
    diagnostics: list[dict] = field(default_factory=list)
    drift: dict | None = None
    deflection: dict | None = None
    volume: dict | None = None
    adoption: dict | None = None
    value_status: str = "unavailable"
    value: dict | None = None
    costs: dict | None = None
    claimed: list[dict] = field(default_factory=list)
    assumptions: list[dict] = field(default_factory=list)
    recommendation: dict = field(default_factory=dict)
    lineage: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return _clean(self.__dict__)


# ---------------------------------------------------------------------------------


def _load_units(cfg: ProjectConfig, pseud: Pseudonymizer | None, now: pd.Timestamp | None):
    raw, files, schema_warnings = read_tables([cfg.resolve(p) for p in cfg.data.work_units])
    mapping = resolve_mapping(cfg.data.mapping_preset, cfg.data.mapping)
    units, report = apply_mapping(raw, mapping, S.WORK_UNIT_COLUMNS, required=S.REQUIRED_WORK_UNIT_COLUMNS)
    if pseud is not None:
        units = pseud.apply(units, (*S.PERSON_COLUMNS, *S.SOURCE_ID_COLUMNS))
    units, health = check_work_units(units, now=now)
    for w in schema_warnings:
        health.add("schema_change", "warning", w)
    return units, files, report, health


def _load_exposure(cfg: ProjectConfig):
    raw, meta = read_table(cfg.resolve(cfg.data.exposure))
    mapping = resolve_mapping({c: c for c in S.EXPOSURE_COLUMNS}, {})
    exposure, _ = apply_mapping(raw, mapping, S.EXPOSURE_COLUMNS, required=(S.TEAM,))
    exposure[S.TEAM] = exposure[S.TEAM].astype("string").str.strip()
    return exposure.dropna(subset=[S.TEAM]), meta


def _add_adjusted(units: pd.DataFrame, col: str, baseline: pd.Series) -> tuple[str, str]:
    resid, mu = residualize(units, col, baseline)
    grand = float(units.loc[baseline, col].mean())
    units[col + "__adj"] = resid + grand
    units[col + "__off"] = mu - grand
    return col + "__adj", col + "__off"


def _fmt_num(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:,.{digits}f}"


def _effect_row(est: Estimate, *, metric_label: str, view: str, unit: str) -> dict:
    s = est.summary()
    s.update({"metric_label": metric_label, "view": view, "unit": unit})
    return s


def _recommend(grade: dict, blocked_: bool, value_status: str, roi: dict | None, primary: dict | None) -> dict:
    if grade["grade"] == "insufficient":
        return {
            "action": "Fix measurement",
            "reason": "No valid comparison exists, so there is no evidence either way. Use the rollout "
            "designer to stage the next expansion in randomized waves with a holdout.",
        }
    if blocked_:
        return {
            "action": "Fix",
            "reason": "A quality guardrail breached its threshold: the program is faster but worse. "
            "Value is withheld until the guardrail recovers.",
        }
    if grade["grade"] == "D":
        return {
            "action": "Fix",
            "reason": "Diagnostics failed, so the effect cannot be trusted. Re-run with a cleaner "
            "comparison (staggered or randomized expansion) before renewing on this evidence.",
        }
    if primary and not primary.get("excludes_zero"):
        return {
            "action": "Fix or stop",
            "reason": "No effect distinguishable from zero on the primary metric. Check adoption and "
            "workflow fit; if a fair re-test also finds nothing, stop or right-size seats.",
        }
    if not roi or value_status != "counted":
        return {"action": "Review", "reason": "Effect measured, but value could not be computed."}
    cash, cap = roi["roi_cash"], roi["roi_capacity"]
    if cash["p10"] is not None and cash["p10"] > 0:
        return {"action": "Renew and expand", "reason": "Cash ROI is positive even at the low end of the range."}
    if cash["p50"] is not None and cash["p50"] > 0:
        return {"action": "Renew", "reason": "Cash ROI is positive in the middle case; the low case is not."}
    if cap["p50"] is not None and cap["p50"] > 0:
        return {
            "action": "Renew with a realization plan",
            "reason": "The AI frees real capacity, but too little has been shown to turn into money. "
            "Decide where freed hours go (backlog, contractor reduction, avoided hires) and document it.",
        }
    return {"action": "Stop or renegotiate", "reason": "Measured value does not cover the cost even as capacity."}


# ---------------------------------------------------------------------------------


def analyze(  # noqa: C901 - the analysis is one ordered story; splitting it hides the order
    cfg: ProjectConfig,
    *,
    key_file: str | None = None,
    now: pd.Timestamp | None = None,
    progress: Callable[[str], None] | None = None,
) -> AnalysisResult:
    say = progress or (lambda _msg: None)
    pb = get_playbook(cfg.playbook)
    a = cfg.analysis
    notes: list[str] = []
    exclusions: dict[str, int] = {}

    # 1. Read, map, pseudonymize, check health ---------------------------------------
    say("Reading and checking work units")
    pseud = (
        None
        if cfg.data.pseudonymized
        else Pseudonymizer.from_env_or_file(key_file or cfg.base_dir / ".probative" / "pseudonym.key")
    )
    units, files, map_report, health = _load_units(cfg, pseud, now)
    if not health.ok:
        raise AnalysisError(
            "data health check failed: " + "; ".join(i.message for i in health.issues if i.severity == "error"), health
        )
    exclusions.update(health.exclusions)
    exposure, exp_meta = _load_exposure(cfg)
    files.append(exp_meta)

    # 2. Window and weekly periods ------------------------------------------------------
    start = pd.Timestamp(a.start) if a.start else units[S.OPENED].min().normalize()
    end = pd.Timestamp(a.end) if a.end else units[S.OPENED].max().normalize()
    origin = start - pd.Timedelta(days=start.weekday())
    inside = (units[S.OPENED] >= start) & (units[S.OPENED] < end + pd.Timedelta(days=1))
    if (~inside).sum():
        exclusions["outside the analysis window"] = int((~inside).sum())
    units = units[inside].reset_index(drop=True)
    units[S.PERIOD] = ((units[S.OPENED] - origin).dt.days // 7).astype(int)
    units = pb.derive(units)
    units[S.TEAM] = units[S.TEAM].astype("string").str.strip()
    if a.include_teams is not None:
        included = units[S.TEAM].isin([t.strip() for t in a.include_teams])
        if (~included).sum():
            exclusions["teams outside this comparison"] = int((~included).sum())
        units = units[included].reset_index(drop=True)

    # 3. Who had the AI from when ---------------------------------------------------------
    starts = (
        exposure.dropna(subset=["start_date"]).groupby(S.TEAM)["start_date"].min()
        if "start_date" in exposure
        else pd.Series(dtype="datetime64[ns]")
    )
    ends = (
        exposure.dropna(subset=["end_date"]).groupby(S.TEAM)["end_date"].max()
        if "end_date" in exposure.columns
        else pd.Series(dtype="datetime64[ns]")
    )
    if a.randomized is not None:
        randomized = a.randomized
    elif "randomized" in exposure.columns and exposure["randomized"].notna().any():
        randomized = bool(exposure["randomized"].fillna(False).all())
    else:
        randomized = False

    n_periods = int(units[S.PERIOD].max()) + 1
    team_cohort: dict[str, float] = {}
    drop = pd.Series(False, index=units.index)
    always: list[str] = []
    for team in sorted(units[S.TEAM].dropna().unique()):
        s = starts.get(team)
        in_team = units[S.TEAM] == team
        if s is None or pd.isna(s):
            team_cohort[team] = np.nan
            continue
        s = pd.Timestamp(s).normalize()
        if s <= start:
            always.append(team)
            drop |= in_team
            continue
        p = int((s - origin).days // 7)
        partial = s.weekday() != 0
        cohort = p + (1 if partial else 0)
        if partial:
            drop |= in_team & (units[S.PERIOD] == p)
        team_cohort[team] = float(cohort) if cohort < n_periods else np.nan
        e = ends.get(team)
        if e is not None and not pd.isna(e):
            end_p = int((pd.Timestamp(e) - origin).days // 7)
            drop |= in_team & (units[S.PERIOD] >= end_p)
    unknown = sorted(set(starts.index) - set(units[S.TEAM].dropna().unique()))
    if unknown:
        notes.append(f"{len(unknown)} team(s) in the rollout file have no work units in the window.")
    if always:
        exclusions["team had the AI before the window"] = int(units[S.TEAM].isin(always).sum())
    trans = drop & ~units[S.TEAM].isin(always)
    if trans.sum():
        exclusions["rollout transition weeks and post-exposure weeks"] = int(trans.sum())
    units = units[~drop].reset_index(drop=True)

    # 4. Minimum group size ---------------------------------------------------------------
    sizes = team_sizes(units)
    small = small_teams(sizes, a.min_group_size)
    if sizes is None:
        notes.append("Assignees are not recorded, so the minimum group size could not be checked from the data.")
    if small:
        exclusions[f"teams with fewer than {a.min_group_size} people"] = int(units[S.TEAM].isin(small).sum())
        units = units[~units[S.TEAM].isin(small)].reset_index(drop=True)
        notes.append(f"{len(small)} team(s) below the minimum group size of {a.min_group_size} were left out.")

    # 5. Right-censoring guard ---------------------------------------------------------------
    cut, _ = censored_tail(units, S.PERIOD, a.censoring_threshold)
    if cut is not None and cut < n_periods:
        exclusions["recent weeks with too many unresolved tickets"] = int((units[S.PERIOD] >= cut).sum())
        units = units[units[S.PERIOD] < cut].reset_index(drop=True)
        notes.append(
            f"The last {n_periods - cut} week(s) were dropped: more than {a.censoring_threshold:.0%} of their "
            f"{pb.unit_noun_plural} were still open at extraction, which would make them look artificially fast."
        )
        n_periods = cut
    effective_end = origin + pd.Timedelta(weeks=n_periods) - pd.Timedelta(days=1)
    effective_end = min(effective_end, end)

    teams = sorted(units[S.TEAM].dropna().unique())
    if not teams:
        raise AnalysisError("no teams left to analyze after exclusions", health)
    code = {t: i for i, t in enumerate(teams)}
    units[TEAM_CODE] = units[S.TEAM].map(code).astype(int)
    cohort_by_team = np.array([team_cohort.get(t, np.nan) for t in teams], dtype=float)
    cohort_by_team[cohort_by_team >= n_periods] = np.nan
    row_cohort = cohort_by_team[units[TEAM_CODE].to_numpy()]
    with np.errstate(invalid="ignore"):
        treated_row = ~np.isnan(row_cohort) & (units[S.PERIOD].to_numpy() >= row_cohort)
    baseline = pd.Series(~treated_row, index=units.index)
    treated_team_row = pd.Series(~np.isnan(row_cohort), index=units.index)

    first_rollout = (
        (origin + pd.Timedelta(weeks=int(np.nanmin(cohort_by_team)))).date()
        if np.isfinite(cohort_by_team).any()
        else None
    )

    # 6. Design ------------------------------------------------------------------------------
    choice = choose_design(
        cohort_by_team, randomized=randomized, requested=a.design, min_teams=a.min_teams, always_treated=len(always)
    )

    # 7. Work typing and drift -----------------------------------------------------------------
    say("Typing work and checking category drift")
    units[S.WORK_TYPE] = assign_work_types(units, cfg.typing, baseline)
    raw_typing = TypingConfig(fields=cfg.typing.fields, rules=[], min_share=0.0, other_label=cfg.typing.other_label)
    raw_labels = assign_work_types(units, raw_typing)
    min_cohort = np.nanmin(cohort_by_team) if np.isfinite(cohort_by_team).any() else np.inf
    post = pd.Series(
        np.where(np.isnan(row_cohort), units[S.PERIOD].to_numpy() >= min_cohort, treated_row), index=units.index
    )
    drift = category_drift(raw_labels, treated_team_row, post) if treated_team_row.any() else None
    if S.DESCRIPTION in units.columns:
        units = units.drop(columns=[S.DESCRIPTION])

    # 8. Registration ---------------------------------------------------------------------------
    guardrail_specs: list[GuardrailSpec] = []
    for g in pb.guardrails:
        spec = g.with_overrides(cfg.guardrails.get(g.key))
        if spec is not None:
            guardrail_specs.append(spec)
    reg = check_registration(cfg, guardrail_specs, cfg.resolve(cfg.registration), first_rollout)

    # 9. Metrics available --------------------------------------------------------------------------
    def available(col: str) -> bool:
        return col in units.columns and units[col].notna().mean() > 0.5

    def available_any(col: str) -> bool:
        return col in units.columns and units[col].notna().sum() >= 50

    primary = cfg.hypothesis.primary_metric
    if not available(primary):
        alt = S.RESOLUTION if primary == S.EFFORT else S.EFFORT
        if available(alt):
            notes.append(f"The primary metric {primary} is not recorded; {alt} was used instead (deviation).")
            primary = alt
        else:
            raise AnalysisError(f"neither {S.EFFORT} nor {S.RESOLUTION} is available in the data", health)
    metrics = [primary] + [m for m in (S.EFFORT, S.RESOLUTION) if m != primary and available(m)]

    base_result = dict(
        program=cfg.program.model_dump(),
        hypothesis=cfg.hypothesis.model_dump(mode="json"),
        playbook={
            "name": pb.name,
            "title": pb.title,
            "unit": pb.unit_noun,
            "units": pb.unit_noun_plural,
            "traps": list(pb.traps),
        },
        window={
            "start": start.date(),
            "end": effective_end.date(),
            "requested_end": end.date(),
            "weeks": n_periods,
            "first_rollout": first_rollout,
            "teams": len(teams),
            "units": len(units),
        },
        registration=reg.to_dict(),
        design=choice.to_dict(),
    )

    before = treated_team_row & baseline
    after = treated_team_row & ~baseline
    before_after = (
        before_after_table(
            units,
            before=before,
            after=after,
            resolution_metric=S.RESOLUTION if available(S.RESOLUTION) else None,
            effort_metric=S.EFFORT if available(S.EFFORT) else None,
        )
        if before.any() and after.any()
        else []
    )

    adoption = None
    if S.AI_TOUCHED in units.columns and after.any() and units.loc[after, S.AI_TOUCHED].notna().any():
        adoption = {"share_touched": float(units.loc[after, S.AI_TOUCHED].astype("Float64").mean())}

    lineage = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "code": code_version(),
        "config_sha256": fingerprint(cfg.model_dump(mode="json")),
        "registration_sha256": reg.sha256,
        "files": [f.to_dict() for f in files],
        "mapping": {
            "fields": map_report.mapped,
            "dropped_source_columns": len(map_report.dropped_source_columns),
            "coercion_failures": map_report.coercion_failures,
        },
        "exclusions": exclusions,
        "bootstrap": {
            "reps": a.bootstrap_reps,
            "seed": a.seed,
            "level": a.confidence,
            "strata": "rollout cohort",
            "falsification_alpha_each": 0.05 / (2 + len(a.placebo_outcomes)),
        },
        "pseudonymized_fields": [c for c in (*S.PERSON_COLUMNS, *S.SOURCE_ID_COLUMNS) if c in units.columns],
    }

    if choice.design == "none":
        grade = evidence_grade(choice, [], preregistered_before_rollout=False, registration_intact=False)
        rec = _recommend(grade.to_dict(), False, "unavailable", None, None)
        return AnalysisResult(
            **base_result,
            grade=grade.to_dict(),
            headline=f"No number: {choice.reasons[-1]}",
            before_after=before_after,
            drift=drift.to_dict() if drift else None,
            adoption=adoption,
            recommendation=rec,
            lineage=lineage,
            health=health.to_dict(),
            notes=notes,
            claimed=[c.model_dump() for c in cfg.claimed],
        )

    # 10. Panels and estimates -------------------------------------------------------------------
    say(f"Estimating effects ({a.bootstrap_reps} team-level bootstrap draws)")
    boot = TeamBootstrap(cohort_by_team, a.bootstrap_reps, a.seed)
    if boot.smallest_stratum < 3:
        notes.append("At least one rollout wave has fewer than 3 teams; its uncertainty is understated.")

    def mpanel(df: pd.DataFrame, col: str, offset: str | None = None, metric: str | None = None) -> Panel:
        return mean_panel(
            df,
            col,
            team_code_col=TEAM_CODE,
            period_col=S.PERIOD,
            cohort_by_team=cohort_by_team,
            n_periods=n_periods,
            offset_col=offset,
            metric=metric or col,
        )

    def run(panel: Panel, label: str, pretrend: bool = False) -> Estimate | None:
        try:
            return estimate(
                panel,
                boot,
                label=label,
                pretrend_k=a.pretrend_weeks if pretrend else 0,
                event_max=a.max_event_weeks,
                level=a.confidence,
            )
        except EstimationError as exc:
            notes.append(f"Could not estimate {label}: {exc}")
            return None

    effects: list[dict] = []
    adjusted: dict[str, tuple[Panel, Estimate | None]] = {}
    for m in metrics:
        meta = pb.metrics[m]
        adj_col, off_col = _add_adjusted(units, m, baseline)
        unseen = int(units[adj_col].isna().sum() - units[m].isna().sum())
        if unseen > 0:
            notes.append(
                f"{unseen} {pb.unit_noun_plural} of types never seen before rollout were left out of "
                f"mix-adjusted {meta.label.lower()}."
            )
        p_adj = mpanel(units, adj_col, off_col, metric=m)
        e_adj = run(p_adj, f"{meta.label} (mix-adjusted)", pretrend=(m == primary))
        e_head = run(mpanel(units, m), f"{meta.label} (headline)")
        adjusted[m] = (p_adj, e_adj)
        for est, view in ((e_head, "headline"), (e_adj, "mix-adjusted")):
            if est is not None:
                effects.append(_effect_row(est, metric_label=meta.label, view=view, unit=meta.unit.strip()))

    main_panel, main = adjusted[primary]
    if main is None:
        raise AnalysisError(f"the primary metric could not be estimated: {notes[-1] if notes else ''}", health)

    # Guardrails (change-based) and placebo outcomes, all type-adjusted.
    say("Checking quality guardrails and placebo outcomes")
    guardrail_results: list[GuardrailResult] = []
    for spec in guardrail_specs:
        if spec.level:
            continue
        if not available_any(spec.column):
            guardrail_results.append(evaluate_change(spec, None))
            continue
        adj_col, off_col = _add_adjusted(units, spec.column, baseline)
        guardrail_results.append(
            evaluate_change(spec, run(mpanel(units, adj_col, off_col, metric=spec.column), spec.label))
        )

    # Falsification tests share one 5% family-wise error budget (Bonferroni).
    n_falsification = 2 + len(a.placebo_outcomes)
    alpha_each = 0.05 / n_falsification
    placebo_checks: list[Check] = []
    for key in a.placebo_outcomes:
        label = pb.placebo_metrics[key].label if key in pb.placebo_metrics else key
        if not available(key):
            placebo_checks.append(placebo_outcome_check(label, None))
            continue
        adj_col, off_col = _add_adjusted(units, key, baseline)
        placebo_checks.append(
            placebo_outcome_check(label, run(mpanel(units, adj_col, off_col, metric=key), label), alpha_each)
        )

    active = units.groupby(TEAM_CODE)[S.PERIOD].agg(first="min", last="max")
    vol_panel = count_panel(
        units,
        team_code_col=TEAM_CODE,
        period_col=S.PERIOD,
        cohort_by_team=cohort_by_team,
        n_periods=n_periods,
        active=active,
    )
    vol = run(vol_panel, f"{pb.unit_noun_plural.capitalize()} per team-week")

    # Effects by work type (raw within type: the type is held fixed by construction).
    by_type: list[dict] = []
    value_metric = S.EFFORT if available(S.EFFORT) else primary
    for wtype, cnt in units.loc[after, S.WORK_TYPE].value_counts().items():
        if cnt < a.min_type_units:
            continue
        est = run(mpanel(units[units[S.WORK_TYPE] == wtype], value_metric), f"{wtype}")
        if est is not None:
            row = est.summary()
            row.update({"work_type": str(wtype), "after_units": int(cnt)})
            by_type.append(row)
    by_type.sort(key=lambda r: -r["after_units"])

    # 11. Diagnostics ----------------------------------------------------------------------------
    say("Running diagnostics")
    pre_len = [
        int(c - units.loc[units[TEAM_CODE] == i, S.PERIOD].min())
        for i, c in enumerate(cohort_by_team)
        if not np.isnan(c) and (units[TEAM_CODE] == i).any()
    ]
    shift = a.placebo_shift_weeks or int(max(2, min(8, (np.median(pre_len) if pre_len else 8) // 2)))
    checks: list[Check] = [pretrend_check(main, alpha_each)]
    placebo_check, _ = placebo_date_check(main_panel, boot, shift, a.confidence, alpha_each)
    checks.append(placebo_check)
    checks.extend(placebo_checks)
    team_labels = [str(t) for t in teams]
    checks.append(leave_one_team_out(main_panel, main, team_labels))
    if drift is not None:
        checks.append(
            Check(
                "Category drift",
                "fail" if drift.status == "fail" else "pass",
                drift.message,
                {"treated_shift": drift.treated_shift, "control_shift": drift.control_shift},
            )
        )
    power = power_check(main)

    grade = evidence_grade(
        choice,
        checks,
        preregistered_before_rollout=reg.exists and reg.before_rollout,
        registration_intact=reg.exists and reg.intact and not reg.deviations,
    )

    # 12. Deflection ---------------------------------------------------------------------------------
    deflection = None
    effort_by_type = (
        units.loc[baseline].groupby(S.WORK_TYPE, observed=True)[S.EFFORT].mean()
        if available(S.EFFORT)
        else pd.Series(dtype=float)
    )
    if cfg.data.self_service:
        say("Verifying deflection")
        s_raw, s_meta = read_table(cfg.resolve(cfg.data.self_service))
        files.append(s_meta)
        lineage["files"].append(s_meta.to_dict())
        s_map = resolve_mapping(SESSION_PRESET, cfg.data.self_service_mapping)
        sessions, _ = apply_mapping(
            s_raw, s_map, S.SESSION_COLUMNS, required=(S.REQUESTER, S.SESSION_STARTED, S.SESSION_OUTCOME)
        )
        if pseud is not None:
            sessions = pseud.apply(sessions, (S.REQUESTER,))
        sessions = sessions[
            (sessions[S.SESSION_STARTED] >= start)
            & (sessions[S.SESSION_STARTED] <= effective_end + pd.Timedelta(days=1))
        ]
        if any(f in sessions.columns for f in cfg.typing.fields):
            labels = assign_work_types(sessions, raw_typing)
            known = set(units[S.WORK_TYPE].unique())
            sessions[S.WORK_TYPE] = labels.where(labels.isin(known), cfg.typing.other_label)
        if S.DESCRIPTION in sessions.columns:
            sessions = sessions.drop(columns=[S.DESCRIPTION])
        defl = verified_deflections(sessions, units, effort_by_type)
        deflection = defl

    guardrail_results.extend(
        evaluate_level(spec, deflection.false if deflection else None, deflection.resolved if deflection else None)
        for spec in guardrail_specs
        if spec.level
    )
    is_blocked = blocked(guardrail_results)

    if deflection is not None:
        if not grade.counted:
            deflection.reason = "Not counted: the evidence grade does not count toward measured value."
        elif is_blocked:
            deflection.reason = "Not counted: a quality guardrail breached."
        elif vol is None or not (vol.ci[1] < 0):
            deflection.reason = (
                "Not counted: ticket volume per team did not fall measurably for AI teams, so the "
                "self-service resolutions are not shown to have replaced tickets."
            )
        elif not available(S.EFFORT):
            deflection.reason = "Not counted: agent hours per ticket type are not recorded."
        else:
            deflection.counted = True
            deflection.reason = "Counted: verified resolutions and a measurable fall in ticket volume for AI teams."

    # 13. Value and ROI ---------------------------------------------------------------------------------
    say("Simulating value and ROI ranges")
    value_dict = costs_dict = None
    value_status = "unavailable"
    hours_draws = None
    if available(S.EFFORT) and adjusted.get(S.EFFORT, (None, None))[1] is not None:
        effort_est = adjusted[S.EFFORT][1]
        hours_draws = -effort_est.adjusted_total_draws()
    elif cfg.data.staffing:
        hours_draws = _staffing_hours(cfg, units, mpanel, run, notes)
    else:
        notes.append("Agent time is not recorded and no staffing file was given, so capacity value cannot be computed.")

    if hours_draws is not None and first_rollout is not None:
        months = max((effective_end.date() - first_rollout).days, 1) / DAYS_PER_MONTH
        costs = period_costs(cfg.costs, months)
        counted = grade.counted and not is_blocked
        roi = simulate_roi(
            hours_draws=hours_draws,
            value=cfg.value,
            costs=costs,
            deflection_hours=deflection.hours_avoided if deflection and deflection.counted else 0.0,
            false_deflections=deflection.false if deflection and deflection.counted else 0,
            counted=counted,
            seed=a.seed,
        )
        value_dict = roi.to_dict()
        costs_dict = costs.to_dict()
        value_status = "counted" if counted else ("withheld" if is_blocked else "indicative")

    primary_summary = main.summary()
    primary_summary["excludes_zero"] = main.excludes_zero
    primary_summary["label"] = pb.metrics[primary].label
    primary_summary["unit"] = pb.metrics[primary].unit.strip()
    primary_summary["mde"] = power.values.get("mde")
    primary_summary["mde_relative"] = power.values.get("mde_relative")

    min_teams_event = max(2, math.ceil(0.25 * choice.n_treated_teams))
    event_rows = [r for r in main.event_rows() if r["pre"] or r.get("teams", 0) >= min_teams_event]

    claimed = []
    for c in cfg.claimed:
        row = c.model_dump()
        if value_dict:
            row["measured_hours"] = value_dict["hours"]
            row["measured_value"] = value_dict["capacity_value"]
        claimed.append(row)

    v = cfg.value
    assumptions = [
        {
            "name": "Loaded cost per agent hour",
            "low": v.loaded_hourly_cost.low,
            "mid": v.loaded_hourly_cost.mid,
            "high": v.loaded_hourly_cost.high,
            "unit": v.currency,
            "owner": v.owner,
            "date": v.agreed_on,
        },
        {
            "name": "Share of freed hours provably redeployed (realization)",
            "low": v.realization_rate.low,
            "mid": v.realization_rate.mid,
            "high": v.realization_rate.high,
            "unit": "share",
            "owner": v.owner,
            "date": v.agreed_on,
        },
        {
            "name": "Extra agent hours per false deflection",
            "low": v.false_deflection_cost_hours.low,
            "mid": v.false_deflection_cost_hours.mid,
            "high": v.false_deflection_cost_hours.high,
            "unit": "h",
            "owner": v.owner,
            "date": v.agreed_on,
        },
    ] + [
        {
            "name": f"Realized saving: {s.description}",
            "low": s.amount,
            "mid": s.amount,
            "high": s.amount,
            "unit": v.currency,
            "owner": v.owner,
            "date": v.agreed_on,
            "evidence": s.evidence,
        }
        for s in v.realized_savings
    ]

    rec = _recommend(grade.to_dict(), is_blocked, value_status, value_dict, primary_summary)

    # Headline -----------------------------------------------------------------------------------------
    meta = pb.metrics[primary]
    lo, hi = main.ci
    rlo, rhi = main.relative_ci
    gr_status = (
        "breached"
        if is_blocked
        else (
            "all pass" if all(g.status in ("pass", "unavailable") for g in guardrail_results) else "some inconclusive"
        )
    )
    headline = (
        f"{cfg.program.name} changed {meta.label.lower()} by {main.att:+.2f}{meta.unit} per {pb.unit_noun} "
        f"({main.relative:+.0%}; {int(a.confidence * 100)}% range {lo:+.2f} to {hi:+.2f}{meta.unit}, "
        f"{rlo:+.0%} to {rhi:+.0%}). Evidence grade {grade.grade}. Guardrails: {gr_status}."
    )
    if value_dict and value_dict["roi_cash"]["p10"] is not None:
        rc = value_dict["roi_cash"]
        label = {"counted": "Cash ROI", "withheld": "Cash ROI (withheld)", "indicative": "Indicative cash ROI"}[
            value_status
        ]
        headline += f" {label} {rc['p10']:.0%} to {rc['p90']:.0%} (P10 to P90)."

    return AnalysisResult(
        **base_result,
        grade=grade.to_dict(),
        headline=headline,
        effects=effects,
        primary=primary_summary,
        event_study=event_rows,
        by_type=by_type,
        before_after=before_after,
        guardrails=[g.to_dict() for g in guardrail_results],
        blocked=is_blocked,
        diagnostics=[c.to_dict() for c in [*checks, power]],
        drift=drift.to_dict() if drift else None,
        deflection=deflection.to_dict() if deflection else None,
        volume=vol.summary() if vol else None,
        adoption=adoption,
        value_status=value_status,
        value=value_dict,
        costs=costs_dict,
        claimed=claimed,
        assumptions=assumptions,
        recommendation=rec,
        lineage=lineage,
        health=health.to_dict(),
        notes=notes,
    )


def _staffing_hours(cfg: ProjectConfig, units: pd.DataFrame, mpanel, run, notes: list[str]) -> np.ndarray | None:
    """Fallback when time worked is not recorded: staffed hours ÷ tickets per team-week."""
    raw, _ = read_table(cfg.resolve(cfg.data.staffing))
    staffing, _ = apply_mapping(
        raw,
        resolve_mapping({c: c for c in S.STAFFING_COLUMNS}, {}),
        S.STAFFING_COLUMNS,
        required=tuple(S.STAFFING_COLUMNS),
    )
    origin_week = units[S.OPENED].min().normalize()
    origin_week = origin_week - pd.Timedelta(days=origin_week.weekday())
    staffing[S.PERIOD] = ((staffing["week_start"] - origin_week).dt.days // 7).astype(int)
    counts = units.groupby([S.TEAM, S.PERIOD, TEAM_CODE]).size().rename("tickets").reset_index()
    merged = counts.merge(staffing[[S.TEAM, S.PERIOD, "hours"]], on=[S.TEAM, S.PERIOD], how="inner")
    if merged.empty:
        notes.append("The staffing file did not match any team-week in the data.")
        return None
    rows = merged.loc[merged.index.repeat(merged["tickets"])].copy()
    rows["hours_per_ticket"] = rows["hours"] / rows["tickets"]
    est = run(mpanel(rows, "hours_per_ticket", metric="hours_per_ticket"), "Staffed hours per ticket")
    notes.append(
        "Agent hours come from staffing ÷ tickets, so they cannot be broken down or mix-adjusted by ticket type."
    )
    return -est.adjusted_total_draws() if est is not None else None
