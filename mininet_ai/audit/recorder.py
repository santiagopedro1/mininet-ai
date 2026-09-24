"""Correlation and error handling for audit sinks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from pydantic import JsonValue, ValidationError

from mininet_ai.audit.contracts import AuditEvent, AuditEventType
from mininet_ai.audit.sinks import AuditSink
from mininet_ai.errors import AuditError
from mininet_ai.sdk import AgentContext


Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditRecorder:
    """Create correlated events and synchronously send them to one sink."""

    def __init__(self, sink: AuditSink, *, clock: Clock = _utc_now) -> None:
        self._sink = sink
        self._clock = clock

    def record(
        self,
        event_type: AuditEventType,
        context: AgentContext,
        data: Mapping[str, JsonValue] | None = None,
    ) -> AuditEvent:
        try:
            event = AuditEvent(
                recordedAt=self._clock(),
                type=event_type,
                runId=context.run_id,
                invocationId=context.invocation_id,
                agentId=context.agent_id,
                data=dict(data or {}),
            )
        except ValidationError as error:
            raise AuditError(
                f"could not construct audit event: {error}",
                code="audit.event.invalid",
            ) from error
        try:
            self._sink.write(event)
        except AuditError:
            raise
        except Exception as error:
            raise AuditError(
                f"audit sink failed: {error}",
                code="audit.write.failed",
            ) from error
        return event
