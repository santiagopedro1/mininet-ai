"""Strict, secret-safe YAML reading for shared model files."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode, Node, SequenceNode

from mininet_ai.errors import SpecificationError
from mininet_ai.specification._yaml_loader import SourceSafeLoader, string_mapping_key
from mininet_ai.specification._yaml_locations import (
    field_location,
    nearest_location,
    yaml_position,
)
from mininet_ai.specification.model_validation import (
    validate_shared_model_configuration,
)
from mininet_ai.specification.models import ModelConfiguration, ModelConfigurations


@dataclass(frozen=True)
class ModelConfigurationSource:
    models: dict[str, ModelConfiguration]
    entry_locations: dict[str, str]
    field_locations: dict[str, dict[str, str]]


def read_model_configurations(path: Path) -> ModelConfigurationSource:
    """Read and validate the whole file, rejecting duplicates at every depth.

    Use a private loader instance: no global YAML constructors are modified.
    Never render PyYAML exceptions, which can include source lines and values.
    """
    marks: dict[tuple[Any, ...], Any] = {}
    try:
        with path.open(encoding="utf-8") as stream:
            loader = SourceSafeLoader(stream)
            try:
                root = loader.get_single_node()

                def construct_scalar(node: Node, location: tuple[Any, ...]) -> Any:
                    try:
                        return loader.construct_object(node, deep=True)
                    except yaml.YAMLError:
                        mark = marks.get(location, node.start_mark)
                        field = ".".join(map(str, location)) or "root"
                        raise SpecificationError(
                            f"invalid YAML in {path}: {field_location(field, yaml_position(mark))}: "
                            "invalid scalar value for YAML type"
                        ) from None

                def reject_cycles(node: Node, active: set[int]) -> None:
                    if id(node) in active:
                        raise SpecificationError(
                            f"recursive YAML aliases are unsupported at {yaml_position(node.start_mark)}"
                        )
                    active = active | {id(node)}
                    if isinstance(node, MappingNode):
                        for key, value in node.value:
                            reject_cycles(key, active)
                            reject_cycles(value, active)
                    elif isinstance(node, SequenceNode):
                        for child in node.value:
                            reject_cycles(child, active)

                def inspect(
                    node: Node, location: tuple[Any, ...], active: set[int]
                ) -> None:
                    if id(node) in active:
                        raise SpecificationError(
                            "recursive YAML aliases are unsupported"
                        )
                    active = active | {id(node)}
                    if isinstance(node, MappingNode):
                        loader.flatten_mapping(node)
                        seen: dict[Any, Any] = {}
                        for key_node, value_node in node.value:
                            key = string_mapping_key(key_node, path, location)
                            previous = seen.get(key)
                            field = location + (key,)
                            if previous is not None:
                                name = ".".join(map(str, field))
                                raise SpecificationError(
                                    f"duplicate mapping key {name} at "
                                    f"{yaml_position(key_node.start_mark)}; first defined at "
                                    f"{yaml_position(previous)}"
                                )
                            seen[key] = key_node.start_mark
                            marks[field] = key_node.start_mark
                            inspect(value_node, field, active)
                    elif isinstance(node, SequenceNode):
                        for index, child in enumerate(node.value):
                            marks[location + (index,)] = child.start_mark
                            inspect(child, location + (index,), active)
                    else:
                        construct_scalar(node, location)

                if root is None:
                    data = None
                else:
                    marks[()] = root.start_mark
                    # Check before merge flattening mutates the node graph.
                    reject_cycles(root, set())
                    inspect(root, (), set())
                    data = loader.construct_document(root)
            finally:
                loader.dispose()
    except (OSError, UnicodeError) as error:
        raise SpecificationError(f"cannot read model file {path}") from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        position = f" at {yaml_position(mark)}" if mark is not None else ""
        raise SpecificationError(f"invalid YAML in {path}{position}") from error

    try:
        configurations = ModelConfigurations.model_validate(data)
    except ValidationError as error:
        details = []
        for issue in error.errors(include_url=False):
            location = tuple(issue["loc"])
            mark = nearest_location(marks, location)
            position = yaml_position(mark) if mark is not None else None
            details.append(
                f"{field_location('.'.join(map(str, location)) or 'root', position)}: {issue['msg']}"
            )
        raise SpecificationError(
            f"invalid {path}:\n  " + "\n  ".join(details)
        ) from error

    locations = {}
    fields = {}
    for name, configuration in configurations.models.items():
        mark = marks.get(("models", name))
        position = f" at {yaml_position(mark)}" if mark is not None else ""
        locations[name] = position
        fields[name] = {}
        for field, field_mark in marks.items():
            if field[:2] == ("models", name) and len(field) > 2:
                suffix = ".".join(map(str, field[2:]))
                fields[name][f"model.{suffix}"] = field_location(
                    f"models.{name}.{suffix}", yaml_position(field_mark)
                )
        validate_shared_model_configuration(
            configuration, context=f"models.{name}{position}", locations=fields[name]
        )
    return ModelConfigurationSource(configurations.models, locations, fields)
