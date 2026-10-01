from __future__ import annotations

from typing import Any

from mininet_ai.compiler import compile_experiment
from mininet_ai.specification.models import Experiment
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION


def compiler_multilayer_snapshot() -> dict[str, Any]:
    return compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).snapshot


def experiment_from(snapshot: dict[str, Any]) -> Experiment:
    return Experiment.model_validate(snapshot)


def named(items: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(item for item in items if item["name"] == name)


def switch_experiment(*names: str) -> Experiment:
    return Experiment.model_validate(
        {
            "apiVersion": "mininet-ai/v1alpha3",
            "kind": "Experiment",
            "metadata": {"name": "switch-names"},
            "substrate": {
                "driver": "mininet-ovs",
                "topology": {
                    "resources": [{"name": name, "kind": "switch"} for name in names]
                },
            },
        }
    )
