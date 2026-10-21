"""Strict, secret-safe YAML reading for shared model files."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.nodes import MappingNode, Node, SequenceNode

from mininet_ai.errors import SpecificationError
from mininet_ai.specification.models import ModelConfigurations


def _position(mark: Any) -> str:
    return f"line {mark.line + 1}, column {mark.column + 1}"


def read_model_configurations(path: Path) -> ModelConfigurations:
    """Read and validate the whole file, rejecting duplicates at every depth.

    Use a private loader instance: no global YAML constructors are modified.
    Never render PyYAML exceptions, which can include source lines and values.
    """
    marks: dict[tuple[Any, ...], Any] = {}
    try:
        with path.open(encoding="utf-8") as stream:
            loader = yaml.SafeLoader(stream)
            try:
                root = loader.get_single_node()

                def reject_cycles(node: Node, active: set[int]) -> None:
                    if id(node) in active:
                        raise SpecificationError(
                            f"recursive YAML aliases are unsupported at {_position(node.start_mark)}"
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
                            key = loader.construct_object(key_node, deep=True)
                            try:
                                previous = seen.get(key)
                            except TypeError:
                                raise SpecificationError(
                                    f"invalid mapping key at {_position(key_node.start_mark)}"
                                ) from None
                            field = location + (key,)
                            if previous is not None:
                                name = ".".join(map(str, field))
                                raise SpecificationError(
                                    f"duplicate mapping key {name} at "
                                    f"{_position(key_node.start_mark)}; first defined at "
                                    f"{_position(previous)}"
                                )
                            seen[key] = key_node.start_mark
                            marks[field] = key_node.start_mark
                            inspect(value_node, field, active)
                    elif isinstance(node, SequenceNode):
                        for index, child in enumerate(node.value):
                            marks[location + (index,)] = child.start_mark
                            inspect(child, location + (index,), active)

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
    except (OSError, UnicodeError, ValueError) as error:
        raise SpecificationError(f"cannot read model file {path}") from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        position = f" at {_position(mark)}" if mark is not None else ""
        raise SpecificationError(f"invalid YAML in {path}{position}") from error

    try:
        return ModelConfigurations.model_validate(data)
    except ValidationError as error:
        details = []
        for issue in error.errors(include_url=False):
            location = tuple(issue["loc"])
            nearest = location
            while nearest not in marks and nearest:
                nearest = nearest[:-1]
            position = f" at {_position(marks[nearest])}" if nearest in marks else ""
            details.append(
                f"{'.'.join(map(str, location)) or 'root'}{position}: {issue['msg']}"
            )
        raise SpecificationError(
            f"invalid {path}:\n  " + "\n  ".join(details)
        ) from error
