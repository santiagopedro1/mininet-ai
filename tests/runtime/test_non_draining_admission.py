"""Deterministically exercise stop races at coordinated admission boundaries."""

from unittest.mock import patch

import pytest

from mininet_ai.coordination import InvocationLease
from mininet_ai.runtime import ContinuousAgentRuntime, ContinuousRuntimeState
from tests.runtime.test_continuous import configured_plan, manual_event


@pytest.mark.parametrize("stop_boundary", ["paused-wakeup", "capacity-reservation"])
def test_non_draining_stop_does_not_admit_a_waiting_request(stop_boundary):
    plan = configured_plan(triggers=[{"type": "manual", "name": "operator"}])
    leases = []

    class Coordinator:
        def reserve_invocation(self, agent_id):
            # Stop can race with a successful capacity reservation too.
            runtime._state = ContinuousRuntimeState.STOPPING
            runtime._drain = False
            runtime._workers_closed = True
            lease = InvocationLease(agent_id, lambda: None)
            leases.append(lease)
            return lease

        def coordinate(self, request, *, lease=None):
            raise AssertionError("pending requests must not execute during discard")

        def close(self):
            return None

    runtime = ContinuousAgentRuntime(plan, "run-1", Coordinator())
    runtime._state = ContinuousRuntimeState.RUNNING
    runtime._supervisor.start()
    runtime._dispatch(manual_event("pending", sequence=1))
    assert runtime.report().enqueued == 1

    def wake_from_pause(agent_id):
        if stop_boundary == "paused-wakeup":
            # stop(drain=False) resumes paused waiters before closing the bus.
            # The queue is still present when this waiter wakes.
            runtime._state = ContinuousRuntimeState.STOPPING
            runtime._drain = False

    executor = runtime._request_executor
    assert executor is not None
    try:
        with (
            patch.object(runtime._supervisor, "wait_until_running", wake_from_pause),
            patch.object(executor, "submit") as submit,
        ):
            runtime._coordinated_worker("switch-router@s1")
            assert submit.call_count == 0, "shutdown admitted a pending invocation"
            assert len(leases) == (0 if stop_boundary == "paused-wakeup" else 1)
            assert all(not lease.active for lease in leases)
            assert runtime._request_slots._value == runtime._request_capacity
            assert runtime.report().completed == 0
            assert runtime.report().failed == 0
    finally:
        for lease in leases:
            lease.close()
        executor.shutdown(wait=True)
