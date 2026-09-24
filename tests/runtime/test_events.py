from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import cast

from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.runtime import (
    AgentLifecyclePayload,
    AgentLifecycleState,
    EventBusError,
    InMemoryRuntimeEventBus,
    IntervalElapsedPayload,
    ObservationRecordedPayload,
    RUNTIME_EVENT_CONTRACT_VERSION,
    RUNTIME_EVENT_SCHEMA_ID,
    ManualIntentPayload,
    RuntimeFailurePayload,
    RuntimeEvent,
    RuntimeEventBus,
    RuntimeEventType,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def manual_event(*, event_id: str = "event-1", sequence: int = 1) -> RuntimeEvent:
    payload = ManualIntentPayload(
        agentId="switch-router@s1",
        intent="Inspect the queue",
    )
    return RuntimeEvent(
        eventId=event_id,
        runId="run-1",
        type=RuntimeEventType.MANUAL_INTENT,
        source="operator",
        subject="switch-router@s1",
        occurredAt=NOW,
        observedAt=NOW + timedelta(milliseconds=2),
        sequence=sequence,
        correlationId="request-1",
        payload=payload.model_dump(mode="json", by_alias=True),
    )


class RuntimeEventContractTests(unittest.TestCase):
    def test_manual_intent_round_trips_as_a_versioned_immutable_event(self) -> None:
        event = manual_event()

        restored = RuntimeEvent.model_validate_json(
            event.model_dump_json(by_alias=True)
        )

        self.assertEqual(
            restored.contract_version,
            RUNTIME_EVENT_CONTRACT_VERSION,
        )
        self.assertEqual(restored.type, "intent.manual")
        self.assertEqual(
            restored.payload_as(ManualIntentPayload).intent,
            "Inspect the queue",
        )
        with self.assertRaises(ValidationError):
            restored.subject = "another-agent"

    def test_event_cannot_be_observed_before_it_occurs(self) -> None:
        payload = manual_event().model_dump(mode="json", by_alias=True)
        payload["observedAt"] = (NOW - timedelta(seconds=1)).isoformat()

        with self.assertRaisesRegex(
            ValidationError,
            "observedAt cannot precede occurredAt",
        ):
            RuntimeEvent.model_validate(payload)

    def test_standard_runtime_payloads_are_typed_and_json_serializable(self) -> None:
        payloads = (
            IntervalElapsedPayload(
                agentId="switch-router@s1",
                trigger="periodic-health",
                scheduledAt=NOW,
            ),
            ObservationRecordedPayload(
                observation="tc.queue-occupancy",
                targets=("s1",),
                values={"s1": {"depth": 80}},
                windowStartedAt=NOW - timedelta(seconds=5),
                windowEndedAt=NOW,
            ),
            AgentLifecyclePayload(
                agentId="switch-router@s1",
                state=AgentLifecycleState.RUNNING,
                previousState=AgentLifecycleState.STARTING,
                attempt=0,
            ),
            RuntimeFailurePayload(
                code="runtime.worker.failed",
                message="worker stopped unexpectedly",
                agentId="switch-router@s1",
                recoverable=True,
            ),
        )

        restored = tuple(
            type(payload).model_validate_json(payload.model_dump_json(by_alias=True))
            for payload in payloads
        )

        self.assertEqual(restored, payloads)
        lifecycle = cast(AgentLifecyclePayload, restored[2])
        self.assertEqual(lifecycle.state, AgentLifecycleState.RUNNING)

    def test_cli_emits_the_versioned_runtime_event_schema(self) -> None:
        result = CliRunner().invoke(app, ["schema", "runtime-event"])

        self.assertEqual(result.exit_code, 0, result.output)
        schema = json.loads(result.output)
        self.assertEqual(schema["$id"], RUNTIME_EVENT_SCHEMA_ID)
        self.assertEqual(
            schema["properties"]["contractVersion"]["const"],
            RUNTIME_EVENT_CONTRACT_VERSION,
        )


class RuntimeEventBusTests(unittest.TestCase):
    def test_bus_delivers_events_in_fifo_order_through_public_interface(self) -> None:
        bus = InMemoryRuntimeEventBus(capacity=2)
        first = manual_event(event_id="event-1", sequence=1)
        second = manual_event(event_id="event-2", sequence=2)

        self.assertIsInstance(bus, RuntimeEventBus)
        bus.publish(first)
        bus.publish(second)

        self.assertEqual(bus.size, 2)
        self.assertEqual(bus.receive(timeout_seconds=0), first)
        self.assertEqual(bus.receive(timeout_seconds=0), second)
        self.assertIsNone(bus.receive(timeout_seconds=0))

    def test_bus_rejects_overflow_and_drains_before_closed(self) -> None:
        bus = InMemoryRuntimeEventBus(capacity=1)
        event = manual_event()
        bus.publish(event)

        with self.assertRaises(EventBusError) as full:
            bus.publish(manual_event(event_id="event-2", sequence=2))
        self.assertEqual(full.exception.code, "runtime.event-bus.full")

        bus.close()
        bus.close()
        self.assertEqual(bus.receive(timeout_seconds=0), event)
        self.assertIsNone(bus.receive(timeout_seconds=0))
        with self.assertRaises(EventBusError) as closed:
            bus.publish(manual_event(event_id="event-3", sequence=3))
        self.assertEqual(closed.exception.code, "runtime.event-bus.closed")

    def test_close_wakes_a_blocked_consumer(self) -> None:
        bus = InMemoryRuntimeEventBus(capacity=1)
        entered = Event()
        received: list[RuntimeEvent | None] = []

        def consume() -> None:
            entered.set()
            received.append(bus.receive())

        consumer = Thread(target=consume)
        consumer.start()
        self.assertTrue(entered.wait(timeout=1))

        bus.close()
        consumer.join(timeout=1)

        self.assertFalse(consumer.is_alive())
        self.assertEqual(received, [None])


if __name__ == "__main__":
    unittest.main()
