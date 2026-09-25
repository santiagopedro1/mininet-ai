"""Event-driven scheduling over the bounded one-shot agent runtime."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Condition, Event, Lock, Semaphore, Thread
from typing import Protocol, runtime_checkable
from uuid import uuid4

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.compiler.models import AgentInstance
from mininet_ai.durations import duration_seconds
from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.contracts import (
    AgentLifecycleState,
    AgentLifecycleTransition,
    ContinuousInvocationRecord,
    ContinuousRuntimeIssue,
    ContinuousRuntimeReport,
    IntervalElapsedPayload,
    ManualIntentPayload,
    RuntimeEvent,
    RuntimeEventType,
)
from mininet_ai.runtime.events import InMemoryRuntimeEventBus, RuntimeEventBus
from mininet_ai.runtime.supervision import AgentSupervisor
from mininet_ai.sdk import AgentInvocationResult, InvocationStatus
from mininet_ai.specification.models import EventTrigger, IntervalTrigger


Clock = Callable[[], datetime]
EventIdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _event_id() -> str:
    return f"event-{uuid4()}"


class ContinuousRuntimeError(MininetAIError):
    """The continuous runtime could not safely change lifecycle state."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class ContinuousRuntimeState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"


@runtime_checkable
class AgentInvoker(Protocol):
    """Existing bounded invocation seam reused by continuous scheduling."""

    def invoke(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
    ) -> AgentInvocationResult: ...


@dataclass(frozen=True)
class _WorkItem:
    event: RuntimeEvent
    agent: AgentInstance
    trigger: str
    intent: str
    coalesce_key: tuple[str, str | None]


class ContinuousAgentRuntime:
    """Match runtime events and execute bounded agent invocations."""

    def __init__(
        self,
        plan: DeploymentPlan,
        run_id: str,
        invoker: AgentInvoker,
        *,
        event_bus: RuntimeEventBus | None = None,
        clock: Clock = _utc_now,
        event_id_factory: EventIdFactory = _event_id,
    ) -> None:
        if not run_id:
            raise ValueError("continuous runtime run id cannot be empty")
        bus = event_bus or InMemoryRuntimeEventBus(
            capacity=plan.resource_limits.max_queued_events
        )
        if bus.capacity > plan.resource_limits.max_queued_events:
            raise ContinuousRuntimeError(
                "event bus capacity exceeds the deployment plan limit",
                code="runtime.queue.limit-exceeded",
            )
        self._plan = plan
        self._run_id = run_id
        self._invoker = invoker
        self._bus = bus
        self._clock = clock
        self._event_id_factory = event_id_factory
        self._agents = {agent.id: agent for agent in plan.agents}
        self._queues = {agent.id: deque() for agent in plan.agents}
        self._condition = Condition()
        self._report_lock = Lock()
        self._lifecycle_lock = Lock()
        self._global_slots = Semaphore(
            plan.resource_limits.max_concurrent_invocations
        )
        self._queued = 0
        self._workers_closed = False
        self._drain = True
        self._interval_stop = Event()
        self._dispatcher: Thread | None = None
        self._workers: list[Thread] = []
        self._intervals: list[Thread] = []
        self._last_sequences: dict[str, int] = {}
        self._last_triggered: dict[tuple[str, str], datetime] = {}
        self._state = ContinuousRuntimeState.CREATED
        self._counts = {
            "received": 0,
            "matched": 0,
            "enqueued": 0,
            "coalesced": 0,
            "dropped": 0,
            "rejected": 0,
            "completed": 0,
            "failed": 0,
        }
        self._invocations: list[ContinuousInvocationRecord] = []
        self._issues: list[ContinuousRuntimeIssue] = []
        self._lifecycle: list[AgentLifecycleTransition] = []
        self._supervisor = AgentSupervisor(
            {
                agent.id: agent.execution.restart
                for agent in self._plan.agents
            },
            listener=self._record_transition,
            clock=clock,
        )

    @property
    def state(self) -> ContinuousRuntimeState:
        with self._lifecycle_lock:
            return self._state

    def start(self) -> None:
        """Start dispatch, interval production, and bounded workers once."""

        with self._lifecycle_lock:
            if self._state != ContinuousRuntimeState.CREATED:
                raise ContinuousRuntimeError(
                    f"continuous runtime is already {self._state.value}",
                    code="runtime.lifecycle.invalid",
                )
            self._state = ContinuousRuntimeState.RUNNING
        self._supervisor.start()
        for agent in self._plan.agents:
            for index in range(agent.execution.max_concurrency):
                worker = Thread(
                    target=self._worker,
                    args=(agent.id,),
                    name=f"mininet-ai-{agent.id}-{index}",
                    daemon=True,
                )
                self._workers.append(worker)
                worker.start()
            for trigger in agent.triggers:
                if isinstance(trigger, IntervalTrigger):
                    interval = Thread(
                        target=self._interval,
                        args=(agent, trigger),
                        name=f"mininet-ai-interval-{agent.id}-{trigger.name}",
                        daemon=True,
                    )
                    self._intervals.append(interval)
                    interval.start()
        self._dispatcher = Thread(
            target=self._dispatch_loop,
            name="mininet-ai-dispatcher",
            daemon=True,
        )
        self._dispatcher.start()

    def pause(self, agent_id: str) -> None:
        """Pause new invocations for one supervised agent."""

        self._require_running()
        self._supervisor.pause(agent_id)

    def resume(self, agent_id: str) -> None:
        """Resume queued invocations for one supervised agent."""

        self._require_running()
        self._supervisor.resume(agent_id)

    def agent_state(self, agent_id: str) -> AgentLifecycleState:
        """Return one agent's current supervised lifecycle state."""

        return self._supervisor.state(agent_id)

    def publish(self, event: RuntimeEvent) -> None:
        """Validate and publish one normalized event for this run."""

        if self.state != ContinuousRuntimeState.RUNNING:
            raise ContinuousRuntimeError(
                "continuous runtime is not accepting events",
                code="runtime.lifecycle.not-running",
            )
        if event.run_id != self._run_id:
            raise ContinuousRuntimeError(
                f"event belongs to run {event.run_id!r}, not {self._run_id!r}",
                code="runtime.event.run-mismatch",
            )
        self._bus.publish(event)

    def stop(
        self,
        *,
        drain: bool = True,
        timeout_seconds: float = 30,
    ) -> ContinuousRuntimeReport:
        """Stop producers and consumers, optionally draining accepted work."""

        if timeout_seconds <= 0:
            raise ValueError("continuous runtime stop timeout must be positive")
        with self._lifecycle_lock:
            if self._state == ContinuousRuntimeState.STOPPED:
                return self.report()
            if self._state != ContinuousRuntimeState.RUNNING:
                raise ContinuousRuntimeError(
                    f"cannot stop continuous runtime from {self._state.value}",
                    code="runtime.lifecycle.invalid",
                )
            self._state = ContinuousRuntimeState.STOPPING
            self._drain = drain
        if drain:
            self._supervisor.resume_paused()
        deadline = time.monotonic() + timeout_seconds
        self._interval_stop.set()
        for interval in self._intervals:
            self._join(interval, deadline)
        self._bus.close()
        if self._dispatcher is not None:
            self._join(self._dispatcher, deadline)
        for worker in self._workers:
            self._join(worker, deadline)
        self._supervisor.stop()
        with self._lifecycle_lock:
            self._state = ContinuousRuntimeState.STOPPED
        return self.report()

    def report(self) -> ContinuousRuntimeReport:
        """Return an immutable snapshot of current delivery outcomes."""

        with self._report_lock:
            return ContinuousRuntimeReport(
                **self._counts,
                invocations=tuple(self._invocations),
                issues=tuple(self._issues),
                lifecycle=tuple(self._lifecycle),
            )

    def _require_running(self) -> None:
        if self.state != ContinuousRuntimeState.RUNNING:
            raise ContinuousRuntimeError(
                "continuous runtime is not running",
                code="runtime.lifecycle.not-running",
            )

    def _record_transition(self, transition: AgentLifecycleTransition) -> None:
        with self._report_lock:
            self._lifecycle.append(transition)

    def _join(self, thread: Thread, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            thread.join(remaining)
        if thread.is_alive():
            raise ContinuousRuntimeError(
                f"thread {thread.name!r} did not stop before the deadline",
                code="runtime.stop.timeout",
            )

    def _interval(self, agent: AgentInstance, trigger: IntervalTrigger) -> None:
        delay = duration_seconds(trigger.initial_delay)
        if self._interval_stop.wait(delay):
            return
        every = duration_seconds(trigger.every)
        sequence = 0
        while not self._interval_stop.is_set():
            scheduled_at = self._clock()
            payload = IntervalElapsedPayload(
                agentId=agent.id,
                trigger=trigger.name,
                scheduledAt=scheduled_at,
            )
            event = RuntimeEvent(
                eventId=self._event_id_factory(),
                runId=self._run_id,
                type=RuntimeEventType.INTERVAL_ELAPSED,
                source=f"interval:{agent.id}:{trigger.name}",
                subject=agent.id,
                occurredAt=scheduled_at,
                observedAt=scheduled_at,
                sequence=sequence,
                payload=payload.model_dump(mode="json", by_alias=True),
            )
            try:
                self.publish(event)
            except Exception as error:
                self._issue(
                    "runtime.interval.publish-failed",
                    str(error) or type(error).__name__,
                    event=event,
                    agent_id=agent.id,
                    trigger=trigger.name,
                    rejected=True,
                )
            sequence += 1
            if self._interval_stop.wait(every):
                return

    def _dispatch_loop(self) -> None:
        try:
            while True:
                event = self._bus.receive()
                if event is None:
                    break
                if not self._drain:
                    self._issue(
                        "runtime.queue.dropped-on-stop",
                        "event dropped during non-draining stop",
                        event=event,
                        dropped=True,
                    )
                    continue
                self._dispatch(event)
        finally:
            with self._condition:
                if not self._drain:
                    for queue in self._queues.values():
                        while queue:
                            item = queue.popleft()
                            self._queued -= 1
                            self._issue(
                                "runtime.queue.dropped-on-stop",
                                "queued invocation dropped during stop",
                                event=item.event,
                                agent_id=item.agent.id,
                                trigger=item.trigger,
                                dropped=True,
                            )
                self._workers_closed = True
                self._condition.notify_all()

    def _dispatch(self, event: RuntimeEvent) -> None:
        with self._report_lock:
            self._counts["received"] += 1
        previous = self._last_sequences.get(event.source)
        if previous is not None and event.sequence <= previous:
            self._issue(
                "runtime.event.out-of-order",
                f"source sequence {event.sequence} did not follow {previous}",
                event=event,
                rejected=True,
            )
            return
        self._last_sequences[event.source] = event.sequence
        try:
            matches = self._matches(event)
        except (KeyError, ValueError) as error:
            self._issue(
                "runtime.event.payload-invalid",
                str(error) or type(error).__name__,
                event=event,
                rejected=True,
            )
            return
        with self._report_lock:
            self._counts["matched"] += len(matches)
        for item in matches:
            self._enqueue(item)

    def _matches(self, event: RuntimeEvent) -> tuple[_WorkItem, ...]:
        if event.type == RuntimeEventType.MANUAL_INTENT:
            payload = event.payload_as(ManualIntentPayload)
            agent = self._agents[payload.agent_id]
            trigger = next(
                (item for item in agent.triggers if item.type == "manual"),
                None,
            )
            if trigger is None:
                return ()
            return (
                _WorkItem(
                    event=event,
                    agent=agent,
                    trigger=trigger.name,
                    intent=payload.intent,
                    coalesce_key=(trigger.name, event.subject),
                ),
            )
        if event.type == RuntimeEventType.INTERVAL_ELAPSED:
            payload = event.payload_as(IntervalElapsedPayload)
            agent = self._agents[payload.agent_id]
            trigger = next(
                (
                    item
                    for item in agent.triggers
                    if isinstance(item, IntervalTrigger)
                    and item.name == payload.trigger
                ),
                None,
            )
            if trigger is None:
                return ()
            return (
                _WorkItem(
                    event=event,
                    agent=agent,
                    trigger=trigger.name,
                    intent=(
                        f"Interval trigger {trigger.name!r} elapsed at "
                        f"{payload.scheduled_at.isoformat()}"
                    ),
                    coalesce_key=(trigger.name, event.subject),
                ),
            )
        matches = []
        for agent in self._plan.agents:
            for trigger in agent.triggers:
                if not isinstance(trigger, EventTrigger):
                    continue
                if trigger.event != event.type:
                    continue
                if trigger.source is not None and trigger.source != event.source:
                    continue
                if trigger.subject is not None and trigger.subject != event.subject:
                    continue
                if (
                    trigger.subject is None
                    and event.subject is not None
                    and event.subject
                    not in {
                        agent.id,
                        agent.deployment,
                        *agent.attachment.targets,
                    }
                ):
                    continue
                identity = (agent.id, trigger.name)
                last = self._last_triggered.get(identity)
                cooldown = duration_seconds(trigger.cooldown)
                if last is not None and (
                    event.observed_at - last
                ).total_seconds() < cooldown:
                    self._issue(
                        "runtime.trigger.cooldown",
                        "event arrived during trigger cooldown",
                        event=event,
                        agent_id=agent.id,
                        trigger=trigger.name,
                    )
                    continue
                matches.append(
                    _WorkItem(
                        event=event,
                        agent=agent,
                        trigger=trigger.name,
                        intent=(
                            f"Handle runtime event {event.type!r} from "
                            f"{event.source!r}: "
                            f"{json.dumps(event.payload, sort_keys=True)}"
                        ),
                        coalesce_key=(trigger.name, event.subject),
                    )
                )
        return tuple(matches)

    def _enqueue(self, item: _WorkItem) -> None:
        execution = item.agent.execution
        with self._condition:
            queue = self._queues[item.agent.id]
            if execution.overflow == "coalesce":
                for index, queued in enumerate(queue):
                    if queued.coalesce_key == item.coalesce_key:
                        queue[index] = item
                        with self._report_lock:
                            self._counts["coalesced"] += 1
                        self._last_triggered[
                            (item.agent.id, item.trigger)
                        ] = item.event.observed_at
                        return
            at_agent_limit = len(queue) >= execution.queue_capacity
            at_global_limit = (
                self._queued >= self._plan.resource_limits.max_queued_events
            )
            if at_agent_limit or at_global_limit:
                if execution.overflow == "drop-oldest" and queue:
                    dropped = queue.popleft()
                    self._queued -= 1
                    self._issue(
                        "runtime.queue.drop-oldest",
                        "oldest queued invocation was replaced",
                        event=dropped.event,
                        agent_id=dropped.agent.id,
                        trigger=dropped.trigger,
                        dropped=True,
                    )
                else:
                    self._issue(
                        "runtime.queue.full",
                        "agent invocation queue is full",
                        event=item.event,
                        agent_id=item.agent.id,
                        trigger=item.trigger,
                        rejected=True,
                    )
                    return
            queue.append(item)
            self._queued += 1
            self._last_triggered[(item.agent.id, item.trigger)] = (
                item.event.observed_at
            )
            with self._report_lock:
                self._counts["enqueued"] += 1
            self._condition.notify_all()

    def _worker(self, agent_id: str) -> None:
        while True:
            with self._condition:
                queue = self._queues[agent_id]
                while not queue and not self._workers_closed:
                    self._condition.wait()
                if not queue and self._workers_closed:
                    return
                item = queue.popleft()
                self._queued -= 1
            try:
                result = self._supervisor.execute(
                    item.agent.id,
                    lambda: self._invoke(item),
                )
            except Exception as error:
                self._issue(
                    "runtime.invocation.failed",
                    str(error) or type(error).__name__,
                    event=item.event,
                    agent_id=item.agent.id,
                    trigger=item.trigger,
                )
                with self._report_lock:
                    self._counts["failed"] += 1
                continue
            result = result.model_copy(
                update={
                    "timings": result.timings.model_copy(
                        update={
                            "event_detection_seconds": max(
                                0.0,
                                (
                                    item.event.observed_at
                                    - item.event.occurred_at
                                ).total_seconds(),
                            )
                        }
                    )
                }
            )
            record = ContinuousInvocationRecord(
                eventId=item.event.event_id,
                agentId=item.agent.id,
                trigger=item.trigger,
                result=result,
            )
            with self._report_lock:
                self._invocations.append(record)
                self._counts["completed"] += 1
                if result.status != InvocationStatus.SUCCEEDED:
                    self._counts["failed"] += 1

    def _invoke(self, item: _WorkItem) -> AgentInvocationResult:
        with self._global_slots:
            return self._invoker.invoke(
                self._run_id,
                item.agent.id,
                item.intent,
            )

    def _issue(
        self,
        code: str,
        message: str,
        *,
        event: RuntimeEvent | None = None,
        agent_id: str | None = None,
        trigger: str | None = None,
        dropped: bool = False,
        rejected: bool = False,
    ) -> None:
        issue = ContinuousRuntimeIssue(
            code=code,
            message=message,
            eventId=event.event_id if event is not None else None,
            agentId=agent_id,
            trigger=trigger,
        )
        with self._report_lock:
            self._issues.append(issue)
            if dropped:
                self._counts["dropped"] += 1
            if rejected:
                self._counts["rejected"] += 1
