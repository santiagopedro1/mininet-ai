"""Typed mutation operations for live Mininet/OVS runs."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast

from mininet_ai.specification.models import ResourceKind
from mininet_ai.substrates.runtime import ActionRequest, ActionStatus

from .observations import CommandExecutor, LocalCommandExecutor

if TYPE_CHECKING:
    from mininet_ai.compiler.models import (
        DeploymentPlan,
        PlannedLink,
        PlannedResource,
        PlannedSwitch,
    )


_DURATION = re.compile(r"^(?:0|[0-9]+(?:\.[0-9]+)?)(?:us|ms|s)$")
_FLOW_TOKEN = re.compile(r"^[^\x00\r\n]+$")


@dataclass(frozen=True)
class ActionOutcome:
    """Successful provider result, independent of runtime identity and time."""

    changed: bool
    output: dict[str, Any]


class ActionExecutionError(Exception):
    """A typed rejection or execution failure returned as an action result."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status: ActionStatus = ActionStatus.REJECTED,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ActionProvider(Protocol):
    def execute(self, request: ActionRequest) -> ActionOutcome:
        """Validate and execute one substrate mutation."""
        ...

    def close(self) -> None:
        """Stop processes started through this provider."""
        ...

    def owned_pids(self) -> tuple[int, ...]:
        """Return live process leaders that recovery must own."""
        ...

    def release_pids(self, pids: tuple[int, ...]) -> None:
        """Stop newly owned processes after persistence fails."""
        ...


@dataclass
class _ManagedProcess:
    host: str
    process: Any


class MininetOVSActions:
    """Implement supported mutations behind one small runtime-facing interface."""

    def __init__(
        self,
        plan: DeploymentPlan,
        network: Any,
        *,
        executor: CommandExecutor | None = None,
    ) -> None:
        self._plan = plan
        self._network = network
        self._executor = executor or LocalCommandExecutor()
        self._resources = {resource.name: resource for resource in plan.resources}
        self._links = {
            resource.name: cast("PlannedLink", resource)
            for resource in plan.resources
            if resource.kind == ResourceKind.LINK
        }
        self._link_parameters = {
            link.name: self._planned_link_parameters(link)
            for link in self._links.values()
        }
        self._processes: dict[str, _ManagedProcess] = {}

    def execute(self, request: ActionRequest) -> ActionOutcome:
        handlers = {
            "link.enable": lambda: self._set_link_state(request, up=True),
            "link.disable": lambda: self._set_link_state(request, up=False),
            "link.configure": lambda: self._configure_link(request),
            "openflow.flow.install": lambda: self._install_flow(request),
            "openflow.flow.remove": lambda: self._remove_flow(request),
            "host.process.start": lambda: self._start_process(request),
            "host.process.stop": lambda: self._stop_process(request),
        }
        try:
            handler = handlers[request.name]
        except KeyError as error:
            raise ActionExecutionError(
                f"action {request.name!r} is not implemented",
                code="runtime.action.unsupported",
            ) from error
        return handler()

    def close(self) -> None:
        for process_id in tuple(self._processes):
            managed = self._processes[process_id]
            if managed.process.poll() is None:
                self._terminate(managed.process, timeout_seconds=2)
        self._processes.clear()

    def owned_pids(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                managed.process.pid
                for managed in self._processes.values()
                if managed.process.poll() is None
            )
        )

    def release_pids(self, pids: tuple[int, ...]) -> None:
        requested = set(pids)
        for process_id, managed in tuple(self._processes.items()):
            if managed.process.pid not in requested:
                continue
            if managed.process.poll() is None:
                self._terminate(managed.process, timeout_seconds=2)
            del self._processes[process_id]

    def _set_link_state(
        self, request: ActionRequest, *, up: bool
    ) -> ActionOutcome:
        self._require_no_parameters(request)
        link = self._require_kind(request.target, ResourceKind.LINK)
        interfaces = self._link_interfaces(cast("PlannedLink", link))
        states = [self._interface_is_up(name, node) for name, node, _ in interfaces]
        desired = "up" if up else "down"
        changed_indexes = [
            index for index, state in enumerate(states) if state is not up
        ]
        completed: list[int] = []
        try:
            for index in changed_indexes:
                name, node, _ = interfaces[index]
                self._run(
                    ["ip", "link", "set", "dev", name, desired],
                    node=node,
                    timeout_seconds=request.timeout_seconds,
                )
                completed.append(index)
        except ActionExecutionError:
            rollback = "down" if up else "up"
            for index in reversed(completed):
                name, node, _ = interfaces[index]
                try:
                    self._run(
                        ["ip", "link", "set", "dev", name, rollback],
                        node=node,
                        timeout_seconds=request.timeout_seconds,
                    )
                except ActionExecutionError:
                    pass
            raise
        return ActionOutcome(
            changed=bool(changed_indexes),
            output={
                "link": request.target,
                "state": desired,
                "interfaces": [item[0] for item in interfaces],
            },
        )

    def _configure_link(self, request: ActionRequest) -> ActionOutcome:
        link = cast(
            "PlannedLink", self._require_kind(request.target, ResourceKind.LINK)
        )
        updates = self._validate_link_parameters(request.parameters)
        previous = dict(self._link_parameters[link.name])
        current = {**previous, **updates}
        if current.get("jitter") is not None and current.get("delay") is None:
            self._reject("link jitter requires a base delay")
        if current == previous:
            return ActionOutcome(
                changed=False,
                output={"link": link.name, "parameters": current},
            )

        interfaces = self._link_interfaces(link)
        configured: list[Any] = []
        try:
            for _, _, interface in interfaces:
                interface.config(**self._mininet_link_parameters(current))
                configured.append(interface)
        except Exception as error:
            for interface in reversed(configured):
                try:
                    interface.config(**self._mininet_link_parameters(previous))
                except Exception:
                    pass
            raise ActionExecutionError(
                f"could not configure link {link.name!r}: {error}",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            ) from error
        self._link_parameters[link.name] = current
        return ActionOutcome(
            changed=True,
            output={"link": link.name, "parameters": current},
        )

    def _install_flow(self, request: ActionRequest) -> ActionOutcome:
        switch = cast(
            "PlannedSwitch",
            self._require_kind(request.target, ResourceKind.SWITCH),
        )
        flow = self._flow_expression(request.parameters, require_actions=True)
        command = self._ofctl_command(switch, "add-flow", flow)
        self._run(command, timeout_seconds=request.timeout_seconds)
        return ActionOutcome(
            changed=True,
            output={"switch": switch.name, "flow": flow},
        )

    def _remove_flow(self, request: ActionRequest) -> ActionOutcome:
        switch = cast(
            "PlannedSwitch",
            self._require_kind(request.target, ResourceKind.SWITCH),
        )
        parameters = dict(request.parameters)
        strict = parameters.pop("strict", False)
        if not isinstance(strict, bool):
            self._reject("strict must be a boolean")
        selector = self._flow_expression(parameters, require_actions=False)
        command = self._ofctl_command(switch, "del-flows", selector)
        if strict:
            command.insert(command.index("del-flows"), "--strict")
        self._run(command, timeout_seconds=request.timeout_seconds)
        return ActionOutcome(
            changed=True,
            output={"switch": switch.name, "selector": selector, "strict": strict},
        )

    def _start_process(self, request: ActionRequest) -> ActionOutcome:
        self._require_kind(request.target, ResourceKind.HOST)
        unknown = set(request.parameters) - {"command", "arguments"}
        if unknown:
            self._reject(f"unknown process parameters: {', '.join(sorted(unknown))}")
        command = request.parameters.get("command")
        arguments = request.parameters.get("arguments", [])
        if not isinstance(command, str) or not command or "\x00" in command:
            self._reject("command must be a non-empty string")
        if (
            not isinstance(arguments, list)
            or any(
                not isinstance(argument, str) or "\x00" in argument
                for argument in arguments
            )
        ):
            self._reject("arguments must be a list of strings")
        if request.id in self._processes:
            self._reject(f"process id {request.id!r} is already in use")

        node = self._node(request.target)
        try:
            process = node.popen(
                [command, *arguments],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as error:
            raise ActionExecutionError(
                f"could not start process on host {request.target!r}: {error}",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            ) from error
        if process.poll() is not None:
            raise ActionExecutionError(
                f"process exited immediately with status {process.returncode}",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            )
        self._processes[request.id] = _ManagedProcess(request.target, process)
        return ActionOutcome(
            changed=True,
            output={
                "processId": request.id,
                "host": request.target,
                "pid": process.pid,
                "command": [command, *arguments],
            },
        )

    def _stop_process(self, request: ActionRequest) -> ActionOutcome:
        self._require_kind(request.target, ResourceKind.HOST)
        unknown = set(request.parameters) - {"processId"}
        if unknown:
            self._reject(f"unknown process parameters: {', '.join(sorted(unknown))}")
        process_id = request.parameters.get("processId")
        if not isinstance(process_id, str) or not process_id:
            self._reject("processId must be a non-empty string")
        try:
            managed = self._processes[process_id]
        except KeyError as error:
            raise ActionExecutionError(
                f"unknown managed process {process_id!r}",
                code="runtime.action.process-unknown",
            ) from error
        if managed.host != request.target:
            self._reject(
                f"process {process_id!r} belongs to host {managed.host!r}"
            )
        running = managed.process.poll() is None
        if running:
            self._terminate(
                managed.process,
                timeout_seconds=request.timeout_seconds,
            )
        return ActionOutcome(
            changed=running,
            output={
                "processId": process_id,
                "host": request.target,
                "returnCode": managed.process.returncode,
            },
        )

    def _terminate(self, process: Any, *, timeout_seconds: float) -> None:
        process_group: int | None = None
        try:
            try:
                process_group = os.getpgid(process.pid)
            except ProcessLookupError:
                process.wait(timeout=timeout_seconds)
                return
            if process_group == os.getpgrp():
                process.terminate()
                process.wait(timeout=timeout_seconds)
                return

            os.killpg(process_group, signal.SIGTERM)
            deadline = time.monotonic() + timeout_seconds
            while self._process_group_exists(process_group):
                process.poll()
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            if not self._process_group_exists(process_group):
                process.poll()
                return

            os.killpg(process_group, signal.SIGKILL)
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                raise ActionExecutionError(
                    f"process group {process_group} did not stop before the timeout",
                    code="runtime.action.timeout",
                    status=ActionStatus.FAILED,
                ) from error
            kill_deadline = time.monotonic() + timeout_seconds
            while self._process_group_exists(process_group):
                if time.monotonic() >= kill_deadline:
                    raise ActionExecutionError(
                        f"process group {process_group} did not stop before "
                        "the timeout",
                        code="runtime.action.timeout",
                        status=ActionStatus.FAILED,
                    )
                time.sleep(
                    min(0.05, max(0, kill_deadline - time.monotonic()))
                )
        except ActionExecutionError:
            raise
        except subprocess.TimeoutExpired:
            if process_group is None or process_group == os.getpgrp():
                process.kill()
            else:
                os.killpg(process_group, signal.SIGKILL)
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired as error:
                raise ActionExecutionError(
                    f"process {process.pid} did not stop before the timeout",
                    code="runtime.action.timeout",
                    status=ActionStatus.FAILED,
                ) from error
        except Exception as error:
            raise ActionExecutionError(
                f"could not stop process {process.pid}: {error}",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            ) from error

    @staticmethod
    def _process_group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _flow_expression(
        self, parameters: Mapping[str, Any], *, require_actions: bool
    ) -> str:
        allowed = {
            "match",
            "actions",
            "cookie",
            "cookieMask",
            "table",
            "priority",
            "idleTimeout",
            "hardTimeout",
        }
        if not require_actions:
            allowed -= {"actions", "idleTimeout", "hardTimeout"}
        unknown = set(parameters) - allowed
        if unknown:
            self._reject(f"unknown flow parameters: {', '.join(sorted(unknown))}")
        if "match" not in parameters:
            self._reject("match is required")

        fields: list[str] = []
        cookie = parameters.get("cookie")
        cookie_mask = parameters.get("cookieMask")
        if cookie_mask is not None and cookie is None:
            self._reject("cookieMask requires cookie")
        if cookie is not None:
            cookie_value = self._flow_integer("cookie", cookie, maximum=2**64 - 1)
            cookie_field = f"cookie={cookie_value:#x}"
            if cookie_mask is not None:
                mask = self._flow_integer(
                    "cookieMask", cookie_mask, maximum=2**64 - 1
                )
                cookie_field += f"/{mask:#x}"
            fields.append(cookie_field)
        integer_fields = (
            ("table", "table", 254),
            ("priority", "priority", 65_535),
            ("idleTimeout", "idle_timeout", 65_535),
            ("hardTimeout", "hard_timeout", 65_535),
        )
        for parameter, field, maximum in integer_fields:
            if parameter in parameters:
                value = self._flow_integer(parameter, parameters[parameter], maximum)
                fields.append(f"{field}={value}")
        match = parameters["match"]
        if isinstance(match, Mapping):
            for name, value in sorted(match.items()):
                if not isinstance(name, str) or not name:
                    self._reject("match keys must be non-empty strings")
                token = name if value is True else f"{name}={value}"
                self._require_flow_token(token, "match")
                fields.append(token)
        elif isinstance(match, str):
            if match:
                self._require_flow_token(match, "match")
                fields.append(match)
        else:
            self._reject("match must be a string or object")

        if require_actions:
            actions = parameters.get("actions")
            if not isinstance(actions, str) or not actions:
                self._reject("actions must be a non-empty string")
            self._require_flow_token(actions, "actions")
            fields.append(f"actions={actions}")
        return ",".join(fields)

    def _ofctl_command(
        self, switch: PlannedSwitch, operation: str, flow: str
    ) -> list[str]:
        command = ["ovs-ofctl"]
        if switch.protocols:
            command.extend(["-O", switch.protocols[-1].value])
        command.extend([operation, switch.name, flow])
        return command

    def _validate_link_parameters(
        self, parameters: Mapping[str, Any]
    ) -> dict[str, Any]:
        allowed = {"bandwidth", "delay", "jitter", "loss", "maxQueueSize"}
        unknown = set(parameters) - allowed
        if unknown:
            self._reject(f"unknown link parameters: {', '.join(sorted(unknown))}")
        if not parameters:
            self._reject("at least one link parameter is required")
        result = dict(parameters)
        if "bandwidth" in result:
            bandwidth = result["bandwidth"]
            if (
                isinstance(bandwidth, bool)
                or not isinstance(bandwidth, (int, float))
                or not 0 < bandwidth <= 1_000
            ):
                self._reject("bandwidth must be greater than 0 and at most 1000 Mbps")
        if "loss" in result:
            loss = result["loss"]
            if (
                isinstance(loss, bool)
                or not isinstance(loss, (int, float))
                or not 0 <= loss <= 100
            ):
                self._reject("loss must be between 0 and 100 percent")
        if "maxQueueSize" in result:
            queue_size = result["maxQueueSize"]
            if (
                isinstance(queue_size, bool)
                or not isinstance(queue_size, int)
                or queue_size <= 0
            ):
                self._reject("maxQueueSize must be a positive integer")
        for name in ("delay", "jitter"):
            if name in result:
                value = result[name]
                if value is not None and (
                    not isinstance(value, str) or _DURATION.fullmatch(value) is None
                ):
                    self._reject(f"{name} must be null or a duration such as '2ms'")
        return result

    @staticmethod
    def _planned_link_parameters(link: PlannedLink) -> dict[str, Any]:
        return {
            "bandwidth": link.bandwidth,
            "delay": link.delay,
            "jitter": link.jitter,
            "loss": link.loss,
            "maxQueueSize": link.max_queue_size,
        }

    @staticmethod
    def _mininet_link_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "bw": parameters["bandwidth"],
            "delay": parameters["delay"],
            "jitter": parameters["jitter"],
            "loss": parameters["loss"],
            "max_queue_size": parameters["maxQueueSize"],
        }

    def _link_interfaces(
        self, link: PlannedLink
    ) -> list[tuple[str, Any, Any]]:
        by_name = {
            interface.name: interface
            for live_link in self._network.links
            for interface in (live_link.intf1, live_link.intf2)
        }
        result = []
        for port_name in link.endpoints:
            port = self._resources[port_name]
            try:
                interface = by_name[port_name]
            except KeyError as error:
                raise ActionExecutionError(
                    f"live interface {port_name!r} is unavailable",
                    code="runtime.action.failed",
                    status=ActionStatus.FAILED,
                ) from error
            result.append((port_name, self._node(port.parent or ""), interface))
        return result

    def _interface_is_up(self, name: str, node: Any) -> bool:
        result = self._run(["ip", "-j", "link", "show", "dev", name], node=node)
        try:
            value = json.loads(result.stdout)
            flags = value[0]["flags"]
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as error:
            raise ActionExecutionError(
                f"could not parse state for interface {name!r}",
                code="runtime.action.invalid-output",
                status=ActionStatus.FAILED,
            ) from error
        return "UP" in flags

    def _run(
        self,
        arguments: Sequence[str],
        *,
        node: Any | None = None,
        timeout_seconds: float | None = None,
    ):
        try:
            result = self._executor.run(
                arguments,
                node=node,
                timeout_seconds=timeout_seconds or 10,
            )
        except subprocess.TimeoutExpired as error:
            raise ActionExecutionError(
                f"command timed out: {arguments[0]}",
                code="runtime.action.timeout",
                status=ActionStatus.FAILED,
            ) from error
        except Exception as error:
            raise ActionExecutionError(
                f"could not execute {arguments[0]!r}: {error}",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            ) from error
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise ActionExecutionError(
                f"command {arguments[0]!r} failed: {detail}",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            )
        return result

    def _require_kind(self, name: str, kind: ResourceKind) -> PlannedResource:
        resource = self._resources[name]
        if resource.kind != kind:
            self._reject(
                f"action target {name!r} must be a {kind.value}, not "
                f"{resource.kind.value}"
            )
        return resource

    def _node(self, name: str) -> Any:
        try:
            return self._network.get(name)
        except Exception as error:
            raise ActionExecutionError(
                f"live node {name!r} is unavailable",
                code="runtime.action.failed",
                status=ActionStatus.FAILED,
            ) from error

    def _require_no_parameters(self, request: ActionRequest) -> None:
        if request.parameters:
            self._reject(f"action {request.name!r} does not accept parameters")

    @staticmethod
    def _flow_integer(name: str, value: Any, maximum: int) -> int:
        if isinstance(value, bool):
            MininetOVSActions._reject(f"{name} must be an integer")
        if isinstance(value, str):
            try:
                value = int(value, 0)
            except ValueError:
                MininetOVSActions._reject(f"{name} must be an integer")
        if not isinstance(value, int) or not 0 <= value <= maximum:
            MininetOVSActions._reject(
                f"{name} must be between 0 and {maximum}"
            )
        return value

    @staticmethod
    def _require_flow_token(value: str, name: str) -> None:
        if _FLOW_TOKEN.fullmatch(value) is None:
            MininetOVSActions._reject(
                f"{name} must not contain NUL or newline characters"
            )

    @staticmethod
    def _reject(message: str) -> NoReturn:
        raise ActionExecutionError(
            message,
            code="runtime.action.invalid-parameters",
        )
