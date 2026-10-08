from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import SpecificationError
from mininet_ai.specification.loader import load_experiment
from mininet_ai.specification.models import (
    API_VERSION,
    AgentBlueprint,
    CapabilityDefinition,
    Experiment,
)
from tests.specification_fixtures import COMPILER_MULTILAYER_SPECIFICATION


class PublicContractVersionTests(unittest.TestCase):
    def test_cli_schemas_and_constant_use_v1alpha3(self) -> None:
        self.assertEqual(API_VERSION, "mininet-ai/v1alpha3")
        for name in ("experiment", "agent-blueprint", "capability", "deployment-plan"):
            with self.subTest(schema=name):
                result = CliRunner().invoke(app, ["schema", name])
                self.assertEqual(result.exit_code, 0, result.output)
                schema = json.loads(result.output)
                self.assertEqual(
                    schema["properties"]["apiVersion"]["const"], "mininet-ai/v1alpha3"
                )

    def test_public_models_reject_older_documents(self) -> None:
        snapshot = compile_experiment(COMPILER_MULTILAYER_SPECIFICATION).snapshot
        for model, document in (
            (Experiment, snapshot),
            (AgentBlueprint, snapshot["blueprints"][0]),
            (CapabilityDefinition, snapshot["capabilityDefinitions"][0]),
        ):
            for version in ("mininet-ai/v1alpha1", "mininet-ai/v1alpha2"):
                with self.subTest(model=model.__name__, version=version):
                    older_document = {**document, "apiVersion": version}
                    with self.assertRaises(ValidationError):
                        model.model_validate(older_document)

    def test_loader_rejects_old_experiments_and_inline_definitions(self) -> None:
        for version in ("mininet-ai/v1alpha1", "mininet-ai/v1alpha2"):
            for key in (None, "blueprints", "capabilityDefinitions"):
                with (
                    self.subTest(version=version, document=key),
                    TemporaryDirectory() as temporary,
                ):
                    snapshot = compile_experiment(
                        COMPILER_MULTILAYER_SPECIFICATION
                    ).snapshot
                    document = snapshot if key is None else snapshot[key][0]
                    document["apiVersion"] = version
                    experiment = Path(temporary) / "experiment.yaml"
                    experiment.write_text(yaml.safe_dump(snapshot), encoding="utf-8")
                    with self.assertRaisesRegex(SpecificationError, "apiVersion"):
                        load_experiment(experiment)

    def test_loader_rejects_v1alpha2_external_references(self) -> None:
        for key in ("blueprints", "capabilityDefinitions"):
            with self.subTest(reference=key), TemporaryDirectory() as temporary:
                snapshot = compile_experiment(
                    COMPILER_MULTILAYER_SPECIFICATION
                ).snapshot
                reference = snapshot[key][0]
                reference["apiVersion"] = "mininet-ai/v1alpha2"
                directory = Path(temporary)
                (directory / "reference.yaml").write_text(
                    yaml.safe_dump(reference), encoding="utf-8"
                )
                snapshot[key] = ["./reference.yaml"]
                experiment = directory / "experiment.yaml"
                experiment.write_text(yaml.safe_dump(snapshot), encoding="utf-8")
                with self.assertRaisesRegex(SpecificationError, "apiVersion"):
                    load_experiment(experiment)
