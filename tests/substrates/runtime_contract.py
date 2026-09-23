"""Reusable lifecycle conformance tests for substrate runtime adapters."""

from __future__ import annotations

from mininet_ai.errors import RuntimeOperationError
from mininet_ai.substrates import (
    RUNTIME_CONTRACT_VERSION,
    ActionRequest,
    ActionStatus,
    ObservationQuery,
    RunState,
    SubstrateRuntime,
)


class SubstrateRuntimeContract:
    def make_runtime(self) -> SubstrateRuntime:
        raise NotImplementedError

    def make_plan(self):
        raise NotImplementedError

    def test_runtime_satisfies_versioned_protocol(self) -> None:
        runtime = self.make_runtime()

        self.assertIsInstance(runtime, SubstrateRuntime)
        self.assertEqual(runtime.contract_version, RUNTIME_CONTRACT_VERSION)

    def test_deploy_returns_inspectable_running_resources(self) -> None:
        runtime = self.make_runtime()
        plan = self.make_plan()

        run = runtime.deploy(plan)
        snapshot = runtime.inspect(run.id)

        self.assertEqual(run.state, RunState.RUNNING)
        self.assertEqual(snapshot.run, run)
        self.assertEqual(
            tuple(resource.name for resource in snapshot.resources),
            tuple(resource.name for resource in plan.resources),
        )

    def test_running_run_can_observe_and_execute(self) -> None:
        runtime = self.make_runtime()
        plan = self.make_plan()
        run = runtime.deploy(plan)
        target = plan.resources[0].name

        observation = runtime.observe(
            run.id,
            ObservationQuery(name="topology.resources", targets=(target,)),
        )
        action = runtime.execute(
            run.id,
            ActionRequest(
                id="contract-action",
                name="fake.change",
                target=target,
            ),
        )

        self.assertEqual(observation.run_id, run.id)
        self.assertIn(target, observation.values)
        self.assertEqual(action.status, ActionStatus.SUCCEEDED)
        self.assertTrue(action.changed)

    def test_teardown_is_idempotent(self) -> None:
        runtime = self.make_runtime()
        run = runtime.deploy(self.make_plan())

        first = runtime.teardown(run.id)
        second = runtime.teardown(run.id)

        self.assertEqual(first.run.state, RunState.STOPPED)
        self.assertFalse(first.already_stopped)
        self.assertTrue(first.released_resources)
        self.assertTrue(second.already_stopped)
        self.assertEqual(second.released_resources, ())

    def test_unknown_run_has_typed_error(self) -> None:
        runtime = self.make_runtime()

        with self.assertRaises(RuntimeOperationError) as context:
            runtime.inspect("missing")

        self.assertEqual(context.exception.code, "runtime.run.unknown")
        self.assertEqual(context.exception.run_id, "missing")
