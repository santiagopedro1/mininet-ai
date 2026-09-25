"""Stateful rootless substrate used by the Phase 4 demonstration."""

from __future__ import annotations

from threading import Event, Lock

from mininet_ai.substrates import (
    ActionRequest,
    ActionResult,
    FakeSubstrateRuntime,
    ObservationQuery,
    ObservationResult,
)


class CongestedFakeRuntime(FakeSubstrateRuntime):
    """Expose congestion and make OpenFlow installation observable."""

    def __init__(self) -> None:
        super().__init__()
        self.action_observed = Event()
        self._state_lock = Lock()
        self._installed: dict[str, bool] = {}

    def observe(
        self,
        run_id: str,
        query: ObservationQuery,
    ) -> ObservationResult:
        baseline = super().observe(run_id, query)
        if query.name == "tc.queue-occupancy":
            values = {
                target: {"queue": {"depth": 90}}
                for target in query.targets
            }
        elif query.name == "openflow.flows":
            with self._state_lock:
                values = {
                    target: {"installed": self._installed.get(target, False)}
                    for target in query.targets
                }
        else:
            return baseline
        return baseline.model_copy(update={"values": values})

    def execute(self, run_id: str, request: ActionRequest) -> ActionResult:
        result = super().execute(run_id, request)
        installed = None
        with self._state_lock:
            if request.name == "openflow.flow.install":
                self._installed[request.target] = True
                installed = True
            elif request.name == "openflow.flow.remove":
                self._installed[request.target] = False
                installed = False
        if installed is None:
            return result
        self.action_observed.set()
        return result.model_copy(update={"output": {"installed": installed}})
