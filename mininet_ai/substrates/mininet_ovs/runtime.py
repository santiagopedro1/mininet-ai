"""Live Mininet/OVS implementation of the substrate runtime contract."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from mininet_ai.errors import RuntimeOperationError
from mininet_ai.specification.models import (
    ControllerType,
    ResourceKind,
    SwitchDatapath,
)
from mininet_ai.substrates.mininet_ovs.actions import (
    ActionExecutionError,
    ActionProvider,
    MininetOVSActions,
)
from mininet_ai.substrates.mininet_ovs.driver import (
    MininetOVSDriver,
    parse_default_route,
)
from mininet_ai.substrates.mininet_ovs.observations import (
    MininetOVSObservations,
    ObservationCollectionError,
    ObservationProvider,
)
from mininet_ai.substrates.mininet_ovs.state import (
    STATE_API_VERSION,
    PersistedRun,
    ProcessOwner,
    RunStateStore,
    StateLockHeld,
    StateStoreError,
)
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
    RuntimeIssue,
    RuntimeSnapshot,
    TeardownResult,
)

if TYPE_CHECKING:
    from mininet_ai.compiler.models import (
        DeploymentPlan,
        PlannedController,
        PlannedHost,
        PlannedLink,
        PlannedPort,
        PlannedResource,
        PlannedSwitch,
    )


Clock = Callable[[], datetime]
RunIdFactory = Callable[[], str]
Recovery = Callable[["DeploymentPlan", tuple[ProcessOwner, ...]], None]
ObservationFactory = Callable[["DeploymentPlan", Any], ObservationProvider]
ActionFactory = Callable[["DeploymentPlan", Any], ActionProvider]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _run_id() -> str:
    return f"mn-{uuid4()}"


@dataclass(frozen=True)
class _MininetBindings:
    network_class: type
    host_class: type
    ovs_controller_class: type
    ovs_switch_class: type
    remote_controller_class: type
    tc_link_class: type


def _load_mininet_bindings() -> _MininetBindings:
    if os.geteuid() != 0:
        raise RuntimeOperationError(
            "the Mininet/OVS runtime requires root privileges",
            code="runtime.permission.denied",
        )
    try:
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import Host, OVSController, OVSSwitch, RemoteController
    except ImportError as error:
        raise RuntimeOperationError(
            "Mininet is not installed in the runtime environment",
            code="runtime.dependency.missing",
        ) from error

    return _MininetBindings(
        network_class=Mininet,
        host_class=Host,
        ovs_controller_class=OVSController,
        ovs_switch_class=OVSSwitch,
        remote_controller_class=RemoteController,
        tc_link_class=TCLink,
    )


@dataclass
class _MininetRun:
    info: RunInfo
    plan: DeploymentPlan
    network: Any
    observations: ObservationProvider
    actions: ActionProvider
    resources: tuple[LiveResource, ...]


def _interface_exists(name: str) -> bool:
    try:
        socket.if_nametoindex(name)
    except OSError:
        return False
    return True


def _observation_provider(
    plan: DeploymentPlan, network: Any
) -> MininetOVSObservations:
    return MininetOVSObservations(plan, network)


def _action_provider(plan: DeploymentPlan, network: Any) -> MininetOVSActions:
    return MininetOVSActions(plan, network)


def _live_resources(
    resources: tuple[PlannedResource, ...], state: ResourceOperationalState
) -> tuple[LiveResource, ...]:
    return tuple(
        LiveResource(
            name=resource.name,
            kind=resource.kind,
            state=state,
            attributes=resource.model_dump(
                mode="json",
                by_alias=True,
                exclude={"name", "kind"},
                exclude_none=True,
            ),
        )
        for resource in resources
    )


class MininetOVSRuntime:
    """Deploy one compiled topology through Mininet's Python implementation."""

    name = "mininet-ovs"
    contract_version = RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        *,
        clock: Clock = _utc_now,
        run_id_factory: RunIdFactory = _run_id,
        bindings_factory: Callable[[], _MininetBindings] = _load_mininet_bindings,
        connect_timeout_seconds: float = 5,
        state_store: RunStateStore | None = None,
        owner: ProcessOwner | None = None,
        recovery: Recovery | None = None,
        observation_factory: ObservationFactory = _observation_provider,
        action_factory: ActionFactory = _action_provider,
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._bindings_factory = bindings_factory
        self._connect_timeout_seconds = connect_timeout_seconds
        self._state_store = state_store or RunStateStore()
        self._owner = owner or ProcessOwner.current()
        self._recovery = recovery or self._recover_owned_resources
        self._observation_factory = observation_factory
        self._action_factory = action_factory
        self._runs: dict[str, _MininetRun] = {}

    def deploy(self, plan: DeploymentPlan) -> RunInfo:
        self._validate_plan(plan)
        self._ensure_no_active_run()
        run_id = self._new_run_id()
        started_at = self._clock()
        info = RunInfo(
            id=run_id,
            substrate=self.name,
            plan_digest=plan.digest,
            state=RunState.DEPLOYING,
            started_at=started_at,
        )
        network: Any | None = None
        process_groups: tuple[ProcessOwner, ...] = ()

        self._claim_run(info, plan)

        try:
            bindings = self._bindings_factory()
            self._preflight_interfaces(plan)
            network = self._build_network(plan, bindings)
            process_groups = self._network_process_groups(network)
            self._write_state(info, plan, process_groups)
            self._start_network(plan, network)
            observations = self._observation_factory(plan, network)
            actions = self._action_factory(plan, network)
            resources = observations.snapshot()
            unavailable = sorted(
                resource.name
                for resource in resources
                if resource.state
                in {
                    ResourceOperationalState.DOWN,
                    ResourceOperationalState.FAILED,
                }
            )
            if unavailable:
                raise RuntimeError(
                    "deployed resources are not operational: "
                    + ", ".join(unavailable)
                )
            info = info.model_copy(update={"state": RunState.RUNNING})
            self._write_state(info, plan, process_groups)
        except Exception as error:
            rollback_error = self._rollback(network, plan) if network else None
            state_error = self._settle_failed_deploy(
                info,
                plan,
                process_groups,
                rollback_error,
            )
            if (
                isinstance(error, RuntimeOperationError)
                and rollback_error is None
                and state_error is None
            ):
                raise
            detail = f": {error}"
            if rollback_error is not None:
                detail += f"; rollback also failed: {rollback_error}"
            if state_error is not None:
                detail += f"; runtime state update also failed: {state_error}"
            raise RuntimeOperationError(
                f"could not deploy Mininet/OVS run {run_id!r}{detail}",
                code="runtime.deploy.failed",
                run_id=run_id,
            ) from error

        self._runs[run_id] = _MininetRun(
            info=info,
            plan=plan,
            network=network,
            observations=observations,
            actions=actions,
            resources=resources,
        )
        return info

    def inspect(self, run_id: str) -> RuntimeSnapshot:
        run = self._runs.get(run_id)
        if run is None:
            return self._inspect_persisted(run_id)
        try:
            run.resources = run.observations.snapshot()
        except ObservationCollectionError as error:
            raise RuntimeOperationError(
                f"could not inspect Mininet/OVS run {run_id!r}: {error}",
                code=error.code,
                run_id=run_id,
            ) from error
        except Exception as error:
            raise RuntimeOperationError(
                f"could not inspect Mininet/OVS run {run_id!r}: {error}",
                code="runtime.observation.failed",
                run_id=run_id,
            ) from error
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
        try:
            values = run.observations.collect(query)
        except ObservationCollectionError as error:
            raise RuntimeOperationError(
                str(error),
                code=error.code,
                run_id=run_id,
            ) from error
        except Exception as error:
            raise RuntimeOperationError(
                f"could not collect observation {query.name!r}: {error}",
                code="runtime.observation.failed",
                run_id=run_id,
            ) from error
        return ObservationResult(
            run_id=run_id,
            query=query,
            observed_at=self._clock(),
            values=values,
        )

    def execute(self, run_id: str, request: ActionRequest) -> ActionResult:
        run = self._require_running(run_id)
        self._require_targets(
            run_id,
            (request.target,),
            {resource.name for resource in run.resources},
        )
        try:
            outcome = run.actions.execute(request)
            run.resources = run.observations.snapshot()
        except ActionExecutionError as error:
            return ActionResult(
                run_id=run_id,
                request_id=request.id,
                status=error.status,
                completed_at=self._clock(),
                issue=RuntimeIssue(
                    code=error.code,
                    message=str(error),
                    target=request.target,
                ),
            )
        except Exception as error:
            return ActionResult(
                run_id=run_id,
                request_id=request.id,
                status=ActionStatus.FAILED,
                completed_at=self._clock(),
                issue=RuntimeIssue(
                    code="runtime.action.failed",
                    message=str(error),
                    target=request.target,
                ),
            )
        return ActionResult(
            run_id=run_id,
            request_id=request.id,
            status=ActionStatus.SUCCEEDED,
            completed_at=self._clock(),
            changed=outcome.changed,
            output=outcome.output,
        )

    def teardown(self, run_id: str) -> TeardownResult:
        run = self._runs.get(run_id)
        if run is None:
            return self._recover_persisted(run_id)
        if run.info.state == RunState.STOPPED:
            return TeardownResult(run=run.info, already_stopped=True)

        try:
            run.info = run.info.model_copy(update={"state": RunState.STOPPING})
            self._write_state(
                run.info,
                run.plan,
                self._network_process_groups(run.network),
            )
            run.actions.close()
            run.network.stop()
            self._remove_owned_artifacts(run.plan)
            self._state_store.clear()
        except Exception as error:
            failed_info = run.info.model_copy(
                update={
                    "state": RunState.FAILED,
                    "issue": RuntimeIssue(
                        code="runtime.teardown.failed",
                        message=str(error),
                    ),
                }
            )
            run.info = failed_info
            run.resources = tuple(
                resource.model_copy(
                    update={"state": ResourceOperationalState.UNKNOWN}
                )
                for resource in run.resources
            )
            try:
                self._write_state(
                    failed_info,
                    run.plan,
                    self._network_process_groups(run.network),
                )
            except StateStoreError:
                pass
            self._state_store.release()
            raise RuntimeOperationError(
                f"could not tear down Mininet/OVS run {run_id!r}: {error}",
                code="runtime.teardown.failed",
                run_id=run_id,
            ) from error
        self._state_store.release()

        run.info = run.info.model_copy(
            update={"state": RunState.STOPPED, "stopped_at": self._clock()}
        )
        released = tuple(resource.name for resource in run.resources)
        run.resources = tuple(
            resource.model_copy(update={"state": ResourceOperationalState.STOPPED})
            for resource in run.resources
        )
        return TeardownResult(run=run.info, released_resources=released)

    def request_stop(
        self, run_id: str, *, timeout_seconds: float = 30
    ) -> TeardownResult:
        """Ask the verified owner process to tear down an active run."""

        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if run_id in self._runs:
            return self.teardown(run_id)
        try:
            record = self._state_store.read()
        except StateStoreError as error:
            raise RuntimeOperationError(
                f"could not read Mininet/OVS runtime state: {error}",
                code="runtime.state.failed",
                run_id=run_id,
            ) from error
        if record is None or record.run.id != run_id:
            self._raise_unknown_run(run_id)

        if not record.owner.is_alive() or not self._state_store.is_locked():
            return self.teardown(run_id)
        try:
            os.kill(record.owner.pid, signal.SIGTERM)
        except PermissionError as error:
            raise RuntimeOperationError(
                f"permission denied signaling owner of run {run_id!r}",
                code="runtime.permission.denied",
                run_id=run_id,
            ) from error
        except ProcessLookupError:
            return self.teardown(run_id)

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
            try:
                current = self._state_store.read()
            except StateStoreError as error:
                raise RuntimeOperationError(
                    f"could not read Mininet/OVS runtime state: {error}",
                    code="runtime.state.failed",
                    run_id=run_id,
                ) from error
            if current is None:
                stopped = record.run.model_copy(
                    update={
                        "state": RunState.STOPPED,
                        "stopped_at": self._clock(),
                        "issue": None,
                    }
                )
                plan = self._plan_from_record(record)
                return TeardownResult(
                    run=stopped,
                    released_resources=tuple(
                        resource.name for resource in plan.resources
                    ),
                )
            if current.run.id != run_id:
                raise RuntimeOperationError(
                    f"runtime state changed while stopping run {run_id!r}",
                    code="runtime.state.changed",
                    run_id=run_id,
                )
            if not current.owner.is_alive():
                return self.teardown(run_id)
        raise RuntimeOperationError(
            f"timed out waiting for run {run_id!r} to stop",
            code="runtime.stop.timeout",
            run_id=run_id,
        )

    def _claim_run(self, info: RunInfo, plan: DeploymentPlan) -> None:
        try:
            self._state_store.acquire()
            existing = self._state_store.read()
            if existing is not None:
                self._state_store.release()
                raise RuntimeOperationError(
                    "Mininet/OVS run "
                    f"{existing.run.id!r} was orphaned; tear it down before "
                    "deploying another run",
                    code="runtime.run.orphaned",
                    run_id=existing.run.id,
                )
            self._write_state(info, plan, ())
        except StateLockHeld as error:
            existing = self._read_state_for_error()
            raise RuntimeOperationError(
                "another process owns the Mininet/OVS runtime",
                code="runtime.run.active",
                run_id=existing.run.id if existing is not None else None,
            ) from error
        except StateStoreError as error:
            self._state_store.release()
            raise RuntimeOperationError(
                f"could not claim Mininet/OVS runtime state: {error}",
                code="runtime.state.failed",
                run_id=info.id,
            ) from error

    def _settle_failed_deploy(
        self,
        info: RunInfo,
        plan: DeploymentPlan,
        process_groups: tuple[ProcessOwner, ...],
        rollback_error: Exception | None,
    ) -> StateStoreError | None:
        try:
            if rollback_error is None:
                self._state_store.clear()
            else:
                failed_info = info.model_copy(
                    update={
                        "state": RunState.FAILED,
                        "issue": RuntimeIssue(
                            code="runtime.rollback.failed",
                            message=str(rollback_error),
                        ),
                    }
                )
                self._write_state(failed_info, plan, process_groups)
        except StateStoreError as error:
            return error
        finally:
            self._state_store.release()
        return None

    def _write_state(
        self,
        info: RunInfo,
        plan: DeploymentPlan,
        process_groups: tuple[ProcessOwner, ...],
    ) -> None:
        self._state_store.write(
            PersistedRun(
                apiVersion=STATE_API_VERSION,
                run=info,
                owner=self._owner,
                plan=plan.model_dump(mode="json", by_alias=True),
                processGroups=process_groups,
            )
        )

    def _validate_plan(self, plan: DeploymentPlan) -> None:
        if plan.substrate != self.name:
            raise RuntimeOperationError(
                f"plan uses substrate {plan.substrate!r}, not {self.name!r}",
                code="runtime.substrate.mismatch",
            )
        issues = MininetOVSDriver().validate_resources(plan.resources)
        if issues:
            detail = "; ".join(issue.format() for issue in issues)
            raise RuntimeOperationError(
                f"invalid Mininet/OVS deployment plan: {detail}",
                code="runtime.plan.invalid",
            )

    def _inspect_persisted(self, run_id: str) -> RuntimeSnapshot:
        try:
            record = self._state_store.read()
        except StateStoreError as error:
            raise RuntimeOperationError(
                f"could not inspect Mininet/OVS runtime state: {error}",
                code="runtime.state.failed",
                run_id=run_id,
            ) from error
        if record is None or record.run.id != run_id:
            self._raise_unknown_run(run_id)

        try:
            plan = self._plan_from_record(record)
            owner_active = (
                record.owner.is_alive() and self._state_store.is_locked()
            )
        except (StateStoreError, ValueError) as error:
            raise RuntimeOperationError(
                f"could not inspect Mininet/OVS runtime state: {error}",
                code="runtime.state.failed",
                run_id=run_id,
            ) from error
        if owner_active:
            info = record.run
            resource_state = (
                ResourceOperationalState.UP
                if info.state == RunState.RUNNING
                else ResourceOperationalState.UNKNOWN
            )
        else:
            info = record.run.model_copy(
                update={
                    "state": RunState.FAILED,
                    "issue": RuntimeIssue(
                        code="runtime.run.orphaned",
                        message="no process currently owns this run",
                    ),
                }
            )
            resource_state = ResourceOperationalState.UNKNOWN
        return RuntimeSnapshot(
            run=info,
            observed_at=self._clock(),
            resources=_live_resources(plan.resources, resource_state),
        )

    def _recover_persisted(self, run_id: str) -> TeardownResult:
        try:
            self._state_store.acquire()
        except StateLockHeld as error:
            existing = self._read_state_for_error()
            raise RuntimeOperationError(
                "cannot recover a Mininet/OVS run while its owner is active",
                code="runtime.run.active",
                run_id=existing.run.id if existing is not None else run_id,
            ) from error
        except StateStoreError as error:
            raise RuntimeOperationError(
                f"could not acquire Mininet/OVS runtime state: {error}",
                code="runtime.state.failed",
                run_id=run_id,
            ) from error

        try:
            record = self._state_store.read()
            if record is None or record.run.id != run_id:
                self._raise_unknown_run(run_id)
            plan = self._plan_from_record(record)
            self._recovery(plan, record.process_groups)
            self._state_store.clear()
        except RuntimeOperationError:
            raise
        except (StateStoreError, ValueError) as error:
            raise RuntimeOperationError(
                f"could not recover Mininet/OVS run {run_id!r}: {error}",
                code="runtime.recovery.failed",
                run_id=run_id,
            ) from error
        except Exception as error:
            raise RuntimeOperationError(
                f"could not recover Mininet/OVS run {run_id!r}: {error}",
                code="runtime.recovery.failed",
                run_id=run_id,
            ) from error
        finally:
            self._state_store.release()

        stopped = record.run.model_copy(
            update={
                "state": RunState.STOPPED,
                "stopped_at": self._clock(),
                "issue": None,
            }
        )
        return TeardownResult(
            run=stopped,
            released_resources=tuple(resource.name for resource in plan.resources),
        )

    @staticmethod
    def _plan_from_record(record: PersistedRun) -> DeploymentPlan:
        from mininet_ai.compiler.models import DeploymentPlan

        return DeploymentPlan.model_validate(record.plan)

    def _read_state_for_error(self) -> PersistedRun | None:
        try:
            return self._state_store.read()
        except StateStoreError:
            return None

    def _ensure_no_active_run(self) -> None:
        active = sorted(
            run.info.id
            for run in self._runs.values()
            if run.info.state != RunState.STOPPED
        )
        if active:
            raise RuntimeOperationError(
                f"Mininet/OVS runtime already has an active run: {active[0]!r}",
                code="runtime.run.active",
                run_id=active[0],
            )

    def _new_run_id(self) -> str:
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
        return run_id

    @staticmethod
    def _network_process_groups(network: Any) -> tuple[ProcessOwner, ...]:
        nodes = (
            *getattr(network, "controllers", ()),
            *getattr(network, "switches", ()),
            *getattr(network, "hosts", ()),
        )
        pids = sorted(
            {
                pid
                for node in nodes
                if isinstance((pid := getattr(node, "pid", None)), int)
                and pid > 0
            }
        )
        identities = []
        for pid in pids:
            try:
                identities.append(ProcessOwner.for_pid(pid))
            except (OSError, IndexError, ValueError):
                continue
        return tuple(identities)

    @staticmethod
    def _preflight_interfaces(plan: DeploymentPlan) -> None:
        names = (
            resource.name
            for resource in plan.resources
            if resource.kind in {ResourceKind.SWITCH, ResourceKind.PORT}
        )
        conflicts = sorted(name for name in names if _interface_exists(name))
        if conflicts:
            raise RuntimeOperationError(
                "refusing to replace existing network interfaces: "
                + ", ".join(conflicts),
                code="runtime.resource.conflict",
            )

    def _build_network(
        self, plan: DeploymentPlan, bindings: _MininetBindings
    ) -> Any:
        network = bindings.network_class(
            topo=None,
            controller=None,
            switch=bindings.ovs_switch_class,
            host=bindings.host_class,
            link=bindings.tc_link_class,
            build=False,
            autoSetMacs=False,
            autoStaticArp=False,
            waitConnected=False,
        )
        resources = {resource.name: resource for resource in plan.resources}

        for resource in plan.resources:
            if resource.kind == ResourceKind.CONTROLLER:
                controller = cast("PlannedController", resource)
                controller_class = (
                    bindings.ovs_controller_class
                    if controller.controller_type == ControllerType.BUILTIN
                    else bindings.remote_controller_class
                )
                network.addController(
                    controller.name,
                    controller=controller_class,
                    ip=controller.address or "127.0.0.1",
                    port=controller.port,
                    protocol=controller.protocol.value,
                )
            elif resource.kind == ResourceKind.SWITCH:
                switch = cast("PlannedSwitch", resource)
                network.addSwitch(
                    switch.name,
                    cls=bindings.ovs_switch_class,
                    failMode=switch.fail_mode.value,
                    datapath=(
                        "user"
                        if switch.datapath == SwitchDatapath.USERSPACE
                        else "kernel"
                    ),
                    protocols=",".join(item.value for item in switch.protocols)
                    or None,
                )
            elif resource.kind == ResourceKind.HOST:
                host = cast("PlannedHost", resource)
                network.addHost(
                    host.name,
                    cls=bindings.host_class,
                    ip=None,
                    mac=None,
                    defaultRoute=None,
                )

        for resource in plan.resources:
            if resource.kind != ResourceKind.LINK:
                continue
            link = cast("PlannedLink", resource)
            left = cast("PlannedPort", resources[link.endpoints[0]])
            right = cast("PlannedPort", resources[link.endpoints[1]])
            left_node = network.get(left.parent)
            right_node = network.get(right.parent)
            parameters: dict[str, Any] = {"loss": link.loss}
            if link.bandwidth is not None:
                parameters["bw"] = link.bandwidth
            if link.delay is not None:
                parameters["delay"] = link.delay
            if link.jitter is not None:
                parameters["jitter"] = link.jitter
            if link.max_queue_size is not None:
                parameters["max_queue_size"] = link.max_queue_size

            deployed_link = network.addLink(
                left_node,
                right_node,
                cls=bindings.tc_link_class,
                port1=left.number,
                port2=right.number,
                intfName1=left.name,
                intfName2=right.name,
                params1=self._port_parameters(left),
                params2=self._port_parameters(right),
                **parameters,
            )
            self._configure_port(deployed_link.intf1, left)
            self._configure_port(deployed_link.intf2, right)

        network.build()
        self._configure_default_routes(plan, network)
        return network

    def _start_network(self, plan: DeploymentPlan, network: Any) -> None:
        for controller in network.controllers:
            controller.start()
        for resource in plan.resources:
            if resource.kind != ResourceKind.SWITCH:
                continue
            switch = cast("PlannedSwitch", resource)
            controllers = [network.get(name) for name in switch.controllers]
            network.get(switch.name).start(controllers)
        if not network.waitConnected(
            timeout=self._connect_timeout_seconds, delay=0.1
        ):
            raise RuntimeError("one or more switches did not become ready")

    @staticmethod
    def _port_parameters(port: PlannedPort) -> dict[str, Any]:
        parameters: dict[str, Any] = {}
        if port.ipv4 is not None:
            parameters["ip"] = port.ipv4
        return parameters

    @staticmethod
    def _configure_port(interface: Any, port: PlannedPort) -> None:
        if port.mac is not None:
            output = interface.setMAC(port.mac)
            if output.strip():
                raise RuntimeError(
                    f"could not set MAC on {port.name!r}: {output.strip()}"
                )
        output = interface.ifconfig("mtu", str(port.mtu))
        if output.strip():
            raise RuntimeError(
                f"could not set MTU {port.mtu} on {port.name!r}: {output.strip()}"
            )

    @staticmethod
    def _configure_default_routes(plan: DeploymentPlan, network: Any) -> None:
        for resource in plan.resources:
            if resource.kind != ResourceKind.HOST:
                continue
            host = cast("PlannedHost", resource)
            if host.default_route is None:
                continue
            arguments = [
                "ip",
                "route",
                "replace",
                "default",
                *parse_default_route(host.default_route),
            ]
            output, error, status = network.get(host.name).pexec(arguments)
            if status != 0:
                detail = (error or output).strip()
                raise RuntimeError(
                    f"could not set default route on {host.name!r}: {detail}"
                )

    def _rollback(
        self, network: Any, plan: DeploymentPlan
    ) -> Exception | None:
        failure: Exception | None = None
        try:
            network.stop()
        except Exception as error:  # pragma: no cover - defensive reporting
            failure = error
        try:
            self._remove_owned_artifacts(plan)
        except Exception as error:  # pragma: no cover - defensive reporting
            failure = failure or error
        return failure

    @staticmethod
    def _remove_owned_artifacts(plan: DeploymentPlan) -> None:
        for resource in plan.resources:
            if resource.kind != ResourceKind.CONTROLLER:
                continue
            controller = cast("PlannedController", resource)
            if controller.controller_type == ControllerType.BUILTIN:
                (Path("/tmp") / f"{controller.name}.log").unlink(missing_ok=True)

    def _recover_owned_resources(
        self, plan: DeploymentPlan, process_groups: tuple[ProcessOwner, ...]
    ) -> None:
        node_names = {
            resource.name
            for resource in plan.resources
            if resource.kind
            in {ResourceKind.CONTROLLER, ResourceKind.SWITCH, ResourceKind.HOST}
        }
        recorded: set[int] = set()
        for process in process_groups:
            if not process.is_alive():
                continue
            try:
                recorded.add(os.getpgid(process.pid))
            except ProcessLookupError:
                continue
        discovered = self._discover_process_groups(node_names)
        for process_group in sorted(recorded | discovered):
            self._terminate_process_group(process_group)

        for resource in reversed(plan.resources):
            if resource.kind == ResourceKind.SWITCH:
                self._run_cleanup_command(
                    "ovs-vsctl",
                    "--timeout=5",
                    "--if-exists",
                    "del-br",
                    resource.name,
                )
        for resource in reversed(plan.resources):
            if resource.kind == ResourceKind.PORT and _interface_exists(
                resource.name
            ):
                self._run_cleanup_command(
                    "ip", "link", "delete", resource.name
                )
        self._remove_owned_artifacts(plan)

    @staticmethod
    def _discover_process_groups(node_names: set[str]) -> set[int]:
        markers = {f"mininet:{name}".encode() for name in node_names}
        process_groups: set[int] = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                arguments = set(
                    (entry / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
                )
                if not arguments.intersection(markers):
                    continue
                process_groups.add(os.getpgid(int(entry.name)))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
        return process_groups

    @staticmethod
    def _terminate_process_group(process_group: int) -> None:
        if process_group <= 0 or process_group == os.getpgrp():
            return
        try:
            os.killpg(process_group, signal.SIGHUP)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _run_cleanup_command(*arguments: str) -> None:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise RuntimeError(
                f"cleanup command {' '.join(arguments)!r} failed: {detail}"
            )

    def _get_run(self, run_id: str) -> _MininetRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            self._raise_unknown_run(run_id, cause=error)

    @staticmethod
    def _raise_unknown_run(
        run_id: str, *, cause: Exception | None = None
    ) -> None:
        error = RuntimeOperationError(
            f"unknown substrate run {run_id!r}",
            code="runtime.run.unknown",
            run_id=run_id,
        )
        if cause is None:
            raise error
        raise error from cause

    def _require_running(self, run_id: str) -> _MininetRun:
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
            raise RuntimeOperationError(
                f"run {run_id!r} has no resources named: {', '.join(unknown)}",
                code="runtime.resource.unknown",
                run_id=run_id,
            )
