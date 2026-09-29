"""Deterministic conflict arbitration for prepared network actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.sdk import ActionProposal, AgentContext, ExecutionCatalog
from mininet_ai.specification.models import StrictModel


ArbitrationPolicy = Literal["reject", "serialize", "priority"]


class ArbitrationDecision(StrictModel):
    """One recorded admission decision for a proposed action."""

    candidate_id: str = Field(alias="candidateId", min_length=1)
    invocation_id: str = Field(alias="invocationId", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    proposal_id: str = Field(alias="proposalId", min_length=1)
    target: str = Field(min_length=1)
    effects: tuple[str, ...] = ()
    conflict_keys: tuple[str, ...] = Field(default=(), alias="conflictKeys")
    priority: int
    admission_sequence: int = Field(alias="admissionSequence", ge=0)
    disposition: Literal["execute", "reject"]
    commit_order: int | None = Field(default=None, alias="commitOrder", ge=0)
    winner_candidate_id: str | None = Field(
        default=None,
        alias="winnerCandidateId",
        min_length=1,
    )


class ArbitrationReport(StrictModel):
    """Deterministic action decisions for one coordination request."""

    policy: ArbitrationPolicy
    decisions: tuple[ArbitrationDecision, ...] = ()


@dataclass(frozen=True)
class ArbitrationCandidate:
    candidate_id: str
    context: AgentContext
    proposal: ActionProposal
    effects: tuple[str, ...]
    conflict_keys: tuple[str, ...]
    admission_sequence: int


@dataclass(frozen=True)
class ArbitrationPlan:
    candidates: tuple[ArbitrationCandidate, ...]
    ordered: tuple[ArbitrationCandidate, ...]
    rejected: frozenset[str]
    report: ArbitrationReport


class ConflictArbitrator:
    """Resolve action conflicts using the policy compiled into a plan."""

    def __init__(self, plan: DeploymentPlan) -> None:
        self._catalog = ExecutionCatalog(plan)
        self._policy: ArbitrationPolicy = plan.policies.conflicting_actions

    def arbitrate(
        self,
        proposals: tuple[tuple[AgentContext, ActionProposal], ...],
    ) -> ArbitrationPlan:
        candidates = tuple(
            self._candidate(context, proposal, sequence)
            for sequence, (context, proposal) in enumerate(proposals)
        )
        if self._policy == "reject":
            return self._reject_conflicts(candidates)
        ordered = (
            candidates
            if self._policy == "serialize"
            else tuple(
                sorted(
                    candidates,
                    key=lambda item: (
                        -item.context.priority,
                        item.admission_sequence,
                        item.context.agent_id,
                        item.proposal.id,
                    ),
                )
            )
        )
        commit_orders = {
            candidate.candidate_id: index
            for index, candidate in enumerate(ordered)
        }
        return ArbitrationPlan(
            candidates=candidates,
            ordered=ordered,
            rejected=frozenset(),
            report=ArbitrationReport(
                policy=self._policy,
                decisions=tuple(
                    self._decision(
                        candidate,
                        disposition="execute",
                        commit_order=commit_orders[candidate.candidate_id],
                    )
                    for candidate in candidates
                ),
            ),
        )

    def _reject_conflicts(
        self,
        candidates: tuple[ArbitrationCandidate, ...],
    ) -> ArbitrationPlan:
        winners: list[ArbitrationCandidate] = []
        rejected: set[str] = set()
        decisions: list[ArbitrationDecision] = []
        for candidate in candidates:
            winner = next(
                (
                    accepted
                    for accepted in winners
                    if self._conflicts(accepted, candidate)
                ),
                None,
            )
            if winner is not None:
                rejected.add(candidate.candidate_id)
                decisions.append(
                    self._decision(
                        candidate,
                        disposition="reject",
                        winner_candidate_id=winner.candidate_id,
                    )
                )
                continue
            decisions.append(
                self._decision(
                    candidate,
                    disposition="execute",
                    commit_order=len(winners),
                )
            )
            winners.append(candidate)
        return ArbitrationPlan(
            candidates=candidates,
            ordered=tuple(winners),
            rejected=frozenset(rejected),
            report=ArbitrationReport(
                policy=self._policy,
                decisions=tuple(decisions),
            ),
        )

    def _candidate(
        self,
        context: AgentContext,
        proposal: ActionProposal,
        sequence: int,
    ) -> ArbitrationCandidate:
        definition = self._catalog.resolve(context.agent_id)
        effects = next(
            (
                tuple(sorted(capability.effects))
                for capability in definition.capabilities
                if capability.metadata.name == proposal.capability
            ),
            (),
        )
        return ArbitrationCandidate(
            candidate_id=f"{context.invocation_id}:{proposal.id}",
            context=context,
            proposal=proposal,
            effects=effects,
            conflict_keys=tuple(
                f"{proposal.target}:{effect}" for effect in effects
            ),
            admission_sequence=sequence,
        )

    @staticmethod
    def _conflicts(
        left: ArbitrationCandidate,
        right: ArbitrationCandidate,
    ) -> bool:
        return bool(set(left.conflict_keys) & set(right.conflict_keys))

    @staticmethod
    def _decision(
        candidate: ArbitrationCandidate,
        *,
        disposition: Literal["execute", "reject"],
        commit_order: int | None = None,
        winner_candidate_id: str | None = None,
    ) -> ArbitrationDecision:
        return ArbitrationDecision(
            candidateId=candidate.candidate_id,
            invocationId=candidate.context.invocation_id,
            agentId=candidate.context.agent_id,
            proposalId=candidate.proposal.id,
            target=candidate.proposal.target,
            effects=candidate.effects,
            conflictKeys=candidate.conflict_keys,
            priority=candidate.context.priority,
            admissionSequence=candidate.admission_sequence,
            disposition=disposition,
            commitOrder=commit_order,
            winnerCandidateId=winner_candidate_id,
        )
