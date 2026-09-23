"""In-memory adapter for the substrate runtime lifecycle interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from mininet_ai.errors import RuntimeOperationError
from mininet_ai.substrates.runtime import (
    RUNTIME_CONTRACT_VERSION,
    ActionRequest,
    ActionResult,
    ActionStatus,
    LiveResource,
    ObservationQuery,
    ObservationResult,
    ResourceOperationalState,
    RunInfo,
    RunState,
    RuntimeSnapshot,
    TeardownResult,
)

if TYPE_CHECKING:
    from mininet_ai.compiler.models import DeploymentPlan


Clock = Callable[[], datetime]
RunIdFactory = Callable[[], str]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_id() -> str:
    return f"run-{uuid4()}"


@dataclass
class _FakeRun:
    info: RunInfo
    resources: tuple[LiveResource, ...]


class FakeSubstrateRuntime:
    """Deterministic in-memory lifecycle behavior for callers and tests."""

    name = "fake"
    contract_version = RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        *,
        clock: Clock = _utc_now,
        run_id_factory: RunIdFactory = _run_id,
    ) -> None:
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._runs: dict[str, _FakeRun] = {}

    def deploy(self, plan: DeploymentPlan) -> RunInfo:
        if plan.substrate != self.name:
            raise RuntimeOperationError(
                f"plan uses substrate {plan.substrate!r}, not {self.name!r}",
                code="runtime.substrate.mismatch",
            )

        run_id = self._run_id_factory()
        if not run_id:
            raise RuntimeOperationError(
                "runtime produced an empty run id",
                code="runtime.run.invalid-id",
            )
        if run_id in self._runs:
            raise RuntimeOperationError(
                f"run id {run_id!r} already exists",
                code="runtime.run.duplicate",
                run_id=run_id,
            )

        info = RunInfo(
            id=run_id,
            substrate=self.name,
            plan_digest=plan.digest,
            state=RunState.RUNNING,
            started_at=self._clock(),
        )
        resources = tuple(
            LiveResource(
                name=resource.name,
                kind=resource.kind,
                state=ResourceOperationalState.UP,
                attributes=resource.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude={"name", "kind"},
                    exclude_none=True,
                ),
            )
            for resource in plan.resources
        )
        self._runs[run_id] = _FakeRun(info=info, resources=resources)
        return info

    def inspect(self, run_id: str) -> RuntimeSnapshot:
        run = self._get_run(run_id)
        return RuntimeSnapshot(
            run=run.info,
            observed_at=self._clock(),
            resources=run.resources,
        )

    def observe(
        self, run_id: str, query: ObservationQuery
    ) -> ObservationResult:
        run = self._require_running(run_id)
        resources = {resource.name for resource in run.resources}
        self._require_targets(run_id, query.targets, resources)
        return ObservationResult(
            run_id=run_id,
            query=query,
            observed_at=self._clock(),
            values={
                target: {
                    "observation": query.name,
                    "parameters": query.parameters,
                }
                for target in query.targets
            },
        )

    def execute(self, run_id: str, request: ActionRequest) -> ActionResult:
        run = self._require_running(run_id)
        resources = {resource.name for resource in run.resources}
        self._require_targets(run_id, (request.target,), resources)
        return ActionResult(
            run_id=run_id,
            request_id=request.id,
            status=ActionStatus.SUCCEEDED,
            completed_at=self._clock(),
            changed=True,
            output={
                "action": request.name,
                "target": request.target,
                "parameters": request.parameters,
            },
        )

    def teardown(self, run_id: str) -> TeardownResult:
        run = self._get_run(run_id)
        if run.info.state == RunState.STOPPED:
            return TeardownResult(run=run.info, already_stopped=True)

        stopped_at = self._clock()
        run.info = run.info.model_copy(
            update={"state": RunState.STOPPED, "stopped_at": stopped_at}
        )
        released = tuple(resource.name for resource in run.resources)
        run.resources = tuple(
            resource.model_copy(update={"state": ResourceOperationalState.STOPPED})
            for resource in run.resources
        )
        return TeardownResult(run=run.info, released_resources=released)

    def _get_run(self, run_id: str) -> _FakeRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise RuntimeOperationError(
                f"unknown substrate run {run_id!r}",
                code="runtime.run.unknown",
                run_id=run_id,
            ) from error

    def _require_running(self, run_id: str) -> _FakeRun:
        run = self._get_run(run_id)
        if run.info.state != RunState.RUNNING:
            raise RuntimeOperationError(
                f"substrate run {run_id!r} is {run.info.state.value}, not running",
                code="runtime.run.not-running",
                run_id=run_id,
            )
        return run

    @staticmethod
    def _require_targets(
        run_id: str, targets: tuple[str, ...], resources: set[str]
    ) -> None:
        unknown = sorted(set(targets) - resources)
        if unknown:
            names = ", ".join(unknown)
            raise RuntimeOperationError(
                f"run {run_id!r} has no resources named: {names}",
                code="runtime.resource.unknown",
                run_id=run_id,
            )
