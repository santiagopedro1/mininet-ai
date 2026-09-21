from __future__ import annotations

from typing import Any

from mininet_ai.compiler import compile_experiment
from mininet_ai.specification.models import Experiment
from tests.golden_plans import GOLDEN_CASES


EXAMPLE = GOLDEN_CASES["phase1"][0]


def example_snapshot() -> dict[str, Any]:
    return compile_experiment(EXAMPLE).snapshot


def experiment_from(snapshot: dict[str, Any]) -> Experiment:
    return Experiment.model_validate(snapshot)


def named(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(item for item in items if item["name"] == name)
