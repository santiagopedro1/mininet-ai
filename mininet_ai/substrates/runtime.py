"""Versioned lifecycle interface for deployed substrate runs.

The planning ``SubstrateDriver`` remains a rootless compiler dependency. This
module defines the separate runtime seam used after a deployment plan has been
accepted and privileged substrate work may begin.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import AwareDatetime, Field, model_validator

from mininet_ai.specification.models import ResourceKind, StrictModel

if TYPE_CHECKING:
    from mininet_ai.compiler.models import DeploymentPlan


RUNTIME_CONTRACT_VERSION = "mininet-ai/substrate-runtime/v1alpha1"


class RunState(StrEnum):
    DEPLOYING = "deploying"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class ResourceOperationalState(StrEnum):
    UP = "up"
    DOWN = "down"
    STOPPED = "stopped"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ActionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


class RuntimeIssue(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    target: str | None = None


class RunInfo(StrictModel):
    id: str = Field(min_length=1)
    substrate: str = Field(min_length=1)
    plan_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: RunState
    started_at: AwareDatetime
    stopped_at: AwareDatetime | None = None
    issue: RuntimeIssue | None = None

    @model_validator(mode="after")
    def terminal_fields_match_state(self) -> RunInfo:
        if self.state == RunState.STOPPED and self.stopped_at is None:
            raise ValueError("a stopped run requires stopped_at")
        if self.state != RunState.STOPPED and self.stopped_at is not None:
            raise ValueError("stopped_at is only valid for a stopped run")
        if self.state == RunState.FAILED and self.issue is None:
            raise ValueError("a failed run requires an issue")
        return self


class LiveResource(StrictModel):
    name: str = Field(min_length=1)
    kind: ResourceKind
    state: ResourceOperationalState
    attributes: dict[str, Any] = Field(default_factory=dict)


class RuntimeSnapshot(StrictModel):
    run: RunInfo
    observed_at: AwareDatetime
    resources: tuple[LiveResource, ...]


class ObservationQuery(StrictModel):
    name: str = Field(min_length=1)
    targets: tuple[str, ...] = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ObservationResult(StrictModel):
    run_id: str = Field(min_length=1)
    query: ObservationQuery
    observed_at: AwareDatetime
    values: dict[str, Any] = Field(default_factory=dict)


class ActionRequest(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    target: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=30, gt=0)


class ActionResult(StrictModel):
    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    status: ActionStatus
    completed_at: AwareDatetime
    changed: bool = False
    output: dict[str, Any] = Field(default_factory=dict)
    issue: RuntimeIssue | None = None

    @model_validator(mode="after")
    def issue_matches_status(self) -> ActionResult:
        if self.status == ActionStatus.SUCCEEDED and self.issue is not None:
            raise ValueError("a successful action cannot contain an issue")
        if self.status != ActionStatus.SUCCEEDED and self.issue is None:
            raise ValueError("an unsuccessful action requires an issue")
        return self


class TeardownResult(StrictModel):
    run: RunInfo
    released_resources: tuple[str, ...] = ()
    already_stopped: bool = False


@runtime_checkable
class SubstrateRuntime(Protocol):
    """Lifecycle interface implemented by each executable substrate adapter.

    ``deploy`` is synchronous: it returns only after the run is healthy. An
    implementation must roll back its owned effects before raising on a
    deployment failure. Observation and action operations require a running
    run. ``teardown`` affects only resources owned by the run and is
    idempotent.
    """

    name: str
    contract_version: str

    def deploy(self, plan: DeploymentPlan) -> RunInfo:
        """Deploy a validated plan and return its running identity."""
        ...

    def inspect(self, run_id: str) -> RuntimeSnapshot:
        """Return lifecycle and normalized live-resource state for a run."""
        ...

    def observe(
        self, run_id: str, query: ObservationQuery
    ) -> ObservationResult:
        """Read a normalized observation from a running run."""
        ...

    def execute(self, run_id: str, request: ActionRequest) -> ActionResult:
        """Execute one typed substrate action against a running run."""
        ...

    def teardown(self, run_id: str) -> TeardownResult:
        """Release run-owned resources; repeated calls are successful no-ops."""
        ...


@runtime_checkable
class ExternallyStoppableRuntime(SubstrateRuntime, Protocol):
    """Optional interface for asking a separate owner process to stop a run."""

    def request_stop(
        self, run_id: str, *, timeout_seconds: float = 30
    ) -> TeardownResult:
        """Request owner teardown and wait for the run to stop."""
        ...
