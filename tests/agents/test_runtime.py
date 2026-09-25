from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime
from typing import Any, cast

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.audit import AuditEventType, AuditRecorder, MemoryAuditSink
from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime import InMemorySharedStateStore
from mininet_ai.sdk import InvocationStatus
from mininet_ai.specification.models import Experiment
from mininet_ai.substrates import ActionStatus, FakeSubstrateRuntime
from tests.compiler.helpers import EXAMPLE

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def configured_plan(response, *, implementation=None):
    plan = compile_experiment(EXAMPLE)
    snapshot = copy.deepcopy(plan.snapshot)
    blueprint = snapshot["blueprints"][0]
    if implementation is None:
        blueprint["model"] = {
            "provider": "mock",
            "name": "deterministic",
            "parameters": {"response": response},
        }
    else:
        blueprint["implementation"] = implementation
        blueprint["model"] = None
    return plan.model_copy(update={"snapshot": snapshot})


def configured_shared_plan(response):
    plan = compile_experiment(EXAMPLE)
    snapshot = copy.deepcopy(plan.snapshot)
    blueprint = snapshot["blueprints"][0]
    blueprint["model"] = {
        "provider": "mock",
        "name": "deterministic",
        "parameters": {"response": response},
    }
    blueprint["memory"]["shared"] = {
        "scopes": ["run", "deployment"],
        "maxEntries": 4,
    }
    return compile_experiment(Experiment.model_validate(snapshot))


class OneShotAgentRuntimeTests(unittest.TestCase):
    def runtime(self, response, *, monotonic_clock=None):
        plan = configured_plan(response)
        substrate = FakeSubstrateRuntime(
            clock=lambda: NOW,
            run_id_factory=lambda: "run-1",
        )
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        sink = MemoryAuditSink()
        options = {}
        if monotonic_clock is not None:
            options["monotonic_clock"] = monotonic_clock
        runtime = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            audit=AuditRecorder(sink, clock=lambda: NOW),
            clock=lambda: NOW,
            invocation_id_factory=lambda: "invoke-1",
            **options,
        )
        return runtime, run, sink

    def test_records_separate_context_reasoning_and_action_timings(self) -> None:
        ticks = iter(float(value) for value in range(8))
        runtime, run, _ = self.runtime(
            {"message": "measured"},
            monotonic_clock=lambda: next(ticks),
        )

        result = runtime.invoke(run.id, "switch-router@s1", "inspect")

        self.assertEqual(result.timings.context_build_seconds, 1)
        self.assertEqual(result.timings.reasoning_seconds, 1)
        self.assertEqual(result.timings.action_execution_seconds, 1)
        self.assertEqual(result.timings.total_seconds, 7)
        self.assertIsNone(result.timings.model_queueing_seconds)
        self.assertIsNone(result.timings.action_effect_seconds)

    def test_observes_invokes_authorizes_executes_and_audits(self) -> None:
        response = {
            "message": "install a safe rule",
            "proposals": [
                {
                    "id": "proposal-1",
                    "capability": "openflow.flow.install",
                    "target": "s1",
                    "arguments": {"match": "ip", "actions": "normal"},
                }
            ],
        }
        runtime, run, sink = self.runtime(response)

        result = runtime.invoke(run.id, "switch-router@s1", "repair forwarding")

        self.assertEqual(result.status, InvocationStatus.SUCCEEDED)
        assert result.response is not None
        self.assertEqual(result.response.message, "install a safe rule")
        self.assertEqual(len(result.action_results), 1)
        self.assertEqual(result.action_results[0].status, ActionStatus.SUCCEEDED)
        self.assertEqual(result.action_results[0].output["target"], "s1")
        context_data = cast(dict[str, Any], sink.events[0].data["context"])
        self.assertEqual(
            set(context_data["observations"]),
            {
                "ovs.port-counters",
                "tc.queue-occupancy",
                "topology.neighbors",
            },
        )
        self.assertEqual(
            tuple(event.type for event in sink.events),
            (
                AuditEventType.AGENT_STARTED,
                AuditEventType.AGENT_COMPLETED,
                AuditEventType.CAPABILITY_STARTED,
                AuditEventType.CAPABILITY_COMPLETED,
            ),
        )
        completed = cast(dict[str, Any], sink.events[1].data)
        runtime_data = cast(dict[str, Any], completed["runtime"])
        self.assertEqual(runtime_data["name"], "agno")
        self.assertEqual(runtime_data["agnoRunId"], "invoke-1")
        self.assertEqual(runtime_data["sessionId"], "run-1:switch-router@s1")
        self.assertRegex(cast(str, runtime_data["runtimeVersion"]), r"^3\.0\.")
        memory = cast(dict[str, Any], runtime_data["memory"])
        self.assertFalse(memory["learnedMemory"])
        self.assertFalse(memory["conversationHistory"])
        metrics = cast(dict[str, Any], runtime_data["metrics"])
        self.assertEqual(metrics["totalTokens"], 0)

    def test_action_rejection_rejects_the_invocation(self) -> None:
        response = {
            "proposals": [
                {
                    "id": "proposal-1",
                    "capability": "openflow.flow.install",
                    "target": "s2",
                    "arguments": {"match": "ip", "actions": "normal"},
                }
            ]
        }
        runtime, run, _ = self.runtime(response)

        result = runtime.invoke(run.id, "switch-router@s1", "repair forwarding")

        self.assertEqual(result.status, InvocationStatus.REJECTED)
        assert result.issue is not None
        self.assertEqual(result.issue.code, "capability.target.out-of-scope")
        self.assertEqual(result.action_results[0].status, ActionStatus.REJECTED)

    def test_shared_state_updates_are_scoped_audited_and_visible_next_run(
        self,
    ) -> None:
        response = {
            "sharedStateUpdates": [
                {
                    "scope": "run",
                    "key": "preferred-path",
                    "value": "west",
                    "expectedVersion": 0,
                },
                {
                    "scope": "deployment",
                    "key": "leader",
                    "value": "s1",
                },
            ]
        }
        plan = configured_shared_plan(response)
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "shared-run")
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        sink = MemoryAuditSink()
        state = InMemorySharedStateStore(clock=lambda: NOW)
        invocation_ids = iter(("shared-1", "shared-2"))
        runtime = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            audit=AuditRecorder(sink, clock=lambda: NOW),
            shared_state=state,
            clock=lambda: NOW,
            invocation_id_factory=lambda: next(invocation_ids),
        )

        first = runtime.invoke(run.id, "switch-router@s1", "choose path")
        second = runtime.invoke(run.id, "switch-router@s1", "choose again")

        self.assertEqual(first.status, InvocationStatus.SUCCEEDED)
        self.assertEqual(len(first.shared_state_changes), 2)
        self.assertEqual(first.shared_state_changes[0].version, 1)
        self.assertEqual(second.status, InvocationStatus.REJECTED)
        assert second.issue is not None
        self.assertEqual(second.issue.code, "state.version.conflict")
        second_started = next(
            event
            for event in sink.events
            if event.type == AuditEventType.AGENT_STARTED
            and event.invocation_id == "shared-2"
        )
        context_data = cast(dict[str, Any], second_started.data["context"])
        shared_data = cast(dict[str, Any], context_data["sharedState"])
        run_state = cast(dict[str, Any], shared_data["run"])
        self.assertEqual(run_state["preferred-path"]["value"], "west")
        self.assertIn(
            AuditEventType.SHARED_STATE_UPDATED,
            tuple(event.type for event in sink.events),
        )
        self.assertEqual(sink.events[-1].type, AuditEventType.SHARED_STATE_FAILED)

    def test_model_failures_become_typed_invocation_results(self) -> None:
        runtime, run, sink = self.runtime({"metadata": {}})

        result = runtime.invoke(run.id, "switch-router@s1", "repair forwarding")

        self.assertEqual(result.status, InvocationStatus.FAILED)
        assert result.issue is not None
        self.assertEqual(
            result.issue.code,
            "agent.agno.deterministic-response-invalid",
        )
        self.assertEqual(sink.events[-1].type, AuditEventType.AGENT_FAILED)

    def test_python_agno_factories_run_through_the_same_runtime(self) -> None:
        plan = configured_plan(
            None,
            implementation={
                "type": "python",
                "entrypoint": "tests.agents.agno_helpers:agent_factory",
            },
        )
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "run-python")
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        runtime = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            invocation_id_factory=lambda: "invoke-python",
        )

        result = runtime.invoke(run.id, "switch-router@s1", "inspect")

        self.assertEqual(result.status, InvocationStatus.SUCCEEDED)
        assert result.response is not None
        self.assertEqual(result.response.message, "from Python Agno factory")
        self.assertEqual(result.action_results, ())

    def test_run_must_be_running_and_match_the_compiled_plan(self) -> None:
        runtime, run, _ = self.runtime({"message": "done"})
        runtime._substrate.teardown(run.id)
        with self.assertRaises(AgentRuntimeError) as stopped:
            runtime.invoke(run.id, "switch-router@s1", "inspect")
        self.assertEqual(stopped.exception.code, "agent.run.not-running")

        plan = configured_plan({"message": "done"})
        other_plan = plan.model_copy(update={"digest": "sha256:" + "0" * 64})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "other-run")
        other_run = substrate.deploy(other_plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        mismatched = OneShotAgentRuntime(plan, substrate, registries)
        with self.assertRaises(AgentRuntimeError) as mismatch:
            mismatched.invoke(other_run.id, "switch-router@s1", "inspect")
        self.assertEqual(mismatch.exception.code, "agent.run.plan-mismatch")

    def test_empty_intent_and_invocation_ids_are_rejected(self) -> None:
        runtime, run, _ = self.runtime({"message": "done"})
        with self.assertRaises(AgentRuntimeError) as intent:
            runtime.invoke(run.id, "switch-router@s1", "")
        self.assertEqual(intent.exception.code, "agent.intent.invalid")

        runtime._invocation_id_factory = lambda: ""
        with self.assertRaises(AgentRuntimeError) as invocation:
            runtime.invoke(run.id, "switch-router@s1", "inspect")
        self.assertEqual(invocation.exception.code, "agent.invocation.invalid-id")


if __name__ == "__main__":
    unittest.main()
