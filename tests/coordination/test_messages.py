from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from threading import Event, Thread

from pydantic import ValidationError
from typer.testing import CliRunner

from mininet_ai.cli import app
from mininet_ai.coordination import (
    COORDINATION_MESSAGE_CONTRACT_VERSION,
    COORDINATION_MESSAGE_SCHEMA_ID,
    COORDINATION_OUTCOME_CONTRACT_VERSION,
    COORDINATION_OUTCOME_SCHEMA_ID,
    CoordinationMessage,
    CoordinationMessageKind,
    InMemoryMessageChannel,
    MessageChannel,
    MessageChannelError,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def message(
    message_id: str = "message-1",
    *,
    kind: CoordinationMessageKind = CoordinationMessageKind.INTENT,
    source_agent_id: str | None = None,
    target_agent_id: str = "global-router",
    causation_id: str | None = None,
    traversed_message_ids: tuple[str, ...] = (),
) -> CoordinationMessage:
    return CoordinationMessage(
        messageId=message_id,
        runId="run-1",
        kind=kind,
        sourceAgentId=source_agent_id,
        targetAgentId=target_agent_id,
        correlationId="request-1",
        causationId=causation_id,
        triggeringEventId="event-1",
        createdAt=NOW,
        hopCount=len(traversed_message_ids),
        traversedMessageIds=traversed_message_ids,
        payload={"intent": "Inspect the queue"},
    )


class CoordinationMessageContractTests(unittest.TestCase):
    def test_message_round_trips_as_versioned_immutable_json(self) -> None:
        original = message()

        restored = CoordinationMessage.model_validate_json(
            original.model_dump_json(by_alias=True)
        )

        self.assertEqual(restored, original)
        self.assertEqual(
            restored.contract_version,
            COORDINATION_MESSAGE_CONTRACT_VERSION,
        )
        with self.assertRaises(ValidationError):
            restored.target_agent_id = "switch-router@s1"

    def test_delegation_requires_source_and_consistent_traversal(self) -> None:
        with self.assertRaisesRegex(ValidationError, "require sourceAgentId"):
            message(kind=CoordinationMessageKind.DELEGATION)

        with self.assertRaisesRegex(ValidationError, "require causationId"):
            message(
                kind=CoordinationMessageKind.DELEGATION,
                source_agent_id="global-router",
                target_agent_id="switch-router@s1",
                traversed_message_ids=("message-0",),
            )

        delegated = message(
            kind=CoordinationMessageKind.DELEGATION,
            source_agent_id="global-router",
            target_agent_id="switch-router@s1",
            causation_id="message-0",
            traversed_message_ids=("message-0",),
        )
        self.assertEqual(delegated.hop_count, 1)

    def test_message_rejects_repeated_or_self_referential_history(self) -> None:
        for history, expected in (
            (("message-0", "message-0"), "must be unique"),
            (("message-1",), "cannot appear in its own"),
        ):
            with self.subTest(history=history):
                with self.assertRaisesRegex(ValidationError, expected):
                    message(
                        causation_id="message-0",
                        traversed_message_ids=history,
                    )

    def test_cli_emits_versioned_coordination_message_schema(self) -> None:
        result = CliRunner().invoke(app, ["schema", "coordination-message"])

        self.assertEqual(result.exit_code, 0, result.output)
        schema = json.loads(result.output)
        self.assertEqual(schema["$id"], COORDINATION_MESSAGE_SCHEMA_ID)
        self.assertEqual(
            schema["properties"]["contractVersion"]["const"],
            COORDINATION_MESSAGE_CONTRACT_VERSION,
        )

    def test_cli_emits_versioned_coordination_outcome_schema(self) -> None:
        result = CliRunner().invoke(app, ["schema", "coordination-outcome"])

        self.assertEqual(result.exit_code, 0, result.output)
        schema = json.loads(result.output)
        self.assertEqual(schema["$id"], COORDINATION_OUTCOME_SCHEMA_ID)
        self.assertEqual(
            schema["properties"]["contractVersion"]["const"],
            COORDINATION_OUTCOME_CONTRACT_VERSION,
        )


class InMemoryMessageChannelTests(unittest.TestCase):
    def test_channel_delivers_messages_in_fifo_order(self) -> None:
        channel = InMemoryMessageChannel(capacity=2)
        first = message("message-1")
        second = message("message-2")

        self.assertIsInstance(channel, MessageChannel)
        channel.send(first)
        channel.send(second)

        self.assertEqual(channel.capacity, 2)
        self.assertEqual(channel.size, 2)
        self.assertEqual(channel.receive(timeout_seconds=0), first)
        self.assertEqual(channel.receive(timeout_seconds=0), second)
        self.assertIsNone(channel.receive(timeout_seconds=0))

    def test_duplicate_is_rejected_after_original_was_received(self) -> None:
        channel = InMemoryMessageChannel(capacity=1)
        original = message()
        channel.send(original)
        self.assertEqual(channel.receive(timeout_seconds=0), original)

        with self.assertRaises(MessageChannelError) as raised:
            channel.send(original)

        self.assertEqual(
            raised.exception.code,
            "coordination.message-channel.duplicate",
        )
        self.assertEqual(raised.exception.message_id, original.message_id)

    def test_full_and_closed_channel_return_distinct_errors(self) -> None:
        channel = InMemoryMessageChannel(capacity=1)
        channel.send(message("message-1"))

        with self.assertRaises(MessageChannelError) as full:
            channel.send(message("message-2"))
        self.assertEqual(full.exception.code, "coordination.message-channel.full")

        channel.close()
        accepted = channel.receive(timeout_seconds=0)
        self.assertIsNotNone(accepted)
        assert accepted is not None
        self.assertEqual(accepted.message_id, "message-1")
        self.assertIsNone(channel.receive(timeout_seconds=0))
        with self.assertRaises(MessageChannelError) as closed:
            channel.send(message("message-3"))
        self.assertEqual(
            closed.exception.code,
            "coordination.message-channel.closed",
        )

    def test_sink_failure_does_not_accept_message(self) -> None:
        attempts = 0

        class FailingSink:
            def write(self, message: CoordinationMessage) -> None:
                nonlocal attempts
                del message
                attempts += 1
                if attempts == 1:
                    raise OSError("ledger unavailable")

        channel = InMemoryMessageChannel(capacity=1, sink=FailingSink())
        candidate = message()

        with self.assertRaisesRegex(OSError, "ledger unavailable"):
            channel.send(candidate)
        self.assertEqual(channel.size, 0)

        channel.send(candidate)
        self.assertEqual(channel.receive(timeout_seconds=0), candidate)

    def test_close_wakes_blocked_receiver(self) -> None:
        channel = InMemoryMessageChannel(capacity=1)
        entered = Event()
        received: list[CoordinationMessage | None] = []

        def consume() -> None:
            entered.set()
            received.append(channel.receive())

        consumer = Thread(target=consume)
        consumer.start()
        self.assertTrue(entered.wait(timeout=1))

        channel.close()
        consumer.join(timeout=1)

        self.assertFalse(consumer.is_alive())
        self.assertEqual(received, [None])


if __name__ == "__main__":
    unittest.main()
