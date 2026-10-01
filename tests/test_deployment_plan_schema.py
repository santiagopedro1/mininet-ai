from __future__ import annotations

import json
import unittest

from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.compiler import (
    DEPLOYMENT_PLAN_SCHEMA_ID,
    DeploymentPlan,
    compile_experiment,
)
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION


class DeploymentPlanSchemaTests(unittest.TestCase):
    def test_schema_is_versioned_and_describes_concrete_resources(self) -> None:
        schema = DeploymentPlan.model_json_schema(by_alias=True)

        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(schema["$id"], DEPLOYMENT_PLAN_SCHEMA_ID)
        self.assertEqual(schema["$id"], "urn:mininet-ai:schema:v1alpha3:deployment-plan")
        self.assertEqual(
            schema["properties"]["apiVersion"]["const"],
            "mininet-ai/v1alpha3",
        )
        self.assertEqual(schema["properties"]["kind"]["const"], "DeploymentPlan")
        self.assertIn("PlannedController", schema["$defs"])
        self.assertIn("PlannedPort", schema["$defs"])
        self.assertIn("PlannedLink", schema["$defs"])
        self.assertIn("dpid", schema["$defs"]["PlannedSwitch"]["required"])
        self.assertIn("EventTrigger", schema["$defs"])
        self.assertIn("ObservationPolicy", schema["$defs"])
        self.assertIn("MemoryConfiguration", schema["$defs"])
        self.assertIn("ExecutionConfiguration", schema["$defs"])

    def test_compiled_plan_round_trips_through_public_model(self) -> None:
        original = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION)

        restored = DeploymentPlan.model_validate_json(
            original.model_dump_json(by_alias=True)
        )

        self.assertEqual(restored, original)

    def test_runtime_fields_keep_defaults_when_omitted(self) -> None:
        payload = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).model_dump(
            mode="json",
            by_alias=True,
        )
        payload.pop("resourceLimits")
        for agent in payload["agents"]:
            agent.pop("execution")
            agent.pop("memory")
            agent.pop("observationPolicies")
            agent.pop("triggers")

        restored = DeploymentPlan.model_validate(payload)

        self.assertEqual(restored.resource_limits.max_concurrent_invocations, 32)
        self.assertTrue(
            all(agent.triggers[0].type == "manual" for agent in restored.agents)
        )

    def test_older_plans_are_rejected_instead_of_silently_reinterpreted(self) -> None:
        payload = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).model_dump(
            mode="json",
            by_alias=True,
        )
        for version in ("mininet-ai/v1alpha1", "mininet-ai/v1alpha2"):
            with self.subTest(version=version):
                payload["apiVersion"] = version
                with self.assertRaises(ValidationError):
                    DeploymentPlan.model_validate(payload)

    def test_cli_exposes_deployment_plan_schema(self) -> None:
        result = CliRunner().invoke(app, ["schema", "deployment-plan"])

        self.assertEqual(result.exit_code, 0, result.output)
        schema = json.loads(result.output)
        self.assertEqual(schema["$id"], DEPLOYMENT_PLAN_SCHEMA_ID)
        self.assertEqual(schema["title"], "DeploymentPlan")


if __name__ == "__main__":
    unittest.main()
