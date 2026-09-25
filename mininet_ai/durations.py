"""Canonical conversion for validated Mininet AI duration strings."""

from __future__ import annotations

import re


_DURATION_PATTERN = re.compile(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s)")
_DURATION_FACTORS = {"us": 0.000001, "ms": 0.001, "s": 1.0}


def duration_seconds(value: str) -> float:
    """Convert a schema duration to seconds or reject malformed input."""

    match = _DURATION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid duration {value!r}")
    return float(match.group(1)) * _DURATION_FACTORS[match.group(2)]
