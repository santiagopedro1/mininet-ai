"""YAML loading and relative reference resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from yaml.nodes import MappingNode, Node, SequenceNode

from mininet_ai.errors import SpecificationError
from mininet_ai.specification._yaml_locations import (
    field_location,
    nearest_location,
    yaml_position,
)
from mininet_ai.specification.models import (
    AgentBlueprint,
    CapabilityDefinition,
    Experiment,
    ModelConfiguration,
    ModelReference,
    Topology,
)


@dataclass(frozen=True)
class _LoadedBlueprint:
    blueprint: AgentBlueprint
    source: Path
    locations: dict[str, str]


@dataclass(frozen=True)
class LoadedExperiment:
    source: Path
    experiment: Experiment
    topology: Topology
    blueprints: tuple[AgentBlueprint, ...]
    capabilities: tuple[CapabilityDefinition, ...]
    blueprint_sources: tuple[Path, ...] = ()
    blueprint_locations: tuple[dict[str, str], ...] = ()

    @property
    def _blueprint_documents(self) -> tuple[_LoadedBlueprint, ...]:
        # Keep the existing public constructor and dataclasses.replace contract.
        # Adapt its legacy parallel tuples here, not in downstream consumers.
        return tuple(
            _LoadedBlueprint(
                blueprint,
                self.blueprint_sources[index]
                if self.blueprint_sources
                else self.source,
                self.blueprint_locations[index] if self.blueprint_locations else {},
            )
            for index, blueprint in enumerate(self.blueprints)
        )


@dataclass(frozen=True)
class _YamlDocument:
    data: Any
    positions: dict[tuple[Any, ...], str]


def _read_yaml(path: Path) -> _YamlDocument:
    try:
        with path.open(encoding="utf-8") as stream:
            loader = yaml.SafeLoader(stream)
            try:
                root = loader.get_single_node()
                positions = {}

                def locate(
                    node: Node, location: tuple[Any, ...], active: set[int]
                ) -> None:
                    if id(node) in active:
                        return
                    active = active | {id(node)}
                    positions.setdefault(
                        location,
                        yaml_position(node.start_mark),
                    )
                    if isinstance(node, MappingNode):
                        for key, value in node.value:
                            if isinstance(key, yaml.ScalarNode):
                                field = location + (key.value,)
                                positions[field] = yaml_position(key.start_mark)
                                locate(value, field, active)
                    elif isinstance(node, SequenceNode):
                        for index, child in enumerate(node.value):
                            locate(child, location + (index,), active)

                if root is None:
                    return _YamlDocument(None, {})
                locate(root, (), set())
                return _YamlDocument(loader.construct_document(root), positions)
            finally:
                loader.dispose()
    except FileNotFoundError as error:
        raise SpecificationError(f"file not found: {path}") from error
    except OSError as error:
        raise SpecificationError(f"cannot read {path}: {error}") from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        position = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise SpecificationError(f"invalid YAML in {path}{position}") from error


def _model_errors(document: _YamlDocument, path: Path) -> list[str]:
    """Explain invalid model shapes without leaking union internals or values."""
    data = document.data
    if not isinstance(data, dict):
        return []
    if data.get("kind") == "AgentBlueprint":
        blueprints = [((), data)]
    elif isinstance(data.get("blueprints"), list):
        blueprints = [
            (("blueprints", index), value)
            for index, value in enumerate(data.get("blueprints", []))
        ]
    else:
        return []
    details = []
    for origin, blueprint in blueprints:
        if not isinstance(blueprint, dict):
            continue
        metadata = blueprint.get("metadata")
        name = (
            metadata.get("name", "<unnamed>")
            if isinstance(metadata, dict)
            else "<unnamed>"
        )
        context = f"blueprint {name!r} ({path}) model"
        model = blueprint.get("model")
        if not isinstance(model, dict):
            if model is not None:
                position = nearest_location(document.positions, origin + ("model",))
                details.append(
                    f"{context}: {field_location('model', position)}"
                    + ": expected a mapping with provider/name or file/entry fields"
                )
            continue
        reference = bool({"file", "entry"} & model.keys()) or (
            "overrides" in model and not ({"provider", "name"} & model.keys())
        )
        try:
            schema = ModelReference if reference else ModelConfiguration
            schema.model_validate(model)
        except ValidationError as error:
            if reference:
                context += " reference"
            # Only include a known target when it satisfies the local-path rules.
            file = model.get("file")
            if isinstance(file, str) and file:
                try:
                    ModelReference.local_file_only(file)
                except ValueError:
                    pass
                else:
                    context += f" file {file!r}"
            entry = model.get("entry")
            if isinstance(entry, str):
                context += f" entry {entry!r}"
            for issue in error.errors(include_url=False, include_input=False):
                field = ("model",) + tuple(issue["loc"])
                position = nearest_location(document.positions, origin + field)
                details.append(
                    f"{context}: {field_location('.'.join(map(str, field)), position)}"
                    + f": {issue['msg']}"
                )
    return details


def _parse[T: BaseModel](model: type[T], document: _YamlDocument, path: Path) -> T:
    try:
        return model.model_validate(document.data, context={"source": str(path)})
    except ValidationError as error:
        model_errors = _model_errors(document, path)
        if model_errors:
            raise SpecificationError("\n".join(model_errors)) from error
        details = []
        for issue in error.errors(include_url=False):
            location = ".".join(str(part) for part in issue["loc"])
            details.append(f"{location}: {issue['msg']}")
        raise SpecificationError(
            f"invalid {path}:\n  " + "\n  ".join(details)
        ) from error


def _resolve(path: str, base: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (base / candidate).resolve()


def _load_references[T: BaseModel](
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
    document = _read_yaml(source)
    experiment = _parse(Experiment, document, source)
    return _resolve_experiment(experiment, source, document.positions)


def resolve_experiment(
    experiment: Experiment, source: str | Path = "experiment.py"
) -> LoadedExperiment:
    """Resolve a Python-created specification and any relative references."""

    source = Path(source).resolve()
    return _resolve_experiment(experiment, source, {})


def _resolve_experiment(
    experiment: Experiment, source: Path, positions: dict[tuple[Any, ...], str]
) -> LoadedExperiment:
    base = source.parent

    topology_value = experiment.substrate.topology
    if isinstance(topology_value, str):
        topology_path = _resolve(topology_value, base)
        topology = _parse(Topology, _read_yaml(topology_path), topology_path)
    else:
        topology = topology_value

    blueprints = []
    for index, value in enumerate(experiment.blueprints):
        if isinstance(value, str):
            path = _resolve(value, base)
            document = _read_yaml(path)
            blueprint = _parse(AgentBlueprint, document, path)
            fields = document.positions
        else:
            path = source
            blueprint = value
            fields = {
                location[2:]: position
                for location, position in positions.items()
                if location[:2] == ("blueprints", index)
            }
        model_locations = {}
        for location, position in fields.items():
            if location and location[0] == "model":
                field = ".".join(map(str, location))
                model_locations[field] = field_location(field, position)
        blueprints.append(_LoadedBlueprint(blueprint, path, model_locations))

    return LoadedExperiment(
        source=source,
        experiment=experiment,
        topology=topology,
        blueprints=tuple(item.blueprint for item in blueprints),
        capabilities=_load_references(
            experiment.capability_definitions, CapabilityDefinition, base
        ),
        blueprint_sources=tuple(item.source for item in blueprints),
        blueprint_locations=tuple(item.locations for item in blueprints),
    )
