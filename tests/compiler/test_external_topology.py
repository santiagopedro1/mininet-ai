from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import SpecificationError
from tests.compiler.helpers import example_snapshot, experiment_from


class ExternalTopologyTests(unittest.TestCase):
    def _write_experiment(
        self,
        directory: Path,
        snapshot: dict,
        *,
        topology_text: str | None,
    ) -> Path:
        experiment = dict(snapshot)
        experiment["substrate"] = dict(snapshot["substrate"])
        experiment["substrate"]["topology"] = "./topology.yaml"
        experiment_path = directory / "experiment.yaml"
        experiment_path.write_text(
            yaml.safe_dump(experiment, sort_keys=False), encoding="utf-8"
        )
        if topology_text is not None:
            (directory / "topology.yaml").write_text(topology_text, encoding="utf-8")
        return experiment_path

    def test_relative_external_topology_matches_inline_plan(self) -> None:
        snapshot = example_snapshot()
        inline = compile_experiment(experiment_from(snapshot))
        topology_text = yaml.safe_dump(
            snapshot["substrate"]["topology"], sort_keys=False
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            experiment_path = self._write_experiment(
                Path(temporary_directory),
                snapshot,
                topology_text=topology_text,
            )
            external = compile_experiment(experiment_path)

        self.assertEqual(external.digest, inline.digest)
        self.assertEqual(external.resources, inline.resources)
        self.assertEqual(external.agents, inline.agents)
        self.assertEqual(external.snapshot, inline.snapshot)

    def test_missing_external_topology_reports_resolved_path(self) -> None:
        snapshot = example_snapshot()
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            experiment_path = self._write_experiment(
                directory, snapshot, topology_text=None
            )

            with self.assertRaisesRegex(
                SpecificationError,
                rf"file not found: {directory / 'topology.yaml'}",
            ):
                compile_experiment(experiment_path)

    def test_malformed_external_topology_reports_its_path(self) -> None:
        snapshot = example_snapshot()
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            experiment_path = self._write_experiment(
                directory, snapshot, topology_text="resources: ["
            )

            with self.assertRaisesRegex(
                SpecificationError,
                rf"invalid YAML in {directory / 'topology.yaml'}",
            ):
                compile_experiment(experiment_path)

    def test_schema_invalid_external_topology_reports_its_path(self) -> None:
        snapshot = example_snapshot()
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            experiment_path = self._write_experiment(
                directory,
                snapshot,
                topology_text=yaml.safe_dump({"resources": []}),
            )

            with self.assertRaisesRegex(
                SpecificationError,
                rf"(?s)invalid {directory / 'topology.yaml'}.*resources",
            ):
                compile_experiment(experiment_path)


if __name__ == "__main__":
    unittest.main()
