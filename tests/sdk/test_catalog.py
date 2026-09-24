from __future__ import annotations

import unittest

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.sdk import ExecutionCatalog
from tests.compiler.helpers import EXAMPLE


class ExecutionCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = compile_experiment(EXAMPLE)

    def test_resolves_execution_definition_from_normalized_snapshot(self) -> None:
        catalog = ExecutionCatalog(self.plan)

        definition = catalog.resolve("switch-router@s1")

        self.assertEqual(catalog.plan_digest, self.plan.digest)
        self.assertIn("switch-router@s2", catalog.agent_ids)
        self.assertEqual(definition.instance.attachment.targets, ("s1",))
        self.assertEqual(definition.blueprint.metadata.name, "local-router")
        self.assertEqual(
            tuple(item.metadata.name for item in definition.capabilities),
            ("openflow.flow.install",),
        )
        self.assertTrue(definition.policy.require_postcondition_check)

    def test_unknown_instance_has_a_typed_error(self) -> None:
        catalog = ExecutionCatalog(self.plan)

        with self.assertRaises(AgentRuntimeError) as context:
            catalog.resolve("missing")

        self.assertEqual(context.exception.code, "agent.instance.unknown")
        self.assertEqual(context.exception.agent_id, "missing")

    def test_invalid_snapshot_is_rejected_without_guessing(self) -> None:
        plan = self.plan.model_copy(update={"snapshot": {"kind": "Experiment"}})

        with self.assertRaises(AgentRuntimeError) as context:
            ExecutionCatalog(plan)

        self.assertEqual(context.exception.code, "agent.catalog.invalid-snapshot")

    def test_unresolved_snapshot_references_are_rejected(self) -> None:
        snapshot = dict(self.plan.snapshot)
        snapshot["blueprints"] = ["./agent.yaml"]
        plan = self.plan.model_copy(update={"snapshot": snapshot})

        with self.assertRaises(AgentRuntimeError) as context:
            ExecutionCatalog(plan)

        self.assertEqual(
            context.exception.code,
            "agent.catalog.unresolved-reference",
        )

    def test_missing_execution_definitions_have_typed_errors(self) -> None:
        cases = (
            (
                "blueprints",
                [],
                "agent.catalog.blueprint-missing",
            ),
            (
                "capabilityDefinitions",
                [],
                "agent.catalog.capability-missing",
            ),
        )

        for field, value, expected_code in cases:
            with self.subTest(field=field):
                snapshot = dict(self.plan.snapshot)
                snapshot[field] = value
                plan = self.plan.model_copy(update={"snapshot": snapshot})
                with self.assertRaises(AgentRuntimeError) as context:
                    ExecutionCatalog(plan)
                self.assertEqual(context.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
