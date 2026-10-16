"""Benchmark contributions: an anonymous summary of one result, for comparing AI products across companies.

A vendor will not publish how its AI performs across customers, and one company's
result cannot say whether it is typical. Contributions make the comparison
possible without sharing data: they hold rates, ranges, grades and size bands
only. No company, team or person names, no ticket text and no money amounts.
The contributor reviews the file and decides whether to send it.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from probative.lineage import fingerprint

FORMAT = "probative-benchmark/1"

EXCLUDED = [
    "company, program, team and person names",
    "ticket text and identifiers",
    "money amounts: costs, prices, ROI in currency",
    "exact dates (month only) and exact volumes (bands only)",
]


def _band(n: float | None, edges: tuple[tuple[float, str], ...], top: str) -> str | None:
    if n is None:
        return None
    for upper, label in edges:
        if n < upper:
            return label
    return top


def _teams_band(n: int | None) -> str | None:
    return _band(n, ((8, "under 8"), (20, "8–19"), (50, "20–49"), (100, "50–99")), "100 or more")


def _band_count(n: int | None) -> str | None:
    return _band(n, ((200, "under 200"), (1000, "200–999"), (10000, "1,000–9,999")), "10,000 or more")


def _weekly_band(n: float | None) -> str | None:
    return _band(n, ((500, "under 500"), (2000, "500–1,999"), (10000, "2,000–9,999")), "10,000 or more")


def contribution(
    results: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    *,
    closures: dict[str, Any] | None = None,
    reconciliation: dict[str, Any] | None = None,
    platform: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build an anonymous benchmark contribution from any of: evidence pack, resolution audit, action audit and
    bill reconciliation."""
    if not (results or audit or closures or reconciliation):
        raise ValueError("give at least one result: results.json, audit.json, closures.json or reconciliation.json")
    program = next((x.get("program") for x in (results, audit, closures, reconciliation) if x and x.get("program")), {})
    body: dict[str, Any] = {
        "format": FORMAT,
        "month": (today or date.today()).strftime("%Y-%m"),
        "function": program.get("function"),
        "vendor": program.get("vendor"),
        "tool": program.get("tool"),
        "platform": platform,
    }
    if results:
        w = results.get("window") or {}
        weeks = w.get("weeks") or 0
        primary = results.get("primary") or {}
        effects = {(e.get("metric"), e.get("view")): e for e in results.get("effects") or []}
        head = effects.get(("resolution_hours", "headline")) or {}
        adj = effects.get(("resolution_hours", "mix-adjusted")) or {}
        first = w.get("first_rollout")
        weeks_after = None
        if first and w.get("end"):
            weeks_after = (date.fromisoformat(w["end"]) - date.fromisoformat(first)).days // 7
        body["effect"] = {
            "design": (results.get("design") or {}).get("design"),
            "grade": (results.get("grade") or {}).get("grade"),
            "metric": primary.get("metric"),
            "relative": primary.get("relative"),
            "relative_ci_low": primary.get("relative_ci_low"),
            "relative_ci_high": primary.get("relative_ci_high"),
            "resolution_headline_relative": head.get("relative"),
            "resolution_mix_adjusted_relative": adj.get("relative"),
            "guardrails": {g.get("key"): g.get("status") for g in results.get("guardrails") or []},
            "adoption_share": (results.get("adoption") or {}).get("share_touched"),
            "weeks_after_rollout": weeks_after,
            "teams_band": _teams_band(w.get("teams")),
            "weekly_tickets_band": _weekly_band((w.get("units") or 0) / weeks if weeks else None),
        }
    if audit:
        f = audit.get("failed_share") or {}
        body["resolution_audit"] = {
            "failed_share": f.get("estimate"),
            "failed_share_ci_low": f.get("ci_low"),
            "failed_share_ci_high": f.get("ci_high"),
            "ai_return_rate": (audit.get("returns") or {}).get("ai_rate"),
            "baseline_return_rate": (audit.get("returns") or {}).get("baseline_rate"),
            "window_days": (audit.get("settings") or {}).get("window_days"),
            "match": (audit.get("settings") or {}).get("match"),
            "confidence": (audit.get("confidence") or {}).get("level"),
            "claims_band": (audit.get("claims") or {}).get("band"),
        }
    if closures:
        f = closures.get("failed_share") or {}
        body["closure_audit"] = {
            "failed_share": f.get("estimate"),
            "failed_share_ci_low": f.get("ci_low"),
            "failed_share_ci_high": f.get("ci_high"),
            "confidence": (closures.get("confidence") or {}).get("level"),
            "closures_band": _band_count((closures.get("counts") or {}).get("ai_closures")),
        }
    if reconciliation:
        d = reconciliation.get("disputed") or {}
        body["billing"] = {
            "billing_error_share": d.get("billing_error_share"),
            "contractual_definition": (reconciliation.get("settings") or {}).get("contractual"),
            "item_level": (reconciliation.get("settings") or {}).get("item_level"),
        }
    body["excluded"] = EXCLUDED
    body["sha256"] = fingerprint({k: v for k, v in body.items() if k != "sha256"})
    return body
