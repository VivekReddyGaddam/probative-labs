"""Pre-registration: lock the question before results exist.

Hypothesis, primary metric, guardrails with thresholds and the analysis window
are written to a registration file with a timestamp and a content hash. Every
analysis checks the current configuration against it and lists deviations in the
evidence pack, so nobody can pick the best-looking metric afterwards.

The local file is tamper-evident, not tamper-proof: commit it to version
control (or use a service that stores registrations append-only) so its
timestamp can be trusted by a third party.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from truelift import __version__
from truelift.config import ProjectConfig
from truelift.guardrails import GuardrailSpec


def registration_content(cfg: ProjectConfig, guardrails: list[GuardrailSpec]) -> dict[str, Any]:
    a = cfg.analysis
    return {
        "program_id": cfg.program.id,
        "hypothesis": cfg.hypothesis.statement,
        "primary_metric": cfg.hypothesis.primary_metric,
        "expected_effect": cfg.hypothesis.expected_effect,
        "guardrails": {
            g.key: {"threshold": g.threshold, "kind": g.kind, "higher_is_worse": g.higher_is_worse}
            for g in sorted(guardrails, key=lambda g: g.key)
        },
        "window": {
            "start": a.start.isoformat() if a.start else None,
            "end": a.end.isoformat() if a.end else None,
        },
        "design": a.design,
        "placebo_outcomes": sorted(a.placebo_outcomes),
        "confidence": a.confidence,
    }


def content_digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def lock_registration(
    cfg: ProjectConfig,
    guardrails: list[GuardrailSpec],
    path: Path,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; registrations are not overwritten (use --force to replace)")
    content = registration_content(cfg, guardrails)
    record = {
        "registered_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "truelift_version": __version__,
        "sha256": content_digest(content),
        "content": content,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        yaml.safe_dump(record, fh, sort_keys=False)
    return record


@dataclass
class RegistrationStatus:
    exists: bool
    intact: bool = False
    registered_at: str | None = None
    before_rollout: bool = False
    sha256: str | None = None
    deviations: list[str] = field(default_factory=list)
    content: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        if not self.exists:
            return "Not pre-registered: hypothesis, metrics and guardrails were not locked before results existed."
        parts = [f"Registered {self.registered_at}"]
        parts.append("before go-live" if self.before_rollout else "after go-live")
        if not self.intact:
            parts.append("— the registration file was edited after it was locked")
        if self.deviations:
            parts.append(f"— {len(self.deviations)} deviation(s) from the registration")
        return " ".join(parts) + "."

    def to_dict(self) -> dict:
        return {
            "exists": self.exists,
            "intact": self.intact,
            "registered_at": self.registered_at,
            "before_rollout": self.before_rollout,
            "sha256": self.sha256,
            "deviations": self.deviations,
            "content": self.content,
            "summary": self.summary,
        }


def _diff(registered: Any, current: Any, prefix: str = "") -> list[str]:
    if isinstance(registered, dict) and isinstance(current, dict):
        out = []
        for k in sorted(set(registered) | set(current)):
            out += _diff(registered.get(k), current.get(k), f"{prefix}{k}.")
        return out
    if registered != current:
        return [f"{prefix.rstrip('.')}: registered {registered!r}, analysed {current!r}"]
    return []


def check_registration(
    cfg: ProjectConfig, guardrails: list[GuardrailSpec], path: Path, first_rollout: date | None
) -> RegistrationStatus:
    if not path.exists():
        return RegistrationStatus(exists=False)
    with path.open() as fh:
        record = yaml.safe_load(fh) or {}
    content = record.get("content", {})
    registered_at = str(record.get("registered_at"))
    try:
        reg_date = datetime.fromisoformat(registered_at).date()
    except ValueError:
        reg_date = None
    status = RegistrationStatus(
        exists=True,
        intact=record.get("sha256") == content_digest(content),
        registered_at=registered_at,
        before_rollout=bool(reg_date and first_rollout and reg_date < first_rollout),
        sha256=record.get("sha256"),
        content=content,
    )
    status.deviations = _diff(content, registration_content(cfg, guardrails))
    return status
