"""Bounded, read-only validation of the original v1alpha3 saved contract.

These frozen schemas must not be regenerated from current source models. New
source-time construction restrictions belong in compilation, not this reader.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

LEGACY_API_VERSION = "mininet-ai/v1alpha3"


@cache
def _validator(document: str) -> Draft202012Validator:
    schema = (
        files("mininet_ai.compiler")
        .joinpath("schemas", f"v1alpha3-{document}.json")
        .read_text(encoding="utf-8")
    )
    return Draft202012Validator(json.loads(schema))


def validate_legacy_plan(payload: dict[str, Any]) -> None:
    """Check original serialized shape and identity without changing either."""
    for document, value in (
        ("deployment-plan", payload),
        ("experiment", payload.get("snapshot")),
    ):
        error = next(_validator(document).iter_errors(value), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path) or "root"
            # jsonschema's default message can echo arbitrary configuration values.
            raise ValueError(
                f"invalid v1alpha3 saved {document} field {path}: "
                f"does not satisfy original {error.validator} rule"
            )
    canonical = json.dumps(payload["snapshot"], sort_keys=True, separators=(",", ":"))
    digest = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
    if payload["digest"] != digest:
        raise ValueError("v1alpha3 saved plan snapshot digest mismatch")
