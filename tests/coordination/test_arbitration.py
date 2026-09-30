from __future__ import annotations

import unittest
from datetime import UTC, datetime

from mininet_ai.compiler import compile_experiment
from mininet_ai.coordination import ConflictArbitrator
from mininet_ai.sdk import ActionProposal, AgentContext, ExecutionCatalog
from tests.compiler.helpers import example_snapshot, experiment_from

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def configured_plan(policy: str):
    snapshot = example_snapshot()
    snapshot["policies"]["conflicting-actions"] = policy
    return compile_experiment(experiment_from(snapshot))


def context(plan, agent_id: str, invocation_id: str, priority: int) -> AgentContext:
    definition = ExecutionCatalog(plan).resolve(agent_id)
    attachment = definition.instance.attachment
    return AgentContext(
        invocationId=invocation_id,
        runId="run-1",
        agentId=agent_id,
        deployment=definition.instance.deployment,
        layer=attachment.layer,
        customLayer=attachment.custom_layer,
        targetKind=attachment.target_kind,
        targets=attachment.targets,
        capabilities=definition.instance.capabilities,
        intent="repair forwarding",
        priority=priority,
        invokedAt=NOW,
    )


def proposal(proposal_id: str, target: str = "s1") -> ActionProposal:
    return ActionProposal(
        id=proposal_id,
        capability="openflow.flow.install",
        target=target,
        arguments={"match": "ip", "actions": "normal"},
    )


class ConflictArbitratorTests(unittest.TestCase):
    def candidates(self, policy: str):
        plan = configured_plan(policy)
        return plan, (
            (
                context(plan, "switch-router@s1", "invoke-1", 1),
                proposal("proposal-1"),
            ),
            (
                context(plan, "switch-router@s2", "invoke-2", 10),
                proposal("proposal-2"),
            ),
        )

    def test_reject_policy_keeps_first_admitted_conflicting_action(self) -> None:
        plan, candidates = self.candidates("reject")

        result = ConflictArbitrator(plan).arbitrate(candidates)

        self.assertEqual(
            [candidate.candidate_id for candidate in result.ordered],
            ["invoke-1:proposal-1"],
        )
        self.assertEqual(result.rejected, {"invoke-2:proposal-2"})
        self.assertTrue(result.report.decisions[0].conflict_keys)
        self.assertEqual(
            result.report.decisions[1].winner_candidate_id,
            "invoke-1:proposal-1",
        )

    def test_serialize_policy_preserves_admission_order(self) -> None:
        plan, candidates = self.candidates("serialize")

        result = ConflictArbitrator(plan).arbitrate(candidates)

        self.assertEqual(
            [candidate.candidate_id for candidate in result.ordered],
            ["invoke-1:proposal-1", "invoke-2:proposal-2"],
        )
        self.assertEqual(
            [decision.commit_order for decision in result.report.decisions],
            [0, 1],
        )

    def test_priority_policy_orders_highest_priority_pending_action_first(
        self,
    ) -> None:
        plan, candidates = self.candidates("priority")

        result = ConflictArbitrator(plan).arbitrate(candidates)

        self.assertEqual(
            [candidate.candidate_id for candidate in result.ordered],
            ["invoke-2:proposal-2", "invoke-1:proposal-1"],
        )
        decisions = {
            decision.candidate_id: decision.commit_order
            for decision in result.report.decisions
        }
        self.assertEqual(decisions["invoke-2:proposal-2"], 0)
        self.assertEqual(decisions["invoke-1:proposal-1"], 1)

    def test_different_targets_do_not_conflict(self) -> None:
        plan = configured_plan("reject")
        candidates = (
            (
                context(plan, "switch-router@s1", "invoke-1", 0),
                proposal("proposal-1", "s1"),
            ),
            (
                context(plan, "switch-router@s2", "invoke-2", 0),
                proposal("proposal-2", "s2"),
            ),
        )

        result = ConflictArbitrator(plan).arbitrate(candidates)

        self.assertEqual(len(result.ordered), 2)
        self.assertEqual(result.rejected, frozenset())


if __name__ == "__main__":
    unittest.main()
