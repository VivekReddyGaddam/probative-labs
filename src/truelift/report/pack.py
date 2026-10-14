"""Render an analysis result as an evidence pack."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from truelift.report.appendix import appendix_sections
from truelift.report.svg import event_study_svg

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


def months(x: Any) -> str:
    if _missing(x):
        return "not within the period"
    return f"{x:.1f} months"


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("truelift.report", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(num=num, signed=signed, pct=pct, spct=spct, money=money, months=months)
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
