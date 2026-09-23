from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import RuntimeOperationError
from mininet_ai.substrates import (
    FakeSubstrateRuntime,
    ObservationQuery,
    RunState,
    RuntimeRegistry,
    create_substrate_runtime,
    runtime_registry,
)
from tests.golden_plans import GOLDEN_CASES
from tests.substrates.runtime_contract import SubstrateRuntimeContract


EXAMPLE = GOLDEN_CASES["phase1"][0]


class IncrementingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(seconds=1)
        return result


class FakeRuntimeContractTests(SubstrateRuntimeContract, unittest.TestCase):
    def make_runtime(self) -> FakeSubstrateRuntime:
        return FakeSubstrateRuntime(
            clock=IncrementingClock(),
            run_id_factory=lambda: "contract-run",
        )

    def make_plan(self):
        return compile_experiment(EXAMPLE)


class FakeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = compile_experiment(EXAMPLE)
        self.runtime = FakeSubstrateRuntime(
            clock=IncrementingClock(),
            run_id_factory=lambda: "test-run",
        )

    def test_builtin_runtime_registry_constructs_fake_adapter(self) -> None:
        runtime = create_substrate_runtime("fake")

        self.assertIsInstance(runtime, FakeSubstrateRuntime)
        self.assertEqual(runtime_registry.names, ("fake", "mininet-ovs"))

    def test_registry_rejects_invalid_factories(self) -> None:
        registry = RuntimeRegistry()
        registry.register("invalid", lambda: object())

        with self.assertRaisesRegex(TypeError, "does not satisfy the contract"):
            registry.create("invalid")
        with self.assertRaisesRegex(LookupError, "available: invalid"):
            registry.create("missing")

    def test_registry_rejects_incompatible_contract_version(self) -> None:
        class OldRuntime(FakeSubstrateRuntime):
            contract_version = "v0"

        registry = RuntimeRegistry()
        registry.register("fake", OldRuntime)

        with self.assertRaisesRegex(ValueError, "unsupported contract version 'v0'"):
            registry.create("fake")

    def test_deploy_rejects_plan_for_another_substrate(self) -> None:
        other_plan = self.plan.model_copy(update={"substrate": "mininet-ovs"})

        with self.assertRaises(RuntimeOperationError) as context:
            self.runtime.deploy(other_plan)

        self.assertEqual(context.exception.code, "runtime.substrate.mismatch")

    def test_duplicate_generated_run_id_is_rejected(self) -> None:
        self.runtime.deploy(self.plan)

        with self.assertRaises(RuntimeOperationError) as context:
            self.runtime.deploy(self.plan)

        self.assertEqual(context.exception.code, "runtime.run.duplicate")

    def test_unknown_resource_is_rejected(self) -> None:
        run = self.runtime.deploy(self.plan)

        with self.assertRaises(RuntimeOperationError) as context:
            self.runtime.observe(
                run.id,
                ObservationQuery(name="topology.resources", targets=("missing",)),
            )

        self.assertEqual(context.exception.code, "runtime.resource.unknown")

    def test_stopped_run_rejects_observations(self) -> None:
        run = self.runtime.deploy(self.plan)
        self.runtime.teardown(run.id)

        with self.assertRaises(RuntimeOperationError) as context:
            self.runtime.observe(
                run.id,
                ObservationQuery(
                    name="topology.resources",
                    targets=(self.plan.resources[0].name,),
                ),
            )

        self.assertEqual(context.exception.code, "runtime.run.not-running")

    def test_runtime_snapshot_round_trips_as_json(self) -> None:
        run = self.runtime.deploy(self.plan)
        snapshot = self.runtime.inspect(run.id)

        restored = type(snapshot).model_validate_json(snapshot.model_dump_json())

        self.assertEqual(restored, snapshot)
        self.assertEqual(json.loads(snapshot.model_dump_json())["run"]["id"], run.id)


if __name__ == "__main__":
    unittest.main()
