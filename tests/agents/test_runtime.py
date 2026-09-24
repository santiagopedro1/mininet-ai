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
from mininet_ai.sdk import InvocationStatus
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


class OneShotAgentRuntimeTests(unittest.TestCase):
    def runtime(self, response):
        plan = configured_plan(response)
        substrate = FakeSubstrateRuntime(
            clock=lambda: NOW,
            run_id_factory=lambda: "run-1",
        )
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        sink = MemoryAuditSink()
        runtime = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            audit=AuditRecorder(sink, clock=lambda: NOW),
            clock=lambda: NOW,
            invocation_id_factory=lambda: "invoke-1",
        )
        return runtime, run, sink

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
                AuditEventType.MODEL_STARTED,
                AuditEventType.MODEL_COMPLETED,
                AuditEventType.AGENT_COMPLETED,
                AuditEventType.CAPABILITY_STARTED,
                AuditEventType.CAPABILITY_COMPLETED,
            ),
        )

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

    def test_model_failures_become_typed_invocation_results(self) -> None:
        runtime, run, sink = self.runtime({"metadata": {}})

        result = runtime.invoke(run.id, "switch-router@s1", "repair forwarding")

        self.assertEqual(result.status, InvocationStatus.FAILED)
        assert result.issue is not None
        self.assertEqual(result.issue.code, "agent.response.invalid")
        self.assertEqual(sink.events[-1].type, AuditEventType.AGENT_FAILED)

    def test_python_agents_run_without_a_model(self) -> None:
        plan = configured_plan(
            None,
            implementation={
                "type": "python",
                "entrypoint": "tests.agents.helpers:mapping_agent",
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
        self.assertEqual(result.response.message, "inspected switch-router@s1")
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
