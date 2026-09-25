"""Agent lifecycle supervision and bounded failure recovery."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import Condition

from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.contracts import (
    AgentLifecycleState,
    AgentLifecycleTransition,
)
from mininet_ai.sdk import AgentInvocationResult, InvocationStatus
from mininet_ai.specification.models import RestartConfiguration


Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]
TransitionListener = Callable[[AgentLifecycleTransition], None]
Invocation = Callable[[], AgentInvocationResult]
_DURATION_FACTORS = {"us": 0.000001, "ms": 0.001, "s": 1.0}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _duration_seconds(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(us|ms|s)", value)
    if match is None:
        raise ValueError(f"invalid duration {value!r}")
    return float(match.group(1)) * _DURATION_FACTORS[match.group(2)]


class AgentSupervisionError(MininetAIError):
    """An agent cannot perform the requested lifecycle operation."""

    def __init__(self, message: str, *, code: str, agent_id: str) -> None:
        super().__init__(message)
        self.code = code
        self.agent_id = agent_id


class AgentSupervisor:
    """Own agent states and retry failed invocations under declared policy."""

    def __init__(
        self,
        policies: Mapping[str, RestartConfiguration],
        *,
        listener: TransitionListener | None = None,
        clock: Clock = _utc_now,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self._policies = dict(policies)
        self._states: dict[str, AgentLifecycleState | None] = {
            agent_id: None for agent_id in policies
        }
        self._attempts = dict.fromkeys(policies, 0)
        self._listener = listener
        self._clock = clock
        self._sleeper = sleeper
        self._condition = Condition()

    def start(self) -> None:
        """Move every configured agent through starting into running."""

        for agent_id in self._policies:
            self._transition(agent_id, AgentLifecycleState.STARTING)
            self._transition(agent_id, AgentLifecycleState.RUNNING)

    def pause(self, agent_id: str) -> None:
        """Prevent new invocations from starting for one running agent."""

        self._transition_from(
            agent_id,
            AgentLifecycleState.RUNNING,
            AgentLifecycleState.PAUSED,
            "pause",
        )

    def resume(self, agent_id: str) -> None:
        """Allow queued invocations to proceed for one paused agent."""

        self._transition_from(
            agent_id,
            AgentLifecycleState.PAUSED,
            AgentLifecycleState.RUNNING,
            "resume",
        )

    def resume_paused(self) -> None:
        """Release paused workers so a draining shutdown can complete."""

        with self._condition:
            paused = tuple(
                agent_id
                for agent_id, state in self._states.items()
                if state == AgentLifecycleState.PAUSED
            )
        for agent_id in paused:
            self.resume(agent_id)

    def stop(self) -> None:
        """Move every non-stopped agent through stopping into stopped."""

        with self._condition:
            active = tuple(
                agent_id
                for agent_id, state in self._states.items()
                if state != AgentLifecycleState.STOPPED
            )
        for agent_id in active:
            self._transition(agent_id, AgentLifecycleState.STOPPING)
            self._transition(agent_id, AgentLifecycleState.STOPPED)

    def state(self, agent_id: str) -> AgentLifecycleState:
        """Return the current supervised state for one known agent."""

        with self._condition:
            state = self._known_state(agent_id)
            if state is None:
                raise AgentSupervisionError(
                    f"agent {agent_id!r} has not started",
                    code="runtime.agent.not-started",
                    agent_id=agent_id,
                )
            return state

    def execute(self, agent_id: str, invocation: Invocation) -> AgentInvocationResult:
        """Run an invocation, retrying failures within the restart budget."""

        while True:
            self._await_running(agent_id)
            try:
                result = invocation()
            except Exception:
                if self._recover(agent_id):
                    continue
                raise
            if result.status == InvocationStatus.FAILED:
                if self._recover(agent_id):
                    continue
                return result
            with self._condition:
                if self._states[agent_id] == AgentLifecycleState.RUNNING:
                    self._attempts[agent_id] = 0
            return result

    def _recover(self, agent_id: str) -> bool:
        policy = self._policies[agent_id]
        with self._condition:
            while self._states[agent_id] == AgentLifecycleState.RESTARTING:
                self._condition.wait()
            if self._states[agent_id] != AgentLifecycleState.RUNNING:
                return False
            attempt = self._attempts[agent_id] + 1
            self._attempts[agent_id] = attempt
            transitions = [
                self._set_state_locked(
                    agent_id,
                    AgentLifecycleState.FAILED,
                    attempt=attempt,
                )
            ]
            should_restart = (
                policy.policy == "on-failure"
                and attempt <= policy.max_attempts
            )
            if should_restart:
                transitions.append(
                    self._set_state_locked(
                        agent_id,
                        AgentLifecycleState.RESTARTING,
                        attempt=attempt,
                    )
                )
            self._condition.notify_all()
        self._emit(transitions)
        if not should_restart:
            return False
        self._sleeper(_duration_seconds(policy.backoff))
        self._transition(
            agent_id,
            AgentLifecycleState.RUNNING,
            attempt=attempt,
        )
        return True

    def _await_running(self, agent_id: str) -> None:
        with self._condition:
            state = self._known_state(agent_id)
            while state in {
                AgentLifecycleState.STARTING,
                AgentLifecycleState.PAUSED,
                AgentLifecycleState.RESTARTING,
            }:
                self._condition.wait()
                state = self._known_state(agent_id)
            if state != AgentLifecycleState.RUNNING:
                value = state.value if state is not None else "not-started"
                raise AgentSupervisionError(
                    f"agent {agent_id!r} cannot execute while {value}",
                    code="runtime.agent.not-running",
                    agent_id=agent_id,
                )

    def _transition_from(
        self,
        agent_id: str,
        required: AgentLifecycleState,
        state: AgentLifecycleState,
        operation: str,
    ) -> None:
        with self._condition:
            current = self._known_state(agent_id)
            if current != required:
                value = current.value if current is not None else "not-started"
                raise AgentSupervisionError(
                    f"cannot {operation} agent {agent_id!r} while {value}",
                    code="runtime.agent.lifecycle.invalid",
                    agent_id=agent_id,
                )
            transition = self._set_state_locked(agent_id, state)
            self._condition.notify_all()
        self._emit((transition,))

    def _known_state(self, agent_id: str) -> AgentLifecycleState | None:
        try:
            return self._states[agent_id]
        except KeyError as error:
            raise AgentSupervisionError(
                f"unknown agent {agent_id!r}",
                code="runtime.agent.unknown",
                agent_id=agent_id,
            ) from error

    def _transition(
        self,
        agent_id: str,
        state: AgentLifecycleState,
        *,
        attempt: int = 0,
    ) -> None:
        with self._condition:
            transition = self._set_state_locked(
                agent_id,
                state,
                attempt=attempt,
            )
            self._condition.notify_all()
        self._emit((transition,))

    def _set_state_locked(
        self,
        agent_id: str,
        state: AgentLifecycleState,
        *,
        attempt: int = 0,
    ) -> AgentLifecycleTransition:
        previous = self._known_state(agent_id)
        self._states[agent_id] = state
        return AgentLifecycleTransition(
            agentId=agent_id,
            state=state,
            previousState=previous,
            attempt=attempt,
            recordedAt=self._clock(),
        )

    def _emit(
        self,
        transitions: (
            tuple[AgentLifecycleTransition, ...]
            | list[AgentLifecycleTransition]
        ),
    ) -> None:
        if self._listener is not None:
            for transition in transitions:
                self._listener(transition)
