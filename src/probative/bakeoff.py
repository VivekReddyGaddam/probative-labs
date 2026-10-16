"""Pre-purchase bake-off: a randomized head-to-head trial of AI vendors on the buyer's own work.

Vendor proofs of concept are usually graded by the vendors running them. A bake-off
assigns teams to vendors by lottery, keeps a share of teams as a control, runs every
vendor over the same weeks, and then measures each vendor against the control teams with
the same engine, guardrails and ranges as any evidence pack. Where a vendor's
conversations are available, its claimed resolutions are audited as well.

The decision pack ranks vendors on measured performance, says whether the leader is
clearly ahead (ranges that do not overlap, a conservative test), and puts value for money
beside it when prices are given.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from probative import schema as S
from probative.config import CostLine, ProjectConfig
from probative.lineage import code_version, fingerprint
from probative.rollout.waves import plan_waves

CONTROL = "control"


def plan_bakeoff(
    teams: pd.DataFrame,
    vendors: list[str],
    *,
    first_start: date,
    weeks: int,
    control_share: float = 0.25,
    stratify_by: str | None = None,
    seed: int,
) -> pd.DataFrame:
    """Assign teams to vendors (and a control group) by lottery, balanced across strata."""
    if len(vendors) < 2:
        raise ValueError("a bake-off needs at least two vendors")
    if len(set(vendors)) != len(vendors) or CONTROL in vendors:
        raise ValueError(f"vendor names must be distinct and not {CONTROL!r}")
    if not 0 < control_share < 1:
        raise ValueError("control_share must be between 0 and 1: each vendor is measured against the control teams")
    plan = plan_waves(
        teams,
        n_waves=len(vendors),
        first_start=first_start,
        spacing_weeks=0,
        holdout_share=control_share,
        holdout_weeks=weeks,
        stratify_by=stratify_by,
        seed=seed,
    )
    arm = plan["wave"].map(lambda w: CONTROL if w == "holdout" else vendors[int(w) - 1])
    return pd.DataFrame(
        {
            S.TEAM: plan[S.TEAM],
            "arm": arm,
            "start_date": plan["start_date"],
            "kind": ["holdout" if a == CONTROL else "bakeoff" for a in arm],
            "randomized": True,
            "seed": seed,
        }
    )


def run_bakeoff(
    cfg: ProjectConfig,
    *,
    reps: int | None = None,
    key_file: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Measure every vendor in the bake-off against the control teams, and decide."""
    from probative.audit import AuditError, load_audit_inputs, run_audit
    from probative.ingest import Pseudonymizer
    from probative.pipeline import AnalysisError, _clean, analyze

    say = progress or (lambda _m: None)
    b = cfg.bakeoff
    if b is None:
        raise ValueError("the project has no bakeoff section")
    assign = pd.read_csv(cfg.resolve(b.assignment), dtype=str)
    assign[S.TEAM] = assign[S.TEAM].str.strip()
    control = assign.loc[assign["arm"] == CONTROL, S.TEAM].tolist()
    if not control:
        raise ValueError("the assignment has no control teams")
    weeks = max(1, (b.end - b.start).days // 7)
    key = key_file or str(cfg.base_dir / ".probative" / "pseudonym.key")
    notes: list[str] = []
    rows = []
    with tempfile.TemporaryDirectory(prefix="probative-bakeoff-") as tmp:
        for v in b.vendors:
            teams = assign.loc[assign["arm"] == v.name, S.TEAM].tolist()
            if not teams:
                notes.append(f"No teams were assigned to {v.name}.")
                continue
            say(f"Measuring {v.name} on {len(teams)} teams against {len(control)} control teams")
            exposure = Path(tmp) / f"exposure-{len(rows)}.csv"
            pd.DataFrame(
                {S.TEAM: teams, "start_date": b.start.isoformat(), "kind": "wave", "randomized": "true"}
            ).to_csv(exposure, index=False)
            cv = cfg.model_copy(deep=True)
            cv.data.exposure = exposure
            cv.analysis.include_teams = teams + control
            cv.analysis.end = b.end
            cv.analysis.min_teams = min(cv.analysis.min_teams, len(teams) + len(control))
            if reps:
                cv.analysis.bootstrap_reps = reps
            cv.registration = Path(tmp) / "not-registered.yaml"
            cv.program.name = f"{cfg.program.name}: {v.name}"
            cv.program.vendor = v.name
            cv.costs = (
                [
                    CostLine(
                        component="license", description=f"{v.name} quoted price", amount=v.annual_price, basis="annual"
                    )
                ]
                if v.annual_price
                else []
            )
            try:
                res = analyze(cv, key_file=key)
                r = res.to_dict()
            except AnalysisError as exc:
                rows.append({"vendor": v.name, "teams": len(teams), "error": str(exc)})
                continue
            audit = None
            if v.claims:
                try:
                    inp = load_audit_inputs(
                        [cfg.resolve(p) for p in cfg.data.work_units],
                        cfg.resolve(v.claims),
                        preset=cfg.data.mapping_preset,
                        ticket_mapping=cfg.data.mapping,
                        typing_fields=cfg.typing.fields,
                        pseudonymizer=Pseudonymizer.from_env_or_file(key),
                    )
                    a = run_audit(inp, reps=reps or 499).to_dict()
                    audit = {"failed_share": a["failed_share"], "confidence": a["confidence"]["level"]}
                except (AuditError, ValueError) as exc:
                    notes.append(f"{v.name}: the resolution audit could not run ({exc}).")
            p = r.get("primary") or {}
            value = r.get("value") or {}
            scale = 52 / weeks
            cap = (value.get("capacity_value") or {}).get("p50")
            hours = (value.get("hours") or {}).get("p50")
            price = v.annual_price.mid if v.annual_price else None
            rows.append(
                {
                    "vendor": v.name,
                    "teams": len(teams),
                    "grade": r["grade"]["grade"],
                    "blocked": r.get("blocked", False),
                    "metric": p.get("label"),
                    "relative": p.get("relative"),
                    "relative_ci_low": p.get("relative_ci_low"),
                    "relative_ci_high": p.get("relative_ci_high"),
                    "guardrails": {g["key"]: g["status"] for g in r.get("guardrails") or []},
                    "annual_hours": hours * scale if hours is not None else None,
                    "annual_capacity_value": cap * scale if cap is not None else None,
                    "annual_price": price,
                    "net_annual_value": cap * scale - price if cap is not None and price is not None else None,
                    "audit": audit,
                    "headline": r["headline"],
                }
            )
    decision = decide(rows)
    result = {
        "kind": "bakeoff",
        "program": cfg.program.model_dump(mode="json"),
        "trial": {"start": b.start, "end": b.end, "weeks": weeks, "control_teams": len(control)},
        "vendors": rows,
        "decision": decision,
        "notes": notes,
        "lineage": {
            "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "code": code_version(),
            "config_sha256": fingerprint(cfg.model_dump(mode="json")),
        },
    }
    return _clean(result)


def _eligible(v: dict) -> bool:
    return (
        not v.get("error")
        and v.get("grade") in ("A", "B", "C")
        and not v.get("blocked")
        and v.get("relative_ci_high") is not None
        and v["relative_ci_high"] < 0
    )


def decide(rows: list[dict]) -> dict[str, Any]:
    """Rank vendors on measured performance (lower time per ticket is better), then on value for money."""
    ok = sorted((v for v in rows if _eligible(v)), key=lambda v: v["relative"])
    priced = sorted(
        (v for v in rows if v.get("net_annual_value") is not None and _eligible(v)),
        key=lambda v: -v["net_annual_value"],
    )
    value_leader = priced[0]["vendor"] if priced else None
    if not ok:
        return {
            "winner": None,
            "clear": False,
            "value_leader": value_leader,
            "text": "No vendor showed a reliable improvement with quality held. Do not buy on this evidence: "
            "check adoption, extend the trial, or try other vendors.",
        }
    lead = ok[0]
    if len(ok) == 1:
        return {
            "winner": lead["vendor"],
            "clear": True,
            "value_leader": value_leader,
            "text": f"{lead['vendor']} is the only vendor with a reliable improvement and quality held.",
        }
    second = ok[1]
    clear = lead["relative_ci_high"] < second["relative_ci_low"]
    if clear:
        text = f"{lead['vendor']} performed clearly better than {second['vendor']}: their ranges do not overlap."
    else:
        text = (
            f"No clear winner on performance between {lead['vendor']} and {second['vendor']}: their ranges overlap. "
            "Decide on price and terms"
            + (f" ({value_leader} gives the most value for money)" if value_leader else "")
            + ", or extend the trial to separate them."
        )
    return {
        "winner": lead["vendor"] if clear else None,
        "leader": lead["vendor"],
        "clear": clear,
        "value_leader": value_leader,
        "text": text,
    }
