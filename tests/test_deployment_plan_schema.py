from __future__ import annotations

import json
import unittest
from pathlib import Path

from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.compiler import (
    DEPLOYMENT_PLAN_SCHEMA_ID,
    DeploymentPlan,
    compile_experiment,
)


ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "examples" / "phase1" / "experiment.yaml"


class DeploymentPlanSchemaTests(unittest.TestCase):
    def test_schema_is_versioned_and_describes_concrete_resources(self) -> None:
        schema = DeploymentPlan.model_json_schema(by_alias=True)

        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        self.assertEqual(schema["$id"], DEPLOYMENT_PLAN_SCHEMA_ID)
        self.assertEqual(
            schema["properties"]["apiVersion"]["const"],
            "mininet-ai/v1alpha1",
        )
        self.assertEqual(schema["properties"]["kind"]["const"], "DeploymentPlan")
        self.assertIn("PlannedController", schema["$defs"])
        self.assertIn("PlannedPort", schema["$defs"])
        self.assertIn("PlannedLink", schema["$defs"])

    def test_compiled_plan_round_trips_through_public_model(self) -> None:
        original = compile_experiment(EXAMPLE)

        restored = DeploymentPlan.model_validate_json(
            original.model_dump_json(by_alias=True)
        )

        self.assertEqual(restored, original)

    def test_cli_exposes_deployment_plan_schema(self) -> None:
        result = CliRunner().invoke(app, ["schema", "deployment-plan"])

        self.assertEqual(result.exit_code, 0, result.output)
        schema = json.loads(result.output)
        self.assertEqual(schema["$id"], DEPLOYMENT_PLAN_SCHEMA_ID)
        self.assertEqual(schema["title"], "DeploymentPlan")


if __name__ == "__main__":
    unittest.main()
