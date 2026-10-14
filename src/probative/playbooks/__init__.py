"""Function playbooks: units of work, metrics, guardrails and traps per function.

v1 ships the IT service desk. Adding a function means adding a playbook and a
mapping, not a new product.
"""

from probative.playbooks.base import Metric, Playbook
from probative.playbooks.service_desk import SERVICE_DESK

PLAYBOOKS: dict[str, Playbook] = {SERVICE_DESK.name: SERVICE_DESK}


def get_playbook(name: str) -> Playbook:
    try:
        return PLAYBOOKS[name]
    except KeyError as exc:
        raise ValueError(f"unknown playbook {name!r}; available: {sorted(PLAYBOOKS)}") from exc


__all__ = ["PLAYBOOKS", "Metric", "Playbook", "get_playbook"]
