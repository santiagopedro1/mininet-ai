"""Safe reconciliation decisions for a scenario-owned fixed action bundle."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reconciliation:
    policy: str
    present: tuple[int, ...]
    missing: tuple[int, ...]


def reconcile_bundle(states: tuple[str, ...]) -> Reconciliation:
    """Accept only exact, established present/missing state; never guess effects."""
    if any(state not in {"present", "missing"} for state in states):
        return Reconciliation("unknown", (), tuple(range(len(states))))
    present = tuple(index for index, state in enumerate(states) if state == "present")
    missing = tuple(index for index, state in enumerate(states) if state == "missing")
    if present != tuple(range(len(present))):
        return Reconciliation("unknown", (), tuple(range(len(states))))
    if not present:
        return Reconciliation("verified-primary", present, missing)
    if not missing:
        return Reconciliation("verified-recovery", present, missing)
    return Reconciliation("partial", present, missing)
