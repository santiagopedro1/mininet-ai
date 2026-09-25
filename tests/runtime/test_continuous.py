from __future__ import annotations

import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from mininet_ai.compiler import compile_experiment
from mininet_ai.runtime import (
    AgentLifecycleState,
    AgentInvoker,
    ContinuousAgentRuntime,
    ContinuousRuntimeError,
    ContinuousRuntimeState,
    ManualIntentPayload,
    RuntimeEvent,
    RuntimeEventType,
)
from mininet_ai.sdk import (
    AgentInvocationResult,
    AgentResponse,
    AgentRuntimeIssue,
    InvocationStatus,
)
from mininet_ai.specification.models import Experiment
from tests.compiler.helpers import example_snapshot, named


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def configured_plan(
    *,
    triggers: list[dict[str, Any]],
    overflow: str = "reject",
    queue_capacity: int = 4,
    max_concurrency: int = 1,
    global_concurrency: int = 4,
    restart: dict[str, Any] | None = None,
):
    snapshot = example_snapshot()
    deployment = named(snapshot["agents"], "switch-router")
    deployment["placement"]["targets"]["names"] = ["s1"]
    deployment["placement"]["targets"]["matchLabels"] = {}
    deployment["triggers"] = triggers
    deployment["execution"].update(
        {
            "queueCapacity": queue_capacity,
            "maxConcurrency": max_concurrency,
            "overflow": overflow,
        }
    )
    if restart is not None:
        deployment["execution"]["restart"] = restart
    snapshot["resourceLimits"]["max-concurrent-invocations"] = global_concurrency
    snapshot["resourceLimits"]["max-queued-events"] = 16
    return compile_experiment(Experiment.model_validate(snapshot))


def manual_event(
    event_id: str,
    *,
    sequence: int,
    agent_id: str = "switch-router@s1",
) -> RuntimeEvent:
    payload = ManualIntentPayload(agentId=agent_id, intent=f"intent-{event_id}")
    return RuntimeEvent(
        eventId=event_id,
        runId="run-1",
        type=RuntimeEventType.MANUAL_INTENT,
        source="operator",
        subject=agent_id,
        occurredAt=NOW,
        observedAt=NOW,
        sequence=sequence,
        payload=payload.model_dump(mode="json", by_alias=True),
    )


def custom_event(
    event_id: str,
    *,
    sequence: int,
    observed_at: datetime = NOW,
) -> RuntimeEvent:
    return RuntimeEvent(
        eventId=event_id,
        runId="run-1",
        type="queue.threshold-exceeded",
        source="telemetry",
        subject="s1",
        occurredAt=observed_at,
        observedAt=observed_at,
        sequence=sequence,
        payload={"depth": 90},
    )


class RecordingInvoker:
    def __init__(self, *, blocked: bool = False) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.active = 0
        self.max_active = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self._lock = threading.Lock()

    def invoke(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
    ) -> AgentInvocationResult:
        with self._lock:
            self.calls.append((run_id, agent_id, intent))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            invocation = len(self.calls)
            self.entered.set()
        self.release.wait(timeout=2)
        with self._lock:
            self.active -= 1
        return AgentInvocationResult(
            invocationId=f"invoke-{invocation}",
            runId=run_id,
            agentId=agent_id,
            status=InvocationStatus.SUCCEEDED,
            startedAt=NOW,
            completedAt=NOW,
            response=AgentResponse(message="done"),
        )


def wait_until(predicate, *, timeout: float = 1) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not satisfied before timeout")
        time.sleep(0.002)


class ContinuousAgentRuntimeTests(unittest.TestCase):
    def test_pause_resume_and_failure_recovery_follow_compiled_policy(self) -> None:
        plan = configured_plan(
            triggers=[{"type": "manual", "name": "operator"}],
            restart={
                "policy": "on-failure",
                "maxAttempts": 1,
                "backoff": "0s",
            },
        )

        class FlakyInvoker:
            def __init__(self) -> None:
                self.calls = 0

            def invoke(
                self,
                run_id: str,
                agent_id: str,
                intent: str,
            ) -> AgentInvocationResult:
                self.calls += 1
                failed = self.calls == 1
                return AgentInvocationResult(
                    invocationId=f"invoke-{self.calls}",
                    runId=run_id,
                    agentId=agent_id,
                    status=(
                        InvocationStatus.FAILED
                        if failed
                        else InvocationStatus.SUCCEEDED
                    ),
                    startedAt=NOW,
                    completedAt=NOW,
                    response=None if failed else AgentResponse(message="done"),
                    issue=(
                        AgentRuntimeIssue(
                            code="agent.failed",
                            message="temporary failure",
                        )
                        if failed
                        else None
                    ),
                )

        invoker = FlakyInvoker()
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)
        runtime.start()
        runtime.pause("switch-router@s1")
        runtime.publish(manual_event("manual-1", sequence=1))
        time.sleep(0.02)
        self.assertEqual(invoker.calls, 0)
        self.assertEqual(
            runtime.agent_state("switch-router@s1"),
            AgentLifecycleState.PAUSED,
        )

        runtime.resume("switch-router@s1")
        report = runtime.stop()

        self.assertEqual(invoker.calls, 2)
        self.assertEqual(report.completed, 1)
        self.assertEqual(report.failed, 0)
        states = [
            transition.state
            for transition in report.lifecycle
            if transition.agent_id == "switch-router@s1"
        ]
        self.assertEqual(
            states,
            [
                AgentLifecycleState.STARTING,
                AgentLifecycleState.RUNNING,
                AgentLifecycleState.PAUSED,
                AgentLifecycleState.RUNNING,
                AgentLifecycleState.FAILED,
                AgentLifecycleState.RESTARTING,
                AgentLifecycleState.RUNNING,
                AgentLifecycleState.STOPPING,
                AgentLifecycleState.STOPPED,
            ],
        )

    def test_draining_stop_releases_a_paused_agent(self) -> None:
        plan = configured_plan(
            triggers=[{"type": "manual", "name": "operator"}]
        )
        invoker = RecordingInvoker()
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)
        runtime.start()
        runtime.pause("switch-router@s1")
        runtime.publish(manual_event("manual-1", sequence=1))
        wait_until(lambda: runtime.report().enqueued == 1)

        report = runtime.stop(drain=True)

        self.assertEqual(report.completed, 1)
        self.assertEqual(invoker.calls[0][2], "intent-manual-1")
        self.assertEqual(
            runtime.agent_state("switch-router@s1"),
            AgentLifecycleState.STOPPED,
        )

    def test_manual_and_scoped_event_triggers_reuse_bounded_invoker(self) -> None:
        plan = configured_plan(
            triggers=[
                {"type": "manual", "name": "operator"},
                {
                    "type": "event",
                    "name": "queue-alert",
                    "event": "queue.threshold-exceeded",
                    "source": "telemetry",
                    "cooldown": "10s",
                },
            ]
        )
        invoker = RecordingInvoker()
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)

        runtime.start()
        runtime.publish(manual_event("manual-1", sequence=1))
        runtime.publish(custom_event("queue-1", sequence=1))
        runtime.publish(
            custom_event(
                "queue-2",
                sequence=2,
                observed_at=NOW + timedelta(seconds=1),
            )
        )
        report = runtime.stop()

        self.assertIsInstance(invoker, AgentInvoker)
        self.assertEqual(runtime.state, ContinuousRuntimeState.STOPPED)
        self.assertEqual(report.received, 3)
        self.assertEqual(report.matched, 2)
        self.assertEqual(report.completed, 2)
        self.assertEqual(
            report.invocations[0].result.timings.event_detection_seconds,
            0,
        )
        self.assertEqual(
            [record.trigger for record in report.invocations],
            ["operator", "queue-alert"],
        )
        self.assertIn(
            "runtime.trigger.cooldown",
            [issue.code for issue in report.issues],
        )

    def test_coalesce_replaces_queued_work_and_rejects_replayed_events(self) -> None:
        plan = configured_plan(
            triggers=[{"type": "manual", "name": "operator"}],
            overflow="coalesce",
            queue_capacity=1,
        )
        invoker = RecordingInvoker(blocked=True)
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)
        runtime.start()
        runtime.publish(manual_event("event-1", sequence=1))
        self.assertTrue(invoker.entered.wait(timeout=1))
        runtime.publish(manual_event("event-2", sequence=2))
        runtime.publish(manual_event("event-3", sequence=3))
        runtime.publish(manual_event("event-replay", sequence=3))
        wait_until(lambda: runtime.report().coalesced == 1)
        invoker.release.set()

        report = runtime.stop()

        self.assertEqual(report.completed, 2)
        self.assertEqual(report.coalesced, 1)
        self.assertEqual(report.rejected, 1)
        self.assertEqual(
            [record.event_id for record in report.invocations],
            ["event-1", "event-3"],
        )
        self.assertIn(
            "runtime.event.out-of-order",
            [issue.code for issue in report.issues],
        )

    def test_reject_and_drop_oldest_overflow_policies_are_bounded(self) -> None:
        cases = (
            ("reject", "rejected", "event-2"),
            ("drop-oldest", "dropped", "event-3"),
        )
        for policy, counter, final_event in cases:
            with self.subTest(policy=policy):
                plan = configured_plan(
                    triggers=[{"type": "manual", "name": "operator"}],
                    overflow=policy,
                    queue_capacity=1,
                )
                invoker = RecordingInvoker(blocked=True)
                runtime = ContinuousAgentRuntime(plan, "run-1", invoker)
                runtime.start()
                runtime.publish(manual_event("event-1", sequence=1))
                self.assertTrue(invoker.entered.wait(timeout=1))
                runtime.publish(manual_event("event-2", sequence=2))
                runtime.publish(manual_event("event-3", sequence=3))
                wait_until(lambda: getattr(runtime.report(), counter) == 1)
                invoker.release.set()

                report = runtime.stop()

                self.assertEqual(report.completed, 2)
                self.assertEqual(
                    [record.event_id for record in report.invocations],
                    ["event-1", final_event],
                )

    def test_interval_triggers_publish_normalized_events(self) -> None:
        plan = configured_plan(
            triggers=[
                {
                    "type": "interval",
                    "name": "heartbeat",
                    "every": "5ms",
                    "initialDelay": "0s",
                }
            ]
        )
        invoker = RecordingInvoker()
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)

        runtime.start()
        wait_until(lambda: len(invoker.calls) >= 2)
        report = runtime.stop()

        self.assertGreaterEqual(report.completed, 2)
        self.assertTrue(
            all(record.trigger == "heartbeat" for record in report.invocations)
        )
        self.assertTrue(
            all("Interval trigger" in intent for _, _, intent in invoker.calls)
        )

    def test_global_concurrency_limit_serializes_agents(self) -> None:
        plan = configured_plan(
            triggers=[
                {
                    "type": "event",
                    "name": "broadcast",
                    "event": "coordination.tick",
                }
            ],
            max_concurrency=2,
            global_concurrency=1,
        )
        # Restore both switch instances so one broadcast matches two agents.
        snapshot = plan.snapshot.copy()
        snapshot["agents"] = list(snapshot["agents"])
        deployment = named(snapshot["agents"], "switch-router")
        deployment["placement"] = dict(deployment["placement"])
        deployment["placement"]["targets"] = {
            "kind": "switch",
            "names": [],
            "matchLabels": {"role": "edge"},
        }
        plan = compile_experiment(Experiment.model_validate(snapshot))
        invoker = RecordingInvoker(blocked=True)
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)
        event = RuntimeEvent(
            eventId="broadcast-1",
            runId="run-1",
            type="coordination.tick",
            source="coordinator",
            occurredAt=NOW,
            observedAt=NOW,
            sequence=1,
            payload={},
        )

        runtime.start()
        runtime.publish(event)
        self.assertTrue(invoker.entered.wait(timeout=1))
        time.sleep(0.02)
        self.assertEqual(len(invoker.calls), 1)
        invoker.release.set()
        report = runtime.stop()

        self.assertEqual(report.completed, 2)
        self.assertEqual(invoker.max_active, 1)

    def test_per_agent_concurrency_allows_declared_parallelism(self) -> None:
        plan = configured_plan(
            triggers=[{"type": "manual", "name": "operator"}],
            max_concurrency=2,
            global_concurrency=2,
        )
        invoker = RecordingInvoker(blocked=True)
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)

        runtime.start()
        runtime.publish(manual_event("event-1", sequence=1))
        runtime.publish(manual_event("event-2", sequence=2))
        wait_until(lambda: len(invoker.calls) == 2)
        self.assertEqual(invoker.max_active, 2)
        invoker.release.set()
        report = runtime.stop()

        self.assertEqual(report.completed, 2)

    def test_lifecycle_and_run_identity_are_enforced(self) -> None:
        plan = configured_plan(triggers=[{"type": "manual", "name": "operator"}])
        runtime = ContinuousAgentRuntime(plan, "run-1", RecordingInvoker())

        with self.assertRaises(ContinuousRuntimeError) as not_running:
            runtime.publish(manual_event("early", sequence=1))
        self.assertEqual(not_running.exception.code, "runtime.lifecycle.not-running")

        runtime.start()
        wrong = manual_event("wrong", sequence=1).model_copy(
            update={"run_id": "run-2"}
        )
        with self.assertRaises(ContinuousRuntimeError) as mismatch:
            runtime.publish(wrong)
        self.assertEqual(mismatch.exception.code, "runtime.event.run-mismatch")
        runtime.stop()


if __name__ == "__main__":
    unittest.main()
