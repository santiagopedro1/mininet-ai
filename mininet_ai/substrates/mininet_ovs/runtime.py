"""Live Mininet/OVS implementation of the substrate runtime contract."""

from __future__ import annotations

import os
import socket
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
from mininet_ai.substrates.mininet_ovs.driver import (
    MininetOVSDriver,
    parse_default_route,
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
    resources: tuple[LiveResource, ...]


def _interface_exists(name: str) -> bool:
    try:
        socket.if_nametoindex(name)
    except OSError:
        return False
    return True


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
    ) -> None:
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        self._clock = clock
        self._run_id_factory = run_id_factory
        self._bindings_factory = bindings_factory
        self._connect_timeout_seconds = connect_timeout_seconds
        self._runs: dict[str, _MininetRun] = {}

    def deploy(self, plan: DeploymentPlan) -> RunInfo:
        self._validate_plan(plan)
        self._ensure_no_active_run()
        run_id = self._new_run_id()
        started_at = self._clock()
        network: Any | None = None

        try:
            bindings = self._bindings_factory()
            self._preflight_interfaces(plan)
            network = self._build_network(plan, bindings)
            self._start_network(plan, network)
        except RuntimeOperationError:
            if network is not None:
                self._rollback(network, plan)
            raise
        except Exception as error:
            rollback_error = self._rollback(network, plan) if network else None
            detail = f": {error}"
            if rollback_error is not None:
                detail += f"; rollback also failed: {rollback_error}"
            raise RuntimeOperationError(
                f"could not deploy Mininet/OVS run {run_id!r}{detail}",
                code="runtime.deploy.failed",
                run_id=run_id,
            ) from error

        info = RunInfo(
            id=run_id,
            substrate=self.name,
            plan_digest=plan.digest,
            state=RunState.RUNNING,
            started_at=started_at,
        )
        self._runs[run_id] = _MininetRun(
            info=info,
            plan=plan,
            network=network,
            resources=_live_resources(plan.resources, ResourceOperationalState.UP),
        )
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
        resources = {resource.name: resource for resource in run.resources}
        self._require_targets(run_id, query.targets, set(resources))
        if query.name != "topology.resources":
            raise RuntimeOperationError(
                f"observation {query.name!r} is not implemented yet",
                code="runtime.observation.unsupported",
                run_id=run_id,
            )
        return ObservationResult(
            run_id=run_id,
            query=query,
            observed_at=self._clock(),
            values={
                target: resources[target].model_dump(mode="json")
                for target in query.targets
            },
        )

    def execute(self, run_id: str, request: ActionRequest) -> ActionResult:
        run = self._require_running(run_id)
        self._require_targets(
            run_id,
            (request.target,),
            {resource.name for resource in run.resources},
        )
        return ActionResult(
            run_id=run_id,
            request_id=request.id,
            status=ActionStatus.REJECTED,
            completed_at=self._clock(),
            issue=RuntimeIssue(
                code="runtime.action.unsupported",
                message=f"action {request.name!r} is not implemented yet",
                target=request.target,
            ),
        )

    def teardown(self, run_id: str) -> TeardownResult:
        run = self._get_run(run_id)
        if run.info.state == RunState.STOPPED:
            return TeardownResult(run=run.info, already_stopped=True)

        try:
            run.network.stop()
            self._remove_owned_artifacts(run.plan)
        except Exception as error:
            raise RuntimeOperationError(
                f"could not tear down Mininet/OVS run {run_id!r}: {error}",
                code="runtime.teardown.failed",
                run_id=run_id,
            ) from error

        run.info = run.info.model_copy(
            update={"state": RunState.STOPPED, "stopped_at": self._clock()}
        )
        released = tuple(resource.name for resource in run.resources)
        run.resources = tuple(
            resource.model_copy(update={"state": ResourceOperationalState.STOPPED})
            for resource in run.resources
        )
        return TeardownResult(run=run.info, released_resources=released)

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

    def _get_run(self, run_id: str) -> _MininetRun:
        try:
            return self._runs[run_id]
        except KeyError as error:
            raise RuntimeOperationError(
                f"unknown substrate run {run_id!r}",
                code="runtime.run.unknown",
                run_id=run_id,
            ) from error

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
