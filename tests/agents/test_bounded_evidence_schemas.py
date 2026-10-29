"""Strict published evidence must reject empty success, not just bad envelopes."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMAS = Path(__file__).parents[2] / "mininet_ai/compiler/schemas"


def validators():
    bounded = json.loads((SCHEMAS / "bounded-decision-v1alpha1.json").read_text())
    execution = json.loads((SCHEMAS / "agent-execution-v1alpha1.json").read_text())
    registry = Registry().with_resource(bounded["$id"], Resource.from_contents(bounded))
    return Draft202012Validator(bounded), Draft202012Validator(
        execution, registry=registry
    )


def test_execution_schema_rejects_empty_bounded_success():
    _, validator = validators()
    assert not validator.is_valid(
        {
            "executionKind": "bounded-decision",
            "outcome": "succeeded",
            "runtime": {},
        }
    )


@pytest.mark.parametrize(
    "field",
    ["scenario", "request", "timings", "modelProvenance", "effectiveConfiguration"],
)
def test_bounded_schema_rejects_materially_missing_nested_evidence(field):
    from tests.agents.test_bounded_decision import evidence_record

    record = evidence_record().model_dump(mode="json", by_alias=True)
    validator, _ = validators()
    validator.validate(record)
    record[field] = {}
    assert not validator.is_valid(record)
