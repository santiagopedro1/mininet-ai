"""Polling verification for declared capability postconditions."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue, TypeAdapter

from mininet_ai.durations import duration_seconds
from mininet_ai.sdk import ActionProposal, AgentContext
from mininet_ai.specification.models import PostconditionDefinition
from mininet_ai.substrates.runtime import (
    ObservationQuery,
    ObservationResult,
    PostconditionResult,
    RuntimeIssue,
)


Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]
_JSON_VALUE = TypeAdapter(JsonValue)
_MISSING = object()


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PostconditionObserver(Protocol):
    def observe(
        self,
        run_id: str,
        query: ObservationQuery,
    ) -> ObservationResult: ...


@dataclass(frozen=True)
class VerificationReport:
    checks: tuple[PostconditionResult, ...]
    duration_seconds: float

    @property
    def satisfied(self) -> bool:
        return all(check.satisfied for check in self.checks)


class PostconditionVerifier:
    """Poll observations until every postcondition succeeds or expires."""

    def __init__(
        self,
        observer: PostconditionObserver,
        *,
        clock: Clock = _utc_now,
        monotonic_clock: MonotonicClock = time.monotonic,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        self._observer = observer
        self._clock = clock
        self._monotonic = monotonic_clock
        self._sleep = sleeper

    def verify(
        self,
        context: AgentContext,
        proposal: ActionProposal,
        postconditions: tuple[PostconditionDefinition, ...],
    ) -> VerificationReport:
        started = self._monotonic()
        checks = tuple(
            self._verify_one(context, proposal, postcondition)
            for postcondition in postconditions
        )
        return VerificationReport(
            checks=checks,
            duration_seconds=max(0.0, self._monotonic() - started),
        )

    def _verify_one(
        self,
        context: AgentContext,
        proposal: ActionProposal,
        postcondition: PostconditionDefinition,
    ) -> PostconditionResult:
        deadline = self._monotonic() + duration_seconds(postcondition.timeout)
        interval = duration_seconds(postcondition.interval)
        attempts = 0
        actual: JsonValue | None = None
        issue: RuntimeIssue | None = None
        satisfied = False
        while True:
            attempts += 1
            try:
                query = ObservationQuery(
                    name=postcondition.observation,
                    targets=(proposal.target,),
                )
                result = self._observer.observe(
                    context.run_id,
                    query,
                )
                if result.run_id != context.run_id or result.query != query:
                    raise ValueError(
                        "postcondition observation changed request identity"
                    )
                root = result.values.get(proposal.target, _MISSING)
                value = _read_path(root, postcondition.path)
                if value is _MISSING:
                    actual = None
                    issue = RuntimeIssue(
                        code="capability.postcondition.path-missing",
                        message=(
                            f"observation {postcondition.observation!r} has no "
                            f"path {postcondition.path!r} for {proposal.target!r}"
                        ),
                        target=proposal.target,
                    )
                else:
                    actual = _JSON_VALUE.validate_python(value)
                    satisfied = _compare(
                        actual,
                        postcondition.operator,
                        postcondition.expected,
                    )
                    issue = None
            except Exception as error:
                code = getattr(error, "code", None)
                issue = RuntimeIssue(
                    code=(
                        code
                        if isinstance(code, str) and code
                        else "capability.postcondition.observation-failed"
                    ),
                    message=str(error) or type(error).__name__,
                    target=proposal.target,
                )
            now = self._monotonic()
            if satisfied or now >= deadline:
                break
            self._sleep(min(interval, max(0.0, deadline - now)))
        return PostconditionResult(
            observation=postcondition.observation,
            path=postcondition.path,
            operator=postcondition.operator,
            expected=postcondition.expected,
            actual=actual,
            satisfied=satisfied,
            attempts=attempts,
            completedAt=self._clock(),
            issue=issue,
        )


def _read_path(root: object, path: str) -> object:
    value = root
    for part in path.split("."):
        if isinstance(value, Mapping):
            value = value.get(part, _MISSING)
        elif isinstance(value, list) and part.isdigit():
            index = int(part)
            value = value[index] if index < len(value) else _MISSING
        else:
            return _MISSING
        if value is _MISSING:
            return _MISSING
    return value


def _compare(actual: JsonValue, operator: str, expected: JsonValue) -> bool:
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if (
        isinstance(actual, bool)
        or isinstance(expected, bool)
        or not isinstance(actual, (int, float))
        or not isinstance(expected, (int, float))
    ):
        return False
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    return actual <= expected
