"""Reproducibility: every result records the code, data and assumptions that produced it."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from truelift import __version__


def fingerprint(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _git_sha() -> str | None:
    here = Path(__file__).resolve().parent
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, timeout=5, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "."], cwd=here, capture_output=True, text=True, timeout=5
        )
        sha = out.stdout.strip()
        return sha + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return None


def code_version() -> dict[str, Any]:
    return {
        "truelift": __version__,
        "git": _git_sha(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
    }
