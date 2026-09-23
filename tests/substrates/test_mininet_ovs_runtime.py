from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from mininet_ai.compiler import compile_experiment
from mininet_ai.errors import RuntimeOperationError
from mininet_ai.substrates import (
    ActionRequest,
    ActionStatus,
    MininetOVSRuntime,
    ResourceOperationalState,
    RunState,
    create_substrate_runtime,
)
from mininet_ai.substrates.mininet_ovs.runtime import _MininetBindings
from mininet_ai.substrates.mininet_ovs.state import ProcessOwner, RunStateStore
from tests.compiler.helpers import example_snapshot, experiment_from
from tests.substrates.runtime_contract import SubstrateRuntimeContract


class IncrementingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(seconds=1)
        return result


class RecordingNode:
    def __init__(self, name: str, **parameters: Any) -> None:
        self.name = name
        self.parameters = parameters
        self.start_calls: list[Any] = []
        self.pexec_calls: list[list[str]] = []

    def start(self, controllers: Any = None) -> None:
        self.start_calls.append(controllers)

    def pexec(self, arguments: list[str]) -> tuple[str, str, int]:
        self.pexec_calls.append(arguments)
        return "", "", 0


class RecordingInterface:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ifconfig_calls: list[tuple[str, str]] = []
        self.mac_calls: list[str] = []

    def ifconfig(self, command: str, value: str) -> str:
        self.ifconfig_calls.append((command, value))
        return ""

    def setMAC(self, value: str) -> str:
        self.mac_calls.append(value)
        return ""


class RecordingLink:
    def __init__(self, left: str, right: str) -> None:
        self.intf1 = RecordingInterface(left)
        self.intf2 = RecordingInterface(right)


class RecordingNetwork:
    instances: list[RecordingNetwork] = []
    wait_result = True

    def __init__(self, **parameters: Any) -> None:
        self.parameters = parameters
        self.controllers: list[RecordingNode] = []
        self.switches: list[RecordingNode] = []
        self.hosts: list[RecordingNode] = []
        self.links: list[RecordingLink] = []
        self.nodes: dict[str, RecordingNode] = {}
        self.link_calls: list[dict[str, Any]] = []
        self.wait_calls: list[tuple[float, float]] = []
        self.built = False
        self.stopped = False
        type(self).instances.append(self)

    def addController(
        self, name: str, controller: type, **parameters: Any
    ) -> RecordingNode:
        node = RecordingNode(name, controller=controller, **parameters)
        self.controllers.append(node)
        self.nodes[name] = node
        return node

    def addSwitch(self, name: str, **parameters: Any) -> RecordingNode:
        node = RecordingNode(name, **parameters)
        self.switches.append(node)
        self.nodes[name] = node
        return node

    def addHost(self, name: str, **parameters: Any) -> RecordingNode:
        node = RecordingNode(name, **parameters)
        self.hosts.append(node)
        self.nodes[name] = node
        return node

    def addLink(
        self, left: RecordingNode, right: RecordingNode, **parameters: Any
    ) -> RecordingLink:
        link = RecordingLink(parameters["intfName1"], parameters["intfName2"])
        self.links.append(link)
        self.link_calls.append(
            {"left": left.name, "right": right.name, **parameters}
        )
        return link

    def get(self, name: str) -> RecordingNode:
        return self.nodes[name]

    def build(self) -> None:
        self.built = True

    def waitConnected(self, timeout: float, delay: float) -> bool:
        self.wait_calls.append((timeout, delay))
        return self.wait_result

    def stop(self) -> None:
        self.stopped = True


class UnhealthyRecordingNetwork(RecordingNetwork):
    wait_result = False


def bindings(network_class: type = RecordingNetwork) -> _MininetBindings:
    return _MininetBindings(
        network_class=network_class,
        host_class=type("Host", (), {}),
        ovs_controller_class=type("OVSController", (), {}),
        ovs_switch_class=type("OVSSwitch", (), {}),
        remote_controller_class=type("RemoteController", (), {}),
        tc_link_class=type("TCLink", (), {}),
    )


def mininet_plan():
    snapshot = example_snapshot()
    snapshot["substrate"]["driver"] = "mininet-ovs"
    return compile_experiment(experiment_from(snapshot))


def temporary_store(test_case: unittest.TestCase) -> RunStateStore:
    temporary = TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    root = Path(temporary.name)
    return RunStateStore(root / "state", root / "runtime.lock")


def recording_runtime(
    test_case: unittest.TestCase,
    network_class: type = RecordingNetwork,
    *,
    state_store: RunStateStore | None = None,
    owner: ProcessOwner | None = None,
    recovery=None,
) -> MininetOVSRuntime:
    if state_store is None:
        state_store = temporary_store(test_case)
    return MininetOVSRuntime(
        clock=IncrementingClock(),
        run_id_factory=lambda: "mininet-test-run",
        bindings_factory=lambda: bindings(network_class),
        state_store=state_store,
        owner=owner,
        recovery=recovery,
    )


class MininetRuntimeContractTests(SubstrateRuntimeContract, unittest.TestCase):
    def make_runtime(self) -> MininetOVSRuntime:
        return recording_runtime(self)

    def make_plan(self):
        return mininet_plan()


class MininetOVSRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingNetwork.instances.clear()
        UnhealthyRecordingNetwork.instances.clear()
        self.plan = mininet_plan()

    def test_registry_constructs_runtime_without_importing_mininet(self) -> None:
        runtime = create_substrate_runtime("mininet-ovs")

        self.assertIsInstance(runtime, MininetOVSRuntime)

    def test_deploy_translates_the_complete_plan_deterministically(self) -> None:
        runtime = recording_runtime(self)

        runtime.deploy(self.plan)
        network = RecordingNetwork.instances[-1]

        self.assertTrue(network.built)
        self.assertEqual([node.name for node in network.controllers], ["c0"])
        self.assertEqual([node.name for node in network.switches], ["s1", "s2"])
        self.assertEqual([node.name for node in network.hosts], ["h1", "h2"])
        self.assertEqual(len(network.links), 3)
        self.assertEqual(network.wait_calls, [(5, 0.1)])

        controller = network.get("c0")
        self.assertEqual(controller.parameters["port"], 6653)
        self.assertEqual(controller.parameters["protocol"], "tcp")
        self.assertEqual(controller.start_calls, [None])

        switch = network.get("s1")
        self.assertEqual(switch.parameters["failMode"], "secure")
        self.assertEqual(switch.parameters["datapath"], "kernel")
        self.assertEqual(switch.parameters["protocols"], "OpenFlow13")
        self.assertEqual(switch.start_calls, [[controller]])

        host_link = next(
            call
            for call in network.link_calls
            if call["intfName1"] == "h1-eth0"
        )
        self.assertEqual((host_link["left"], host_link["right"]), ("h1", "s1"))
        self.assertEqual((host_link["port1"], host_link["port2"]), (0, 1))
        self.assertEqual(host_link["params1"]["ip"], "10.0.0.1/24")
        self.assertEqual(host_link["bw"], 100)
        self.assertEqual(host_link["delay"], "2ms")

        host_interface = next(
            link.intf1 for link in network.links if link.intf1.name == "h1-eth0"
        )
        self.assertEqual(host_interface.mac_calls, ["02:00:00:00:00:01"])

        mtu_calls = [
            interface.ifconfig_calls
            for link in network.links
            for interface in (link.intf1, link.intf2)
        ]
        self.assertTrue(all(calls == [("mtu", "1500")] for calls in mtu_calls))

    def test_failed_health_check_rolls_back_all_created_resources(self) -> None:
        runtime = recording_runtime(self, UnhealthyRecordingNetwork)

        with self.assertRaises(RuntimeOperationError) as context:
            runtime.deploy(self.plan)

        self.assertEqual(context.exception.code, "runtime.deploy.failed")
        self.assertTrue(UnhealthyRecordingNetwork.instances[-1].stopped)
        with self.assertRaises(RuntimeOperationError) as missing:
            runtime.inspect("mininet-test-run")
        self.assertEqual(missing.exception.code, "runtime.run.unknown")

    def test_default_routes_are_executed_as_validated_argument_lists(self) -> None:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        resources = snapshot["substrate"]["topology"]["resources"]
        next(resource for resource in resources if resource["name"] == "h1")[
            "defaultRoute"
        ] = "via 10.0.0.254 dev h1-eth0"
        plan = compile_experiment(experiment_from(snapshot))
        runtime = recording_runtime(self)

        runtime.deploy(plan)

        self.assertEqual(
            RecordingNetwork.instances[-1].get("h1").pexec_calls,
            [
                [
                    "ip",
                    "route",
                    "replace",
                    "default",
                    "via",
                    "10.0.0.254",
                    "dev",
                    "h1-eth0",
                ]
            ],
        )

    def test_only_one_live_network_may_be_owned_by_a_runtime(self) -> None:
        runtime = recording_runtime(self)
        first = runtime.deploy(self.plan)

        with self.assertRaises(RuntimeOperationError) as context:
            runtime.deploy(self.plan)

        self.assertEqual(context.exception.code, "runtime.run.active")
        self.assertEqual(context.exception.run_id, first.id)
        self.assertEqual(len(RecordingNetwork.instances), 1)

    def test_running_state_is_persisted_until_normal_teardown(self) -> None:
        store = temporary_store(self)
        runtime = recording_runtime(self, state_store=store)

        run = runtime.deploy(self.plan)
        record = store.read()

        self.assertIsNotNone(record)
        self.assertEqual(record.run, run)
        self.assertEqual(record.plan["digest"], self.plan.digest)
        self.assertTrue(store.acquired)

        runtime.teardown(run.id)

        self.assertFalse(store.acquired)
        self.assertFalse(store.state_path.exists())

    def test_a_second_process_cannot_deploy_while_owner_lock_is_held(self) -> None:
        store = temporary_store(self)
        owner = recording_runtime(self, state_store=store)
        run = owner.deploy(self.plan)
        contender_store = RunStateStore(store.state_directory, store.lock_path)
        contender = recording_runtime(self, state_store=contender_store)

        with self.assertRaises(RuntimeOperationError) as context:
            contender.deploy(self.plan)

        self.assertEqual(context.exception.code, "runtime.run.active")
        self.assertEqual(context.exception.run_id, run.id)

    def test_fresh_runtime_can_inspect_a_run_while_owner_holds_lock(self) -> None:
        store = temporary_store(self)
        owner = recording_runtime(self, state_store=store)
        run = owner.deploy(self.plan)
        viewer = recording_runtime(
            self,
            state_store=RunStateStore(store.state_directory, store.lock_path),
        )

        snapshot = viewer.inspect(run.id)

        self.assertEqual(snapshot.run.state, RunState.RUNNING)
        self.assertTrue(
            all(
                resource.state == ResourceOperationalState.UP
                for resource in snapshot.resources
            )
        )

    def test_orphaned_run_is_inspectable_and_recovered_by_run_id(self) -> None:
        store = temporary_store(self)
        original = recording_runtime(self, state_store=store)
        run = original.deploy(self.plan)
        record = store.read()
        self.assertIsNotNone(record)
        store.release()
        recoveries = []
        recovered = recording_runtime(
            self,
            state_store=store,
            recovery=lambda plan, groups: recoveries.append((plan, groups)),
        )

        snapshot = recovered.inspect(run.id)
        result = recovered.teardown(run.id)

        self.assertEqual(snapshot.run.state, RunState.FAILED)
        self.assertEqual(snapshot.run.issue.code, "runtime.run.orphaned")
        self.assertTrue(
            all(
                resource.state == ResourceOperationalState.UNKNOWN
                for resource in snapshot.resources
            )
        )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0][0], self.plan)
        self.assertEqual(result.run.state, RunState.STOPPED)
        self.assertFalse(store.state_path.exists())

    def test_new_deploy_requires_explicit_recovery_of_an_orphan(self) -> None:
        store = temporary_store(self)
        original = recording_runtime(self, state_store=store)
        orphan = original.deploy(self.plan)
        record = store.read()
        self.assertIsNotNone(record)
        store.release()
        dead_owner = ProcessOwner.current().model_copy(
            update={"start_ticks": ProcessOwner.current().start_ticks + 1}
        )
        store.acquire()
        store.write(record.model_copy(update={"owner": dead_owner}))
        store.release()
        replacement = recording_runtime(self, state_store=store)

        with self.assertRaises(RuntimeOperationError) as context:
            replacement.deploy(self.plan)

        self.assertEqual(context.exception.code, "runtime.run.orphaned")
        self.assertEqual(context.exception.run_id, orphan.id)

    def test_actions_are_typed_rejections_until_action_support_lands(self) -> None:
        runtime = recording_runtime(self)
        run = runtime.deploy(self.plan)

        result = runtime.execute(
            run.id,
            ActionRequest(
                id="change-link",
                name="link.disable",
                target="h1-s1",
            ),
        )

        self.assertEqual(result.status, ActionStatus.REJECTED)
        self.assertFalse(result.changed)
        self.assertEqual(result.issue.code, "runtime.action.unsupported")


if __name__ == "__main__":
    unittest.main()
