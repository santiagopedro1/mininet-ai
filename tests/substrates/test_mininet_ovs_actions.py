from __future__ import annotations

import json
import os
import signal
import subprocess
import unittest
from typing import Any
from unittest.mock import call, patch

from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import ActionRequest, ActionStatus
from mininet_ai.substrates.mininet_ovs.actions import (
    ActionExecutionError,
    MininetOVSActions,
)
from mininet_ai.substrates.mininet_ovs.observations import CommandResult
from tests.compiler.helpers import example_snapshot, experiment_from


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.interface_up = {
            "h1-eth0": True,
            "s1-eth1": True,
            "s1-eth2": True,
            "s2-eth1": True,
            "s2-eth2": True,
            "h2-eth0": True,
        }
        self.fail_interface: str | None = None

    def run(
        self, arguments, *, node=None, timeout_seconds: float = 10
    ) -> CommandResult:
        del timeout_seconds
        command = list(arguments)
        node_name = getattr(node, "name", None)
        self.calls.append((command, node_name))
        if command[:5] == ["ip", "-j", "link", "show", "dev"]:
            name = command[5]
            flags = ["UP"] if self.interface_up[name] else []
            return CommandResult(stdout=json.dumps([{"flags": flags}]))
        if command[:4] == ["ip", "link", "set", "dev"]:
            name = command[4]
            if name == self.fail_interface:
                return CommandResult(stderr="scripted failure", returncode=1)
            self.interface_up[name] = command[5] == "up"
        return CommandResult()


class RecordingInterface:
    def __init__(self, name: str) -> None:
        self.name = name
        self.config_calls: list[dict[str, Any]] = []

    def config(self, **parameters: Any) -> dict[str, Any]:
        self.config_calls.append(parameters)
        return parameters


class RecordingLink:
    def __init__(self, left: str, right: str) -> None:
        self.intf1 = RecordingInterface(left)
        self.intf2 = RecordingInterface(right)


class RecordingProcess:
    def __init__(self, pid: int = 1234) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float):
        del timeout
        if self.returncode is None:
            raise subprocess.TimeoutExpired("process", 1)
        return self.returncode


class RecordingNode:
    def __init__(self, name: str) -> None:
        self.name = name
        self.popen_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.processes: list[RecordingProcess] = []

    def popen(self, arguments: list[str], **parameters: Any) -> RecordingProcess:
        self.popen_calls.append((arguments, parameters))
        process = RecordingProcess(pid=1234 + len(self.processes))
        self.processes.append(process)
        return process


class RecordingNetwork:
    def __init__(self, plan) -> None:
        self.nodes = {
            resource.name: RecordingNode(resource.name)
            for resource in plan.resources
            if resource.kind.value in {"host", "switch"}
        }
        links = [
            resource for resource in plan.resources if resource.kind.value == "link"
        ]
        self.links = [RecordingLink(*link.endpoints) for link in links]

    def get(self, name: str) -> RecordingNode:
        return self.nodes[name]


def mininet_plan():
    snapshot = example_snapshot()
    snapshot["substrate"]["driver"] = "mininet-ovs"
    return compile_experiment(experiment_from(snapshot))


class MininetOVSActionsTests(unittest.TestCase):
    def setUp(self) -> None:
        process_group = patch(
            "mininet_ai.substrates.mininet_ovs.actions.os.getpgid",
            return_value=os.getpgrp(),
        )
        process_group.start()
        self.addCleanup(process_group.stop)
        self.plan = mininet_plan()
        self.network = RecordingNetwork(self.plan)
        self.executor = RecordingExecutor()
        self.actions = MininetOVSActions(
            self.plan,
            self.network,
            executor=self.executor,
        )

    def execute(
        self,
        name: str,
        target: str,
        parameters: dict[str, Any] | None = None,
        request_id: str = "request-1",
    ):
        return self.actions.execute(
            ActionRequest(
                id=request_id,
                name=name,
                target=target,
                parameters=parameters or {},
            )
        )

    def test_link_state_actions_are_idempotent_and_change_both_endpoints(self) -> None:
        first = self.execute("link.disable", "h1-s1")
        second = self.execute("link.disable", "h1-s1")
        enabled = self.execute("link.enable", "h1-s1")

        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertTrue(enabled.changed)
        mutations = [
            call
            for call in self.executor.calls
            if call[0][:4] == ["ip", "link", "set", "dev"]
        ]
        self.assertEqual(
            [(call[0][4], call[0][5]) for call in mutations],
            [
                ("h1-eth0", "down"),
                ("s1-eth1", "down"),
                ("h1-eth0", "up"),
                ("s1-eth1", "up"),
            ],
        )

    def test_link_state_failure_rolls_back_changed_endpoint(self) -> None:
        self.executor.fail_interface = "s1-eth1"

        with self.assertRaises(ActionExecutionError) as context:
            self.execute("link.disable", "h1-s1")

        self.assertEqual(context.exception.status, ActionStatus.FAILED)
        self.assertTrue(self.executor.interface_up["h1-eth0"])

    def test_link_configuration_updates_both_tc_interfaces(self) -> None:
        result = self.execute(
            "link.configure",
            "h1-s1",
            {"bandwidth": 50, "delay": "3ms", "loss": 1.5},
        )
        repeated = self.execute(
            "link.configure",
            "h1-s1",
            {"bandwidth": 50, "delay": "3ms", "loss": 1.5},
        )

        self.assertTrue(result.changed)
        self.assertFalse(repeated.changed)
        link = self.network.links[0]
        for interface in (link.intf1, link.intf2):
            self.assertEqual(len(interface.config_calls), 1)
            self.assertEqual(interface.config_calls[0]["bw"], 50)
            self.assertEqual(interface.config_calls[0]["delay"], "3ms")
            self.assertEqual(interface.config_calls[0]["loss"], 1.5)

    def test_openflow_actions_build_argument_lists_from_typed_fields(self) -> None:
        installed = self.execute(
            "openflow.flow.install",
            "s1",
            {
                "cookie": "0x10",
                "table": 0,
                "priority": 100,
                "match": {"in_port": 1, "ip": True},
                "actions": "output:2",
            },
        )
        removed = self.execute(
            "openflow.flow.remove",
            "s1",
            {
                "cookie": "0x10",
                "cookieMask": "0xffffffffffffffff",
                "table": 0,
                "priority": 100,
                "match": {"in_port": 1, "ip": True},
                "strict": True,
            },
        )

        self.assertTrue(installed.changed)
        self.assertTrue(removed.changed)
        self.assertEqual(
            self.executor.calls[-2][0],
            [
                "ovs-ofctl",
                "-O",
                "OpenFlow13",
                "add-flow",
                "s1",
                "cookie=0x10,table=0,priority=100,in_port=1,ip,actions=output:2",
            ],
        )
        self.assertEqual(self.executor.calls[-1][0][3:5], ["--strict", "del-flows"])

    def test_host_processes_are_managed_by_request_id(self) -> None:
        started = self.execute(
            "host.process.start",
            "h1",
            {"command": "sleep", "arguments": ["30"]},
            request_id="traffic-1",
        )
        stopped = self.execute(
            "host.process.stop",
            "h1",
            {"processId": "traffic-1"},
            request_id="stop-traffic",
        )

        self.assertEqual(started.output["pid"], 1234)
        self.assertTrue(stopped.changed)
        self.assertEqual(stopped.output["returnCode"], -15)
        self.assertTrue(self.network.get("h1").processes[0].terminated)

    def test_host_process_stop_signals_the_managed_process_group(self) -> None:
        self.execute(
            "host.process.start",
            "h1",
            {"command": "sleep", "arguments": ["30"]},
            request_id="tree",
        )
        process = self.network.get("h1").processes[0]

        group_running = True

        def mark_stopped(group: int, number: int) -> None:
            nonlocal group_running
            self.assertEqual(group, 4321)
            if number == signal.SIGTERM:
                process.returncode = -15
                group_running = False
            elif number == 0 and not group_running:
                raise ProcessLookupError

        with (
            patch(
                "mininet_ai.substrates.mininet_ovs.actions.os.getpgid",
                return_value=4321,
            ),
            patch(
                "mininet_ai.substrates.mininet_ovs.actions.os.killpg",
                side_effect=mark_stopped,
            ) as kill_group,
        ):
            stopped = self.execute(
                "host.process.stop",
                "h1",
                {"processId": "tree"},
                request_id="stop-tree",
            )

        self.assertTrue(stopped.changed)
        kill_group.assert_any_call(4321, signal.SIGTERM)
        self.assertNotIn(
            call(4321, signal.SIGKILL),
            kill_group.call_args_list,
        )

    def test_host_process_stop_kills_descendants_that_ignore_sigterm(self) -> None:
        self.execute(
            "host.process.start",
            "h1",
            {"command": "sleep", "arguments": ["30"]},
            request_id="tree",
        )
        process = self.network.get("h1").processes[0]
        group_running = True

        def signal_group(group: int, number: int) -> None:
            nonlocal group_running
            self.assertEqual(group, 4321)
            if number == signal.SIGTERM:
                process.returncode = -15
            elif number == signal.SIGKILL:
                group_running = False
            elif number == 0 and not group_running:
                raise ProcessLookupError

        with (
            patch(
                "mininet_ai.substrates.mininet_ovs.actions.os.getpgid",
                return_value=4321,
            ),
            patch(
                "mininet_ai.substrates.mininet_ovs.actions.os.killpg",
                side_effect=signal_group,
            ) as kill_group,
            patch(
                "mininet_ai.substrates.mininet_ovs.actions.time.monotonic",
                side_effect=[0, 31, 31],
            ),
        ):
            self.execute(
                "host.process.stop",
                "h1",
                {"processId": "tree"},
                request_id="stop-tree",
            )

        kill_group.assert_any_call(4321, signal.SIGTERM)
        kill_group.assert_any_call(4321, signal.SIGKILL)

    def test_close_stops_every_running_managed_process(self) -> None:
        self.execute(
            "host.process.start",
            "h1",
            {"command": "sleep", "arguments": ["30"]},
            request_id="one",
        )
        self.execute(
            "host.process.start",
            "h2",
            {"command": "sleep", "arguments": ["30"]},
            request_id="two",
        )

        self.actions.close()

        self.assertTrue(self.network.get("h1").processes[0].terminated)
        self.assertTrue(self.network.get("h2").processes[0].terminated)

    def test_invalid_target_parameters_and_action_have_typed_rejections(self) -> None:
        requests = (
            ("link.disable", "s1", {}),
            ("link.configure", "h1-s1", {"loss": 101}),
            ("openflow.flow.install", "s1", {"match": {}}),
            ("host.process.stop", "h1", {"processId": "missing"}),
            ("not.supported", "s1", {}),
        )

        for name, target, parameters in requests:
            with self.subTest(name=name, parameters=parameters):
                with self.assertRaises(ActionExecutionError) as context:
                    self.execute(name, target, parameters)
                self.assertEqual(context.exception.status, ActionStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()
