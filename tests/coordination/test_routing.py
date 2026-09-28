from __future__ import annotations

import unittest
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from mininet_ai.compiler import compile_experiment
from mininet_ai.coordination import (
    CoordinationRequest,
    CoordinationRuntime,
    InMemoryMessageChannel,
)
from mininet_ai.sdk import (
    AgentCoordinationContext,
    AgentInvocationResult,
    AgentResponse,
    DelegationProposal,
    InvocationStatus,
)
from tests.compiler.helpers import example_snapshot, experiment_from


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
ResponseFactory = Callable[[str, AgentCoordinationContext], AgentResponse]


def plan_for(configuration: dict[str, object]):
    snapshot = example_snapshot()
    snapshot["coordination"] = configuration
    return compile_experiment(experiment_from(snapshot))


def ids(*values: str) -> Callable[[], str]:
    remaining: Iterator[str] = iter(values)
    return lambda: next(remaining)


class RecordingInvoker:
    def __init__(
        self,
        response: ResponseFactory | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, str, AgentCoordinationContext]] = []
        self._response = response or (
            lambda agent_id, context: AgentResponse(message=f"done:{agent_id}")
        )

    def invoke(
        self,
        run_id: str,
        agent_id: str,
        intent: str,
        *,
        coordination: AgentCoordinationContext | None = None,
    ) -> AgentInvocationResult:
        if coordination is None:
            raise AssertionError("coordinated invocation requires context")
        self.calls.append((run_id, agent_id, intent, coordination))
        response = self._response(agent_id, coordination)
        return AgentInvocationResult(
            invocationId=f"invoke-{len(self.calls)}",
            runId=run_id,
            agentId=agent_id,
            status=InvocationStatus.SUCCEEDED,
            startedAt=NOW,
            completedAt=NOW,
            response=response,
        )


def request(agent_id: str = "switch-router@s1") -> CoordinationRequest:
    return CoordinationRequest(
        runId="run-1",
        requestedAgentId=agent_id,
        intent="Inspect the queue",
        correlationId="request-1",
        triggeringEventId="event-1",
    )


class CoordinationRoutingTests(unittest.TestCase):
    def test_independent_intent_invokes_requested_agent_directly(self) -> None:
        invoker = RecordingInvoker()
        runtime = CoordinationRuntime(
            plan_for({"mode": "independent"}),
            invoker,
            clock=lambda: NOW,
            message_id_factory=ids("message-1"),
        )

        outcome = runtime.coordinate(request())

        self.assertEqual(outcome.entry_agent_id, "switch-router@s1")
        self.assertEqual(
            [(agent, intent) for _, agent, intent, _ in invoker.calls],
            [("switch-router@s1", "Inspect the queue")],
        )
        self.assertEqual(invoker.calls[0][3].allowed_destinations, ())
        self.assertEqual(len(outcome.messages), 1)
        self.assertEqual(len(outcome.invocations), 1)
        self.assertEqual(outcome.issues, ())

    def test_centralized_intent_enters_coordinator_then_delegates(self) -> None:
        def respond(
            agent_id: str,
            context: AgentCoordinationContext,
        ) -> AgentResponse:
            if agent_id == "global-router":
                return AgentResponse(
                    delegations=(
                        DelegationProposal(
                            id="delegate-1",
                            targetAgentId=context.requested_agent_id,
                            intent="Inspect your local queue",
                            metadata={"reason": "requested target"},
                        ),
                    )
                )
            return AgentResponse(message="queue inspected")

        invoker = RecordingInvoker(respond)
        runtime = CoordinationRuntime(
            plan_for({"mode": "centralized", "coordinator": "global-router"}),
            invoker,
            clock=lambda: NOW,
            message_id_factory=ids("message-1", "message-2"),
        )

        outcome = runtime.coordinate(request())

        self.assertEqual(outcome.entry_agent_id, "global-router")
        self.assertEqual(
            [agent for _, agent, _, _ in invoker.calls],
            ["global-router", "switch-router@s1"],
        )
        root_context = invoker.calls[0][3]
        self.assertEqual(root_context.requested_agent_id, "switch-router@s1")
        self.assertIn("switch-router@s1", root_context.allowed_destinations)
        delegated_context = invoker.calls[1][3]
        self.assertEqual(delegated_context.source_agent_id, "global-router")
        self.assertEqual(delegated_context.hop_count, 1)
        self.assertEqual(
            delegated_context.metadata,
            {"reason": "requested target"},
        )
        self.assertEqual(outcome.messages[1].causation_id, "message-1")
        self.assertEqual(outcome.messages[1].parent_invocation_id, "invoke-1")
        self.assertEqual(outcome.issues, ())

    def test_hierarchical_reverse_delegation_is_rejected(self) -> None:
        def respond(
            agent_id: str,
            context: AgentCoordinationContext,
        ) -> AgentResponse:
            del agent_id, context
            return AgentResponse(
                delegations=(
                    DelegationProposal(
                        id="delegate-1",
                        targetAgentId="control-router@control-domain",
                        intent="Escalate upward",
                    ),
                )
            )

        invoker = RecordingInvoker(respond)
        runtime = CoordinationRuntime(
            plan_for(
                {
                    "mode": "hierarchical",
                    "relationships": [
                        {
                            "source": "control-router",
                            "targets": ["switch-router"],
                        }
                    ],
                }
            ),
            invoker,
            clock=lambda: NOW,
            message_id_factory=ids("message-1"),
        )

        outcome = runtime.coordinate(request())

        self.assertEqual(len(invoker.calls), 1)
        self.assertEqual(len(outcome.messages), 1)
        self.assertEqual(
            outcome.issues[0].code,
            "coordination.graph.delivery-denied",
        )
        self.assertEqual(
            outcome.issues[0].source_agent_id,
            "switch-router@s1",
        )

    def test_channel_capacity_rejects_excess_sibling_delegation(self) -> None:
        def respond(
            agent_id: str,
            context: AgentCoordinationContext,
        ) -> AgentResponse:
            del context
            if agent_id != "global-router":
                return AgentResponse(message="done")
            return AgentResponse(
                delegations=(
                    DelegationProposal(
                        id="delegate-1",
                        targetAgentId="switch-router@s1",
                        intent="Inspect s1",
                    ),
                    DelegationProposal(
                        id="delegate-2",
                        targetAgentId="switch-router@s2",
                        intent="Inspect s2",
                    ),
                )
            )

        invoker = RecordingInvoker(respond)
        runtime = CoordinationRuntime(
            plan_for({"mode": "centralized", "coordinator": "global-router"}),
            invoker,
            channel=InMemoryMessageChannel(capacity=1),
            clock=lambda: NOW,
            message_id_factory=ids("message-1", "message-2", "message-3"),
        )

        outcome = runtime.coordinate(request())

        self.assertEqual(
            [agent for _, agent, _, _ in invoker.calls],
            ["global-router", "switch-router@s1"],
        )
        self.assertEqual(len(outcome.messages), 2)
        self.assertEqual(
            outcome.issues[0].code,
            "coordination.message-channel.full",
        )

    def test_peer_cycle_stops_at_graph_derived_hop_limit(self) -> None:
        peer_a = "global-router"
        peer_b = "control-router@control-domain"

        def respond(
            agent_id: str,
            context: AgentCoordinationContext,
        ) -> AgentResponse:
            del context
            target = peer_b if agent_id == peer_a else peer_a
            return AgentResponse(
                delegations=(
                    DelegationProposal(
                        id="continue",
                        targetAgentId=target,
                        intent="Continue peer exchange",
                    ),
                )
            )

        invoker = RecordingInvoker(respond)
        runtime = CoordinationRuntime(
            plan_for({"mode": "distributed", "peers": "all"}),
            invoker,
            clock=lambda: NOW,
        )

        outcome = runtime.coordinate(request(peer_a))

        self.assertEqual(len(outcome.invocations), 13)
        self.assertEqual(len(outcome.messages), 13)
        self.assertEqual(
            outcome.issues[-1].code,
            "coordination.message.hop-limit-exceeded",
        )

    def test_invalid_invocation_identity_stops_delegation(self) -> None:
        class WrongInvoker(RecordingInvoker):
            def invoke(
                self,
                run_id: str,
                agent_id: str,
                intent: str,
                *,
                coordination: AgentCoordinationContext | None = None,
            ) -> AgentInvocationResult:
                result = super().invoke(
                    run_id,
                    agent_id,
                    intent,
                    coordination=coordination,
                )
                return result.model_copy(update={"agent_id": "another-agent"})

        runtime = CoordinationRuntime(
            plan_for({"mode": "independent"}),
            WrongInvoker(),
            clock=lambda: NOW,
            message_id_factory=ids("message-1"),
        )

        outcome = runtime.coordinate(request())

        self.assertEqual(outcome.invocations, ())
        self.assertEqual(
            outcome.issues[0].code,
            "coordination.invocation.invalid-identity",
        )


if __name__ == "__main__":
    unittest.main()
