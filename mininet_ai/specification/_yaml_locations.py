"""Private YAML location lookup and rendering shared by source readers."""

from collections.abc import Mapping
from typing import Any


def yaml_position(mark: Any) -> str:
    return f"line {mark.line + 1}, column {mark.column + 1}"


def nearest_location[T](
    locations: Mapping[tuple[Any, ...], T], field: tuple[Any, ...]
) -> T | None:
    while field not in locations and field:
        field = field[:-1]
    return locations.get(field)


def field_location(field: str, position: str | None) -> str:
    return f"{field} at {position}" if position else field
