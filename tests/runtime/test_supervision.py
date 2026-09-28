from __future__ import annotations

import threading
import time
import unittest
from datetime import UTC, datetime

from mininet_ai.runtime import (
    AgentLifecycleState,
    AgentSupervisionError,
    AgentSupervisor,
)
from mininet_ai.sdk import (
    AgentInvocationResult,
    AgentResponse,
    AgentRuntimeIssue,
    InvocationStatus,
)
from mininet_ai.specification.models import RestartConfiguration


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def result(status: InvocationStatus, invocation: int) -> AgentInvocationResult:
    return AgentInvocationResult(
        invocationId=f"invoke-{invocation}",
        runId="run-1",
        agentId="agent-1",
        status=status,
        startedAt=NOW,
        completedAt=NOW,
        response=(
            AgentResponse(message="done")
            if status == InvocationStatus.SUCCEEDED
            else None
        ),
        issue=(
            AgentRuntimeIssue(code="agent.failed", message="failed")
            if status != InvocationStatus.SUCCEEDED
            else None
        ),
    )


class AgentSupervisorTests(unittest.TestCase):
    def test_failed_result_restarts_with_backoff_and_retries(self) -> None:
        transitions = []
        sleeps = []
        calls = 0
        supervisor = AgentSupervisor(
            {
                "agent-1": RestartConfiguration(
                    policy="on-failure",
                    maxAttempts=2,
                    backoff="25ms",
                )
            },
            listener=transitions.append,
            clock=lambda: NOW,
            sleeper=sleeps.append,
        )
        supervisor.start()

        def invoke() -> AgentInvocationResult:
            nonlocal calls
            calls += 1
            status = (
                InvocationStatus.FAILED
                if calls == 1
                else InvocationStatus.SUCCEEDED
            )
            return result(status, calls)

        invocation = supervisor.execute("agent-1", invoke)

        self.assertEqual(invocation.status, InvocationStatus.SUCCEEDED)
        self.assertEqual(calls, 2)
        self.assertEqual(sleeps, [0.025])
        self.assertEqual(
            [transition.state for transition in transitions],
            [
                AgentLifecycleState.STARTING,
                AgentLifecycleState.RUNNING,
                AgentLifecycleState.FAILED,
                AgentLifecycleState.RESTARTING,
                AgentLifecycleState.RUNNING,
            ],
        )
        self.assertEqual(transitions[-1].attempt, 1)

    def test_raised_failure_exhausts_budget_and_leaves_agent_failed(self) -> None:
        supervisor = AgentSupervisor(
            {
                "agent-1": RestartConfiguration(
                    policy="on-failure",
                    maxAttempts=1,
                    backoff="0s",
                )
            },
            sleeper=lambda _: None,
        )
        supervisor.start()
        calls = 0

        def invoke() -> AgentInvocationResult:
            nonlocal calls
            calls += 1
            raise RuntimeError("provider unavailable")

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            supervisor.execute("agent-1", invoke)

        self.assertEqual(calls, 2)
        self.assertEqual(supervisor.state("agent-1"), AgentLifecycleState.FAILED)

    def test_rejected_invocation_is_not_restarted(self) -> None:
        supervisor = AgentSupervisor(
            {
                "agent-1": RestartConfiguration(
                    policy="on-failure",
                    maxAttempts=2,
                    backoff="0s",
                )
            }
        )
        supervisor.start()
        calls = 0

        def invoke() -> AgentInvocationResult:
            nonlocal calls
            calls += 1
            return result(InvocationStatus.REJECTED, calls)

        invocation = supervisor.execute("agent-1", invoke)

        self.assertEqual(invocation.status, InvocationStatus.REJECTED)
        self.assertEqual(calls, 1)
        self.assertEqual(supervisor.state("agent-1"), AgentLifecycleState.RUNNING)

    def test_pause_blocks_new_work_until_resume(self) -> None:
        supervisor = AgentSupervisor(
            {"agent-1": RestartConfiguration()}
        )
        supervisor.start()
        supervisor.pause("agent-1")
        entered = threading.Event()

        def invoke() -> AgentInvocationResult:
            entered.set()
            return result(InvocationStatus.SUCCEEDED, 1)

        worker = threading.Thread(
            target=supervisor.execute,
            args=("agent-1", invoke),
        )
        worker.start()
        time.sleep(0.02)
        self.assertFalse(entered.is_set())

        supervisor.resume("agent-1")
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(entered.is_set())

    def test_invalid_lifecycle_and_unknown_agent_are_typed(self) -> None:
        supervisor = AgentSupervisor(
            {"agent-1": RestartConfiguration()}
        )
        supervisor.start()

        with self.assertRaises(AgentSupervisionError) as lifecycle:
            supervisor.resume("agent-1")
        self.assertEqual(
            lifecycle.exception.code,
            "runtime.agent.lifecycle.invalid",
        )

        with self.assertRaises(AgentSupervisionError) as unknown:
            supervisor.state("missing")
        self.assertEqual(unknown.exception.code, "runtime.agent.unknown")


if __name__ == "__main__":
    unittest.main()
