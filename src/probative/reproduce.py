"""Re-performance: rerun a recorded result from the same inputs and confirm every number matches.

Auditors re-perform rather than trust. Every Probative Labs result records the engine
version, the input files' SHA-256, the settings and the random seed, so the same inputs
and pseudonymization key must give the same numbers. This module checks that.
"""

from __future__ import annotations

import math
from typing import Any

VOLATILE = {"lineage", "run_at", "generated_at"}


def compare(recorded: Any, rerun: Any, path: str = "", out: list[str] | None = None, limit: int = 50) -> list[str]:
    """Differences between two results, ignoring run times and code versions."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(recorded, dict) and isinstance(rerun, dict):
        for k in sorted(set(recorded) | set(rerun)):
            if k in VOLATILE:
                continue
            compare(recorded.get(k), rerun.get(k), f"{path}.{k}" if path else k, out, limit)
    elif isinstance(recorded, list) and isinstance(rerun, list):
        if len(recorded) != len(rerun):
            out.append(f"{path}: {len(recorded)} items recorded, {len(rerun)} rerun")
        for i, (a, b) in enumerate(zip(recorded, rerun, strict=False)):
            compare(a, b, f"{path}[{i}]", out, limit)
    elif isinstance(recorded, float) or isinstance(rerun, float):
        try:
            if not math.isclose(float(recorded), float(rerun), rel_tol=1e-9, abs_tol=1e-12):
                out.append(f"{path}: recorded {recorded!r}, rerun {rerun!r}")
        except (TypeError, ValueError):
            out.append(f"{path}: recorded {recorded!r}, rerun {rerun!r}")
    elif recorded != rerun:
        out.append(f"{path}: recorded {recorded!r}, rerun {rerun!r}")
    return out


def count_values(obj: Any) -> int:
    if isinstance(obj, dict):
        return sum(count_values(v) for k, v in obj.items() if k not in VOLATILE)
    if isinstance(obj, list):
        return sum(count_values(v) for v in obj)
    return 1


def file_mismatches(recorded_files: list[dict], current_files: list[dict]) -> list[str]:
    """Input files whose fingerprint differs from the one recorded with the result."""
    now = {f["path"].split("/")[-1]: f.get("sha256") for f in current_files}
    out = []
    for f in recorded_files:
        name = f["path"].split("/")[-1]
        if name in now and now[name] != f.get("sha256"):
            out.append(f"{name}: the file has changed since the result was produced")
        elif name not in now:
            out.append(f"{name}: not found among the inputs given")
    return out
