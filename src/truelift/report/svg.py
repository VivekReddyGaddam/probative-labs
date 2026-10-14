"""Inline SVG event-study chart (no plotting dependency; themable through CSS variables)."""

from __future__ import annotations

import math
from html import escape


def event_study_svg(rows: list[dict], *, unit: str = "", title: str = "Effect by week since rollout") -> str:
    pts = [r for r in rows if r.get("estimate") is not None]
    if not pts:
        return ""
    w, h = 720, 300
    ml, mr, mt, mb = 64, 16, 20, 44
    xs = [r["event_time"] for r in pts]
    lows = [r["ci_low"] if r.get("ci_low") is not None else r["estimate"] for r in pts]
    highs = [r["ci_high"] if r.get("ci_high") is not None else r["estimate"] for r in pts]
    xmin, xmax = min(xs) - 0.5, max(xs) + 0.5
    ymin, ymax = min(min(lows), 0.0), max(max(highs), 0.0)
    pad = (ymax - ymin) * 0.08 or 1.0
    ymin, ymax = ymin - pad, ymax + pad

    def sx(x: float) -> float:
        return ml + (x - xmin) / (xmax - xmin) * (w - ml - mr)

    def sy(y: float) -> float:
        return mt + (ymax - y) / (ymax - ymin) * (h - mt - mb)

    step = 10 ** math.floor(math.log10((ymax - ymin) / 4)) if ymax > ymin else 1
    for mult in (1, 2, 2.5, 5, 10):
        if (ymax - ymin) / (step * mult) <= 6:
            step *= mult
            break
    decimals = max(0, -math.floor(math.log10(step))) if step < 1 else 0
    ticks = []
    i = math.ceil(ymin / step)
    while i * step <= ymax + 1e-12:
        ticks.append(round(i * step, decimals + 2))  # integer multiples avoid float drift (no "2.8e-17")
        i += 1

    parts = [
        f'<svg class="tl-chart" viewBox="0 0 {w} {h}" role="img" aria-labelledby="es-title es-desc" '
        f'xmlns="http://www.w3.org/2000/svg">',
        f'<title id="es-title">{escape(title)}</title>',
        '<desc id="es-desc">Weekly effect estimates with 95% ranges. '
        "Weeks before rollout should hover around zero.</desc>",
    ]
    for t in ticks:
        y = sy(t)
        parts.append(f'<line x1="{ml}" x2="{w - mr}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        label = f"{t:.{decimals}f}" if t else "0"
        parts.append(f'<text x="{ml - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{label}</text>')
    parts.append(f'<line x1="{ml}" x2="{w - mr}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="zero"/>')
    parts.append(f'<line x1="{sx(-0.5):.1f}" x2="{sx(-0.5):.1f}" y1="{mt}" y2="{h - mb}" class="rollout"/>')
    parts.append(f'<text x="{sx(-0.5) + 6:.1f}" y="{mt + 12}" class="label">rollout</text>')

    post = [r for r in pts if not r.get("pre")]
    if post:
        top = " ".join(
            f"{sx(r['event_time']):.1f},{sy(r['ci_high'] if r.get('ci_high') is not None else r['estimate']):.1f}"
            for r in post
        )
        bottom = " ".join(
            f"{sx(r['event_time']):.1f},{sy(r['ci_low'] if r.get('ci_low') is not None else r['estimate']):.1f}"
            for r in reversed(post)
        )
        parts.append(f'<polygon points="{top} {bottom}" class="band"/>')
        line = " ".join(f"{sx(r['event_time']):.1f},{sy(r['estimate']):.1f}" for r in post)
        parts.append(f'<polyline points="{line}" class="line"/>')
    for r in pts:
        x, y = sx(r["event_time"]), sy(r["estimate"])
        cls = "pre" if r.get("pre") else "post"
        if r.get("pre") and r.get("ci_low") is not None:
            y1, y2 = sy(r["ci_low"]), sy(r["ci_high"])
            parts.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{y1:.1f}" y2="{y2:.1f}" class="whisker"/>')
        tip = f"Week {r['event_time']}: {r['estimate']:+.3g}{unit}"
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" class="{cls}"><title>{escape(tip)}</title></circle>')

    span = xmax - xmin
    every = max(1, int(round(span / 12)))
    for x in range(int(math.ceil(xmin)), int(math.floor(xmax)) + 1):
        if x % every == 0:
            parts.append(f'<text x="{sx(x):.1f}" y="{h - mb + 18}" class="tick" text-anchor="middle">{x}</text>')
    parts.append(
        f'<text x="{(ml + w - mr) / 2:.1f}" y="{h - 6}" class="axis" text-anchor="middle">Weeks since rollout</text>'
    )
    mid = (mt + h - mb) / 2
    unit_label = escape(f" ({unit.strip()})") if unit.strip() else ""
    parts.append(
        f'<text x="14" y="{mid:.1f}" class="axis" text-anchor="middle" '
        f'transform="rotate(-90 14 {mid:.1f})">Effect{unit_label}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)
