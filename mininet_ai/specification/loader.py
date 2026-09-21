"""YAML loading and relative reference resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from mininet_ai.errors import SpecificationError
from mininet_ai.specification.models import (
    AgentBlueprint,
    CapabilityDefinition,
    Experiment,
    Topology,
)


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LoadedExperiment:
    source: Path
    experiment: Experiment
    topology: Topology
    blueprints: tuple[AgentBlueprint, ...]
    capabilities: tuple[CapabilityDefinition, ...]


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return yaml.safe_load(stream)
    except FileNotFoundError as error:
        raise SpecificationError(f"file not found: {path}") from error
    except OSError as error:
        raise SpecificationError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        raise SpecificationError(f"invalid YAML in {path}: {error}") from error


def _parse(model: type[T], data: Any, path: Path) -> T:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        details = []
        for issue in error.errors(include_url=False):
            location = ".".join(str(part) for part in issue["loc"])
            details.append(f"{location}: {issue['msg']}")
        raise SpecificationError(f"invalid {path}:\n  " + "\n  ".join(details)) from error


def _resolve(path: str, base: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def _load_references(
    values: list[T | str], model: type[T], base: Path
) -> tuple[T, ...]:
    resolved: list[T] = []
    for value in values:
        if isinstance(value, str):
            path = _resolve(value, base)
            resolved.append(_parse(model, _read_yaml(path), path))
        else:
            resolved.append(value)
    return tuple(resolved)


def load_experiment(path: str | Path) -> LoadedExperiment:
    """Load an experiment and all paths it references.

    Relative paths are always interpreted relative to the containing
    experiment, making compilation independent of the current directory.
    """

    source = Path(path).resolve()
    experiment = _parse(Experiment, _read_yaml(source), source)
    return resolve_experiment(experiment, source)


def resolve_experiment(
    experiment: Experiment, source: str | Path = "experiment.py"
) -> LoadedExperiment:
    """Resolve a Python-created specification and any relative references."""

    source = Path(source).resolve()
    base = source.parent

    topology_value = experiment.substrate.topology
    if isinstance(topology_value, str):
        topology_path = _resolve(topology_value, base)
        topology = _parse(Topology, _read_yaml(topology_path), topology_path)
    else:
        topology = topology_value

    return LoadedExperiment(
        source=source,
        experiment=experiment,
        topology=topology,
        blueprints=_load_references(experiment.blueprints, AgentBlueprint, base),
        capabilities=_load_references(
            experiment.capability_definitions, CapabilityDefinition, base
        ),
    )
