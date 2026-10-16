"""Render an analysis result as an evidence pack."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from probative.report.appendix import appendix_sections
from probative.report.svg import event_study_svg, share_bars_svg

GRADE_TEXT = {
    "A": "Randomized design, pre-registered: counts toward measured ROI",
    "B": "Comparison-group design with diagnostics passed: counts toward measured ROI",
    "C": "Valid but weaker design: counts, flagged as weaker",
    "D": "Diagnostics failed: indicative only, never added to totals",
    "E": "Claimed (vendor estimate, survey): never added to totals",
    "insufficient": "No valid comparison: no number produced",
}

STATUS_TEXT = {
    "pass": "Pass",
    "watch": "Watch",
    "breach": "Breach",
    "unavailable": "Not available",
    "fail": "Fail",
    "not_run": "Not run",
}


def _missing(x: Any) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def num(x: Any, digits: int = 2) -> str:
    return "n/a" if _missing(x) else f"{x:,.{digits}f}"


def _minus(text: str) -> str:
    return text.replace("-", "\u2212", 1) if text.startswith("-") else text


def signed(x: Any, digits: int = 2) -> str:
    return "n/a" if _missing(x) else _minus(f"{x:+,.{digits}f}")


def pct(x: Any, digits: int = 0) -> str:
    return "n/a" if _missing(x) else _minus(f"{x * 100:,.{digits}f}%")


def spct(x: Any, digits: int = 0) -> str:
    return "n/a" if _missing(x) else _minus(f"{x * 100:+,.{digits}f}%")


def money(x: Any, currency: str = "USD") -> str:
    if _missing(x):
        return "n/a"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    sign = "−" if x < 0 else ""
    return f"{sign}{sym}{abs(x):,.0f}" + ("" if sym else f" {currency}")


def unit_price(x: Any, currency: str = "USD") -> str:
    """A price per item, with cents: $0.99, not $1."""
    if _missing(x):
        return "n/a"
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(currency, "")
    return f"{sym}{x:,.2f}" + ("" if sym else f" {currency}")


def months(x: Any) -> str:
    if _missing(x):
        return "not within the period"
    return f"{x:.1f} months"


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("probative.report", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(num=num, signed=signed, pct=pct, spct=spct, money=money, unit_price=unit_price, months=months)
    env.globals.update(GRADE_TEXT=GRADE_TEXT, STATUS_TEXT=STATUS_TEXT)
    return env


def _context(result: dict) -> dict:
    unit = (result.get("primary") or {}).get("unit", "")
    return {
        "r": result,
        "chart": event_study_svg(result.get("event_study") or [], unit=f" {unit}" if unit else ""),
        "currency": (result.get("value") or {}).get("currency", "USD"),
        "sections": appendix_sections(result),
    }


def render_markdown(result: dict) -> str:
    return _env().get_template("evidence_pack.md.j2").render(**_context(result))


def render_appendix(result: dict) -> str:
    return _env().get_template("method_appendix.md.j2").render(**_context(result))


def render_html(result: dict) -> str:
    return _env().get_template("evidence_pack.html.j2").render(**_context(result))


def write_pack(result: dict, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out / "results.json",
        "markdown": out / "evidence-pack.md",
        "appendix": out / "method-appendix.md",
        "html": out / "evidence-pack.html",
    }
    paths["json"].write_text(json.dumps(result, indent=2))
    paths["markdown"].write_text(render_markdown(result))
    paths["appendix"].write_text(render_appendix(result))
    paths["html"].write_text(render_html(result))
    return paths


def _audit_context(result: dict) -> dict:
    chart = share_bars_svg(
        result.get("by_month") or [],
        value="failed_share",
        label="month",
        title="Claimed resolutions that did not hold, by month",
        axis="Month of the claimed resolution",
    )
    return {"r": result, "chart": chart}


def render_audit_html(result: dict) -> str:
    return _env().get_template("resolution_audit.html.j2").render(**_audit_context(result))


def render_audit_markdown(result: dict) -> str:
    return _env().get_template("resolution_audit.md.j2").render(**_audit_context(result))


def write_audit_pack(result: dict, out_dir: str | Path) -> dict[str, Path]:
    """Write a resolution audit as JSON, Markdown and HTML."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out / "audit.json",
        "markdown": out / "resolution-audit.md",
        "html": out / "resolution-audit.html",
    }
    paths["json"].write_text(json.dumps(result, indent=2))
    paths["markdown"].write_text(render_audit_markdown(result))
    paths["html"].write_text(render_audit_html(result))
    return paths


def write_plan_pack(plan: dict, out_dir: str | Path) -> dict[str, Path]:
    """Write a measurement plan as JSON, Markdown and HTML."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out / "plan.json",
        "markdown": out / "measurement-plan.md",
        "html": out / "measurement-plan.html",
    }
    env = _env()
    paths["json"].write_text(json.dumps(plan, indent=2))
    paths["markdown"].write_text(env.get_template("measurement_plan.md.j2").render(r=plan))
    paths["html"].write_text(env.get_template("measurement_plan.html.j2").render(r=plan))
    return paths


def write_reconciliation_pack(result: dict, out_dir: str | Path) -> dict[str, Path]:
    """Write a bill reconciliation: JSON, the dispute pack (HTML, Markdown) and the disputed items as CSV."""
    import csv

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out / "reconciliation.json",
        "markdown": out / "dispute-pack.md",
        "html": out / "dispute-pack.html",
        "csv": out / "disputed-items.csv",
    }
    env = _env()
    paths["json"].write_text(json.dumps(result, indent=2))
    paths["markdown"].write_text(env.get_template("dispute_pack.md.j2").render(r=result))
    paths["html"].write_text(env.get_template("dispute_pack.html.j2").render(r=result))
    fields = ["line", "item_id", "billed_at", "amount", "reason_code", "reason", "detail"]
    with paths["csv"].open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for item in result.get("lines") or []:
            w.writerow({f: item.get(f) for f in fields})
    return paths


def write_watch_pack(result: dict, out_dir: str | Path) -> dict[str, Path]:
    """Write a regression watch as JSON, Markdown and HTML."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": out / "watch.json", "markdown": out / "regression-watch.md", "html": out / "regression-watch.html"}
    rows = [{**w, "label": str(w["week"])[5:]} for w in result.get("weekly") or []]
    chart = share_bars_svg(
        rows,
        value="failed_share",
        label="label",
        title="Claimed resolutions that did not hold, by week",
        axis="Week of the claimed resolution",
    )
    env = _env()
    paths["json"].write_text(json.dumps(result, indent=2))
    paths["markdown"].write_text(env.get_template("regression_watch.md.j2").render(r=result))
    paths["html"].write_text(env.get_template("regression_watch.html.j2").render(r=result, chart=chart))
    return paths


def write_closure_pack(result: dict, out_dir: str | Path) -> dict[str, Path]:
    """Write an action audit of the AI's own ticket closures as JSON, Markdown and HTML."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": out / "closures.json", "markdown": out / "closure-audit.md", "html": out / "closure-audit.html"}
    env = _env()
    paths["json"].write_text(json.dumps(result, indent=2))
    paths["markdown"].write_text(env.get_template("closure_audit.md.j2").render(r=result))
    paths["html"].write_text(env.get_template("closure_audit.html.j2").render(r=result))
    return paths


def write_bakeoff_pack(result: dict, out_dir: str | Path) -> dict[str, Path]:
    """Write a bake-off decision pack as JSON and HTML."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"json": out / "bakeoff.json", "html": out / "decision-pack.html"}
    paths["json"].write_text(json.dumps(result, indent=2))
    paths["html"].write_text(_env().get_template("bakeoff_pack.html.j2").render(r=result))
    return paths
