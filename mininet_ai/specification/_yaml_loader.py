"""Private, value-safe YAML construction boundaries for source readers."""

from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import Node, ScalarNode

from mininet_ai.errors import SpecificationError
from mininet_ai.specification._yaml_locations import field_location, yaml_position


class SourceSafeLoader(yaml.SafeLoader):
    """Normalize only known built-in scalar conversion failures, at any depth."""

    def construct_object(self, node: Node, deep: bool = False) -> Any:
        # Recursive container constructors call this method for each child. Catch
        # at the actual scalar, not at an outer container's unrelated YAML tag.
        failures: dict[str, tuple[type[Exception], ...]] = {
            "tag:yaml.org,2002:bool": (KeyError,),
            "tag:yaml.org,2002:int": (ValueError, IndexError),
            "tag:yaml.org,2002:float": (ValueError, OverflowError, IndexError),
            "tag:yaml.org,2002:timestamp": (ValueError, OverflowError, AttributeError),
        }
        if not isinstance(node, ScalarNode) or node.tag not in failures:
            return super().construct_object(node, deep=deep)
        try:
            return super().construct_object(node, deep=deep)
        except failures[node.tag]:
            raise yaml.constructor.ConstructorError(
                None, None, "invalid scalar value for YAML type", node.start_mark
            ) from None


def string_mapping_key(node: Node, path: Path, location: tuple[Any, ...]) -> str:
    # Never construct a rejected key: containers may contain arbitrary malformed
    # tagged scalars, and even a scalar's conversion exception can expose values.
    # Merge/value tags are YAML syntax handled by SafeLoader.flatten_mapping,
    # not Python key conversions. Preserve that existing source syntax.
    if not isinstance(node, ScalarNode) or node.tag not in {
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:merge",
        "tag:yaml.org,2002:value",
    }:
        field = ".".join(map(str, location)) or "root"
        raise SpecificationError(
            f"invalid YAML in {path}: {field_location(field, yaml_position(node.start_mark))}: "
            "expected a string mapping key"
        )
    return node.value
