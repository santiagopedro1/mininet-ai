from __future__ import annotations

import threading
import time
import unittest
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from mininet_ai.compiler import compile_experiment
from mininet_ai.runtime import (
    ContinuousAgentRuntime,
    DetectorEventPayload,
    RuntimeEvent,
    RuntimeEventPublisher,
    RuntimeEventType,
    TelemetryPipeline,
    TelemetryPipelineError,
    TelemetryPipelineState,
)
from mininet_ai.sdk import AgentInvocationResult, AgentResponse, InvocationStatus
from mininet_ai.specification.models import Experiment
from mininet_ai.substrates import (
    FakeSubstrateRuntime,
    ObservationQuery,
    ObservationResult,
)
from tests.compiler.helpers import example_snapshot, named


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def configured_plan(
    *,
    aggregation: str = "mean",
    detectors: list[dict[str, Any]],
    every: str = "1s",
    window: str = "10s",
):
    snapshot = example_snapshot()
    deployment = named(snapshot["agents"], "switch-router")
    deployment["placement"]["targets"]["names"] = ["s1"]
    deployment["placement"]["targets"]["matchLabels"] = {}
    deployment["observationPolicies"] = [
        {
            "observation": "tc.queue-occupancy",
            "every": every,
            "window": window,
            "aggregation": aggregation,
            "detectors": detectors,
        }
    ]
    deployment["triggers"] = [
        {
            "type": "event",
            "name": "queue-alert",
            "event": detector["event"],
        }
        for detector in detectors
    ]
    return compile_experiment(Experiment.model_validate(snapshot))


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self._lock = threading.Lock()

    def publish(self, event: RuntimeEvent) -> None:
        with self._lock:
            self.events.append(event)


class SequenceSubstrate(FakeSubstrateRuntime):
    def __init__(
        self,
        values: list[dict[str, Any]],
        timestamps: list[datetime],
    ) -> None:
        super().__init__(run_id_factory=lambda: "run-1")
        self._values = deque(values)
        self._timestamps = deque(timestamps)

    def observe(
        self,
        run_id: str,
        query: ObservationQuery,
    ) -> ObservationResult:
        return ObservationResult(
            run_id=run_id,
            query=query,
            observed_at=self._timestamps.popleft(),
            values=self._values.popleft(),
        )


def wait_until(predicate, *, timeout: float = 1) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition was not satisfied before timeout")
        time.sleep(0.002)


class TelemetryPipelineTests(unittest.TestCase):
    def test_all_declared_aggregations_preserve_the_observation_shape(self) -> None:
        expectations = {
            "latest": 10,
            "minimum": 4,
            "maximum": 10,
            "mean": 7,
            "sum": 14,
        }
        for aggregation, expected in expectations.items():
            with self.subTest(aggregation=aggregation):
                plan = configured_plan(
                    aggregation=aggregation,
                    detectors=[],
                )
                pipeline = TelemetryPipeline(
                    plan,
                    "run-1",
                    SequenceSubstrate(
                        [
                            {"s1": {"queue": {"depth": 4}}},
                            {"s1": {"queue": {"depth": 10}}},
                        ],
                        [NOW, NOW + timedelta(seconds=1)],
                    ),
                    RecordingPublisher(),
                    clock=lambda: NOW + timedelta(seconds=20),
                )

                pipeline.sample_once(
                    "switch-router@s1", "tc.queue-occupancy"
                )
                event = pipeline.sample_once(
                    "switch-router@s1", "tc.queue-occupancy"
                )[0]

                values = cast(dict[str, Any], event.payload["values"])
                self.assertEqual(values["s1"]["queue"]["depth"], expected)

    def test_mean_window_emits_threshold_event_with_causation_and_cooldown(
        self,
    ) -> None:
        plan = configured_plan(
            detectors=[
                {
                    "type": "threshold",
                    "name": "queue-high",
                    "event": "queue.threshold-exceeded",
                    "path": "queue.depth",
                    "operator": "gte",
                    "value": 80,
                    "cooldown": "5s",
                }
            ]
        )
        substrate = SequenceSubstrate(
            [
                {"s1": {"queue": {"depth": 60}}},
                {"s1": {"queue": {"depth": 100}}},
                {"s1": {"queue": {"depth": 100}}},
            ],
            [NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)],
        )
        publisher = RecordingPublisher()
        event_ids = iter(("obs-1", "obs-2", "detector-1", "obs-3"))
        pipeline = TelemetryPipeline(
            plan,
            "run-1",
            substrate,
            publisher,
            clock=lambda: NOW + timedelta(seconds=20),
            event_id_factory=lambda: next(event_ids),
        )

        first = pipeline.sample_once(
            "switch-router@s1", "tc.queue-occupancy"
        )
        second = pipeline.sample_once(
            "switch-router@s1", "tc.queue-occupancy"
        )
        third = pipeline.sample_once(
            "switch-router@s1", "tc.queue-occupancy"
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(third), 1)
        observation = second[0]
        detector = second[1]
        self.assertEqual(observation.type, RuntimeEventType.OBSERVATION_RECORDED)
        recorded_values = cast(dict[str, Any], observation.payload["values"])
        self.assertEqual(recorded_values["s1"]["queue"]["depth"], 80)
        self.assertEqual(detector.type, "queue.threshold-exceeded")
        self.assertEqual(detector.subject, "s1")
        self.assertEqual(detector.causation_id, observation.event_id)
        details = detector.payload_as(DetectorEventPayload)
        self.assertEqual(details.value, 80)
        self.assertEqual(details.threshold, 80)
        self.assertEqual(details.sample_count, 2)
        report = pipeline.report()
        self.assertEqual(report.samples, 3)
        self.assertEqual(report.observations, 3)
        self.assertEqual(report.detector_events, 1)

    def test_zscore_uses_prior_aggregates_as_its_baseline(self) -> None:
        plan = configured_plan(
            aggregation="latest",
            detectors=[
                {
                    "type": "anomaly",
                    "name": "queue-anomaly",
                    "event": "queue.anomaly-detected",
                    "path": "queue.depth",
                    "sensitivity": 2,
                    "minSamples": 3,
                }
            ],
        )
        substrate = SequenceSubstrate(
            [
                {"s1": {"queue": {"depth": value}}}
                for value in (10, 10, 10, 20)
            ],
            [NOW + timedelta(seconds=index) for index in range(4)],
        )
        publisher = RecordingPublisher()
        pipeline = TelemetryPipeline(
            plan,
            "run-1",
            substrate,
            publisher,
            clock=lambda: NOW + timedelta(seconds=20),
        )

        for _ in range(3):
            pipeline.sample_once(
                "switch-router@s1", "tc.queue-occupancy"
            )
        emitted = pipeline.sample_once(
            "switch-router@s1", "tc.queue-occupancy"
        )

        self.assertEqual(len(emitted), 2)
        details = emitted[1].payload_as(DetectorEventPayload)
        self.assertEqual(details.detector_type, "anomaly")
        self.assertEqual(details.baseline_mean, 10)
        self.assertEqual(details.baseline_stddev, 0)
        assert details.score is not None
        self.assertGreater(details.score, 2)
        self.assertEqual(details.sample_count, 3)

    def test_unknown_policy_and_invalid_lifecycle_are_typed(self) -> None:
        plan = configured_plan(detectors=[])
        pipeline = TelemetryPipeline(
            plan,
            "run-1",
            SequenceSubstrate([], []),
            RecordingPublisher(),
        )

        with self.assertRaises(TelemetryPipelineError) as unknown:
            pipeline.sample_once("switch-router@s1", "missing")
        self.assertEqual(unknown.exception.code, "telemetry.policy.unknown")
        with self.assertRaises(TelemetryPipelineError) as lifecycle:
            pipeline.stop()
        self.assertEqual(lifecycle.exception.code, "telemetry.lifecycle.invalid")

    def test_window_has_a_count_bound_and_rejects_backward_time(self) -> None:
        plan = configured_plan(
            detectors=[],
            every="5s",
            window="10s",
        )
        substrate = SequenceSubstrate(
            [{"s1": {"value": index}} for index in range(5)],
            [NOW, NOW, NOW, NOW, NOW - timedelta(seconds=1)],
        )
        pipeline = TelemetryPipeline(
            plan,
            "run-1",
            substrate,
            RecordingPublisher(),
            clock=lambda: NOW,
        )

        event = None
        for _ in range(4):
            event = pipeline.sample_once(
                "switch-router@s1", "tc.queue-occupancy"
            )[0]
        assert event is not None
        self.assertEqual(event.payload["sampleCount"], 3)

        with self.assertRaises(TelemetryPipelineError) as out_of_order:
            pipeline.sample_once("switch-router@s1", "tc.queue-occupancy")
        self.assertEqual(
            out_of_order.exception.code,
            "telemetry.observation.out-of-order",
        )

    def test_scheduled_detector_event_drives_continuous_invocation(self) -> None:
        plan = configured_plan(
            aggregation="latest",
            every="5ms",
            window="20ms",
            detectors=[
                {
                    "type": "threshold",
                    "name": "queue-high",
                    "event": "queue.threshold-exceeded",
                    "path": "queue.depth",
                    "operator": "gte",
                    "value": 1,
                    "cooldown": "0s",
                }
            ],
        )

        class LiveSubstrate(FakeSubstrateRuntime):
            def observe(self, run_id, query):
                observed_at = datetime.now(UTC)
                return ObservationResult(
                    run_id=run_id,
                    query=query,
                    observed_at=observed_at,
                    values={"s1": {"queue": {"depth": 5}}},
                )

        class Invoker:
            def __init__(self) -> None:
                self.calls = 0

            def invoke(self, run_id, agent_id, intent):
                self.calls += 1
                return AgentInvocationResult(
                    invocationId=f"invoke-{self.calls}",
                    runId=run_id,
                    agentId=agent_id,
                    status=InvocationStatus.SUCCEEDED,
                    startedAt=NOW,
                    completedAt=NOW,
                    response=AgentResponse(message="done"),
                )

        substrate = LiveSubstrate(run_id_factory=lambda: "run-1")
        invoker = Invoker()
        runtime = ContinuousAgentRuntime(plan, "run-1", invoker)
        pipeline = TelemetryPipeline(plan, "run-1", substrate, runtime)

        self.assertIsInstance(runtime, RuntimeEventPublisher)
        runtime.start()
        pipeline.start()
        wait_until(lambda: invoker.calls >= 1)
        telemetry_report = pipeline.stop()
        runtime_report = runtime.stop()

        self.assertEqual(pipeline.state, TelemetryPipelineState.STOPPED)
        self.assertGreaterEqual(telemetry_report.detector_events, 1)
        self.assertGreaterEqual(runtime_report.completed, 1)


if __name__ == "__main__":
    unittest.main()
