from __future__ import annotations

import unittest
from typing import cast

from pydantic import ValidationError

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import CompilationError
from mininet_ai.specification.models import (
    AnomalyDetector,
    CapabilityDefinition,
    ConversationMemoryConfiguration,
    EventTrigger,
    Experiment,
    IntervalTrigger,
    LocalMemoryConfiguration,
    SharedMemoryConfiguration,
)
from tests.compiler.helpers import example_snapshot, named


class ContinuousConfigurationTests(unittest.TestCase):
    def test_triggers_are_compiled_into_each_expanded_agent(self) -> None:
        snapshot = example_snapshot()
        deployment = named(snapshot["agents"], "switch-router")
        deployment["triggers"] = [
            {"type": "manual", "name": "operator"},
            {
                "type": "interval",
                "name": "periodic-health",
                "every": "5s",
                "initialDelay": "1s",
            },
            {
                "type": "event",
                "name": "queue-alert",
                "event": "queue.threshold-exceeded",
                "source": "telemetry",
                "subject": "s1",
                "cooldown": "10s",
            },
        ]

        plan = compile_experiment(Experiment.model_validate(snapshot))
        instances = [
            agent for agent in plan.agents if agent.deployment == "switch-router"
        ]

        self.assertEqual(len(instances), 2)
        self.assertEqual(
            [trigger.type for trigger in instances[0].triggers],
            ["manual", "interval", "event"],
        )
        interval = cast(IntervalTrigger, instances[0].triggers[1])
        event = cast(EventTrigger, instances[0].triggers[2])
        self.assertEqual(interval.every, "5s")
        self.assertEqual(event.event, "queue.threshold-exceeded")
        self.assertEqual(instances[0].triggers, instances[1].triggers)

    def test_observation_policies_are_compiled_and_reference_declared_inputs(
        self,
    ) -> None:
        snapshot = example_snapshot()
        deployment = named(snapshot["agents"], "switch-router")
        deployment["observationPolicies"] = [
            {
                "observation": "tc.queue-occupancy",
                "every": "1s",
                "window": "10s",
                "aggregation": "mean",
                "detectors": [
                    {
                        "type": "threshold",
                        "name": "queue-high",
                        "event": "queue.threshold-exceeded",
                        "path": "queue.depth",
                        "operator": "gte",
                        "value": 80,
                        "cooldown": "5s",
                    },
                    {
                        "type": "anomaly",
                        "name": "queue-anomaly",
                        "event": "queue.anomaly-detected",
                        "path": "queue.depth",
                        "method": "zscore",
                        "sensitivity": 3,
                        "minSamples": 10,
                    },
                ],
            }
        ]

        plan = compile_experiment(Experiment.model_validate(snapshot))
        instance = next(
            agent for agent in plan.agents if agent.id == "switch-router@s1"
        )

        policy = instance.observation_policies[0]
        self.assertEqual(policy.observation, "tc.queue-occupancy")
        self.assertEqual(policy.aggregation, "mean")
        self.assertEqual(policy.detectors[0].event, "queue.threshold-exceeded")
        anomaly = cast(AnomalyDetector, policy.detectors[1])
        self.assertEqual(anomaly.min_samples, 10)

        deployment["observationPolicies"][0]["observation"] = "missing.metric"
        with self.assertRaisesRegex(
            CompilationError,
            "observation policy references undeclared observation 'missing.metric'",
        ):
            compile_experiment(Experiment.model_validate(snapshot))

    def test_memory_execution_and_runtime_limits_are_normalized_in_plan(self) -> None:
        snapshot = example_snapshot()
        blueprint = next(
            item
            for item in snapshot["blueprints"]
            if item["metadata"]["name"] == "local-router"
        )
        blueprint["memory"] = {
            "local": {"maxEntries": 200},
            "conversation": {"maxMessages": 20, "summaries": True},
            "learned": {"scope": "agent", "mode": "agentic"},
            "shared": {"scopes": ["deployment"], "maxEntries": 50},
        }
        deployment = named(snapshot["agents"], "switch-router")
        deployment["execution"] = {
            "queueCapacity": 16,
            "maxConcurrency": 1,
            "overflow": "coalesce",
            "restart": {
                "policy": "on-failure",
                "maxAttempts": 3,
                "backoff": "2s",
            },
        }
        snapshot["resourceLimits"].update(
            {
                "max-concurrent-invocations": 8,
                "max-queued-events": 512,
            }
        )

        plan = compile_experiment(Experiment.model_validate(snapshot))
        instance = next(
            agent for agent in plan.agents if agent.id == "switch-router@s1"
        )

        local = cast(LocalMemoryConfiguration, instance.memory.local)
        conversation = cast(
            ConversationMemoryConfiguration,
            instance.memory.conversation,
        )
        shared = cast(SharedMemoryConfiguration, instance.memory.shared)
        self.assertEqual(local.max_entries, 200)
        self.assertEqual(conversation.max_messages, 20)
        self.assertTrue(conversation.summaries)
        assert instance.memory.learned is not None
        self.assertEqual(instance.memory.learned.scope, "agent")
        self.assertEqual(instance.memory.learned.mode, "agentic")
        self.assertEqual(shared.scopes, ("deployment",))
        self.assertEqual(instance.execution.queue_capacity, 16)
        self.assertEqual(instance.execution.overflow, "coalesce")
        self.assertEqual(instance.execution.restart.max_attempts, 3)
        self.assertEqual(plan.resource_limits.max_concurrent_invocations, 8)
        self.assertEqual(plan.resource_limits.max_queued_events, 512)

    def test_capability_postconditions_and_rollback_are_typed_in_snapshot(
        self,
    ) -> None:
        snapshot = example_snapshot()
        capability = next(
            item
            for item in snapshot["capabilityDefinitions"]
            if item["metadata"]["name"] == "openflow.flow.install"
        )
        capability["postconditions"] = [
            {
                "observation": "openflow.flows",
                "path": "installed",
                "operator": "eq",
                "expected": True,
                "timeout": "5s",
                "interval": "250ms",
            }
        ]
        capability["rollback"] = {"timeout": "10s"}

        plan = compile_experiment(Experiment.model_validate(snapshot))
        restored = Experiment.model_validate(plan.snapshot)
        definition = next(
            item
            for item in restored.capability_definitions
            if isinstance(item, CapabilityDefinition)
            and item.metadata.name == "openflow.flow.install"
        )

        self.assertEqual(definition.postconditions[0].observation, "openflow.flows")
        self.assertEqual(definition.postconditions[0].expected, True)
        self.assertIsNotNone(definition.rollback)
        if definition.rollback is None:
            self.fail("rollback configuration was not preserved")
        self.assertEqual(definition.rollback.timeout, "10s")

        capability["postconditions"][0]["observation"] = "missing.metric"
        with self.assertRaisesRegex(
            CompilationError,
            "postcondition observation 'missing.metric'",
        ):
            compile_experiment(Experiment.model_validate(snapshot))

        capability["reversible"] = False
        with self.assertRaisesRegex(
            ValueError,
            "rollback requires a reversible capability",
        ):
            Experiment.model_validate(snapshot)

    def test_invalid_continuous_timing_is_rejected_by_the_schema(self) -> None:
        snapshot = example_snapshot()
        deployment = named(snapshot["agents"], "switch-router")
        deployment["triggers"] = [
            {"type": "interval", "name": "invalid", "every": "0s"}
        ]
        with self.assertRaisesRegex(ValidationError, "interval must be positive"):
            Experiment.model_validate(snapshot)

        snapshot = example_snapshot()
        deployment = named(snapshot["agents"], "switch-router")
        deployment["observationPolicies"] = [
            {
                "observation": "tc.queue-occupancy",
                "every": "5s",
                "window": "1s",
            }
        ]
        with self.assertRaisesRegex(
            ValidationError,
            "window cannot be shorter than its sampling interval",
        ):
            Experiment.model_validate(snapshot)

        snapshot = example_snapshot()
        deployment = named(snapshot["agents"], "switch-router")
        deployment["execution"]["actionTimeout"] = "0s"
        with self.assertRaisesRegex(
            ValidationError,
            "action timeout must be positive",
        ):
            Experiment.model_validate(snapshot)


if __name__ == "__main__":
    unittest.main()
