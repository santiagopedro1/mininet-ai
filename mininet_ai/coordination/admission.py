"""Experiment-wide action admission, without locking observations or reasoning."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition

from mininet_ai.coordination.arbitration import (
    ArbitrationCandidate,
    ArbitrationPolicy,
    ConflictArbitrator,
)
from mininet_ai.sdk import AgentContext, AgentResponse

StateKey = tuple[str, str, str | None, str]


@dataclass(eq=False)
class _CommitReservation:
    reads: set[StateKey]
    writes: set[StateKey]
    candidates: tuple[ArbitrationCandidate, ...]
    priority: int


class ActionAdmission:
    """Protect effects for the entire execution; priority orders only waiters."""

    def __init__(self, policy: ArbitrationPolicy) -> None:
        self._policy = policy
        self._condition = Condition()
        self._active: list[ArbitrationCandidate] = []
        self._pending: list[ArbitrationCandidate] = []
        self._committing: list[_CommitReservation] = []
        self._waiting: list[_CommitReservation] = []

    def rejections(
        self,
        candidates: tuple[ArbitrationCandidate, ...],
        history: list[ArbitrationCandidate],
    ) -> dict[str, ArbitrationCandidate]:
        """Reject already-admitted network conflicts before waiting on state."""
        if self._policy != "reject":
            return {}
        with self._condition:
            admitted = (
                *history,
                *self._active,
                *(
                    candidate
                    for reservation in (*self._committing, *self._waiting)
                    for candidate in reservation.candidates
                ),
            )
            return {
                candidate.candidate_id: winner
                for candidate in candidates
                if (
                    winner := next(
                        (
                            other
                            for other in admitted
                            if ConflictArbitrator.conflicts(other, candidate)
                        ),
                        None,
                    )
                )
                is not None
            }

    @contextmanager
    def decision(
        self,
        context: AgentContext,
        response: AgentResponse,
        candidates: tuple[ArbitrationCandidate, ...],
        history: list[ArbitrationCandidate],
    ) -> Iterator[dict[str, ArbitrationCandidate]]:
        def key(scope: str, name: str) -> StateKey:
            return (
                scope,
                context.run_id,
                context.deployment if scope == "deployment" else None,
                name,
            )

        reservation = _CommitReservation(
            reads={key(r.scope, r.key) for r in response.shared_state_reads},
            writes={key(u.scope, u.key) for u in response.shared_state_updates},
            candidates=candidates,
            priority=context.priority,
        )
        with self._condition:
            rejections = self.rejections(candidates, history)
            reservation.candidates = tuple(
                c for c in candidates if c.candidate_id not in rejections
            )
            if response.proposals and not reservation.candidates:
                reservation.reads.clear()
                reservation.writes.clear()
            self._waiting.append(reservation)
            self._condition.wait_for(lambda: self._decision_ready(reservation))
            self._waiting.remove(reservation)
            self._committing.append(reservation)
        try:
            yield rejections
        finally:
            with self._condition:
                self._committing.remove(reservation)
                self._condition.notify_all()

    def _decision_conflicts(
        self, left: _CommitReservation, right: _CommitReservation
    ) -> bool:
        state_conflict = bool(
            left.writes & (right.reads | right.writes) or right.writes & left.reads
        )
        network_conflict = self._policy != "reject" and any(
            ConflictArbitrator.conflicts(a, b)
            for a in left.candidates
            for b in right.candidates
        )
        return state_conflict or network_conflict

    def _decision_ready(self, reservation: _CommitReservation) -> bool:
        if any(
            self._decision_conflicts(reservation, active) for active in self._committing
        ):
            return False
        index = self._waiting.index(reservation)
        for other_index, other in enumerate(self._waiting):
            if other is reservation or not self._decision_conflicts(reservation, other):
                continue
            if self._policy == "priority":
                if (-other.priority, other_index) < (-reservation.priority, index):
                    return False
            elif other_index < index:
                return False
        return True

    @contextmanager
    def admit(
        self, candidate: ArbitrationCandidate, history: list[ArbitrationCandidate]
    ) -> Iterator[ArbitrationCandidate | None]:
        winner = None
        with self._condition:
            if self._policy == "reject":
                winner = next(
                    (
                        other
                        for other in (*history, *self._active)
                        if ConflictArbitrator.conflicts(other, candidate)
                    ),
                    None,
                )
            if winner is None:
                self._pending.append(candidate)
                self._condition.wait_for(lambda: self._ready(candidate))
                self._pending.remove(candidate)
                self._active.append(candidate)
                history.append(candidate)
        try:
            yield winner
        finally:
            if winner is None:
                with self._condition:
                    self._active.remove(candidate)
                    self._condition.notify_all()

    def _ready(self, candidate: ArbitrationCandidate) -> bool:
        if any(
            ConflictArbitrator.conflicts(other, candidate) for other in self._active
        ):
            return False
        for other in self._pending:
            if other is candidate:
                continue
            if not ConflictArbitrator.conflicts(other, candidate):
                continue
            if self._policy == "priority":
                if (-other.context.priority, self._pending.index(other)) < (
                    -candidate.context.priority,
                    self._pending.index(candidate),
                ):
                    return False
            elif self._pending.index(other) < self._pending.index(candidate):
                return False
        return True
