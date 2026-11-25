"""Scenario harness: mechanism tests through fake OVS/Mininet seams, no VM.

Live substrate correctness remains subject to the authorized feasibility
slice; these tests prove the harness applies the substrate plan verbatim
(ports, baseline rules, fault targets, persistence, reset and cleanup) and
retains evidence, without constructing a network or model clients.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from examples.noms27.recovery_campaign.harness import HOST_MACS, ScenarioLiveHarness
from examples.noms27.recovery_campaign.scenarios import Scenario, make_scenario
from examples.noms27.recovery_campaign.substrate import (
    cleanup_inventory,
    plan_fault,
    plan_flows,
)

FOREIGN_INTERFACE = "foreign-eth0"


def scenario_ports_of(plan: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Ports per switch implied by the plan's declared link endpoints."""

    ports: dict[str, dict[str, int]] = {}
    for link in plan["topology"]["links"]:
        ports.setdefault(link["left"], {})[link["right"]] = link["left_port"]
        ports.setdefault(link["right"], {})[link["left"]] = link["right_port"]
    return {
        switch: values for switch, values in ports.items() if not switch.startswith("h")
    }


def owned_interfaces(plan: dict[str, Any]) -> set[str]:
    return {
        f"{switch}-eth{number}"
        for switch, ports in scenario_ports_of(plan).items()
        for number in ports.values()
    } | {"h1-eth0", "h2-eth0"}


class OvsUnavailable(Exception):
    """Fake seam failure with an ovs-style stderr message."""


class FakeIntf:
    def __init__(self, link: FakeLink):
        self._link = link

    def isUp(self) -> bool:
        return self._link.up


class FakeLink:
    def __init__(self):
        self.up = True
        self.intf1 = FakeIntf(self)
        self.intf2 = FakeIntf(self)


class FakeHost:
    def __init__(self, name: str, mac: str):
        self.name = name
        self.mac = mac

    def pexec(self, arguments: list[str]) -> tuple[str, str, int]:
        if arguments[:4] == ["ip", "-j", "link", "show"]:
            return (
                json.dumps([{"ifname": f"{self.name}-eth0", "address": self.mac}]),
                "",
                0,
            )
        if arguments[:3] == ["ip", "neigh", "replace"]:
            return ("", "", 0)
        raise AssertionError(f"unexpected host command {arguments!r}")


class FakeSwitch:
    def __init__(self, name: str, ports: dict[str, int]):
        self.name = name
        self.ports = ports


class FakeMininet:
    """Applies the substrate plan to an in-memory node/link model."""

    def __init__(self, plan: dict[str, Any], ovs: FakeOvs):
        self._ovs = ovs
        self.switch_nodes = {
            switch: FakeSwitch(
                switch,
                {
                    f"{switch}-eth{number}": number
                    for number in set(scenario_ports_of(plan)[switch].values())
                },
            )
            for switch in plan["topology"]["switches"]
        }
        self.hosts = {host: FakeHost(host, HOST_MACS[host]) for host in ("h1", "h2")}
        self.links: list[FakeLink] = []
        self.pairs: dict[frozenset[str], FakeLink] = {}
        for link in plan["topology"]["links"]:
            link_object = FakeLink()
            self.links.append(link_object)
            self.pairs[frozenset((link["left"], link["right"]))] = link_object
        self.status_operations: list[tuple[str, str, str]] = []
        self.stop_calls = 0

    def get(self, name: str):
        return self.hosts.get(name) or self.switch_nodes[name]

    def configLinkStatus(self, left: str, right: str, state: str) -> None:
        self.status_operations.append((left, right, state))
        self.pairs[frozenset((left, right))].up = state == "up"

    def stop(self) -> None:
        self.stop_calls += 1
        # Modeled substrate: teardown removes the owned state.
        self._ovs.bridges -= set(self.switch_nodes)
        self._ovs.interfaces -= self.owned_interface_names()

    def owned_interface_names(self) -> set[str]:
        return {
            f"{node.name}-eth{number}"
            for node in self.switch_nodes.values()
            for number in node.ports.values()
        } | {"h1-eth0", "h2-eth0"}


class FakeOvs:
    """In-memory ovs-vsctl/ofctl/ip seam with recorded add-flow commands."""

    def __init__(self):
        self.bridges: set[str] = set()
        self.interfaces: set[str] = set()
        self.flows: dict[str, list[str]] = {}
        self.show_failures = False

    def __call__(self, arguments: list[str]) -> str:
        match arguments[0]:
            case "ovs-vsctl":
                return _ovs_vsctl(self, arguments)
            case "ovs-ofctl":
                return _ovs_ofctl(self, arguments)
            case "ip":
                return _ip(self, arguments)
            case _:
                raise AssertionError(arguments)


def _ovs_vsctl(ovs: FakeOvs, arguments: list[str]) -> str:
    if arguments[-1] == "show":
        if ovs.show_failures:
            raise OvsUnavailable("ovsdb-server is not running")
        return "bridge lines\n"
    if arguments[1] == "br-exists":
        return ""
    if arguments[1] == "get" and arguments[2] == "Bridge":
        if arguments[4] == "fail_mode":
            return '"secure"\n'
        if arguments[4] == "protocols":
            return "OpenFlow13\n"
    if arguments[1] == "get-controller":
        return "\n"
    if arguments[1] == "list-br":
        return "\n".join(sorted(ovs.bridges)) + "\n"
    raise AssertionError(arguments)


def _ovs_ofctl(ovs: FakeOvs, arguments: list[str]) -> str:
    sub, switch = arguments[3], arguments[4]
    if sub == "show":
        return "OFPT_FEATURES_REPLY\n"
    if sub == "dump-flows":
        return "\n".join(
            ovs.flows.get(switch, ["cookie=0x1100,priority=0,actions=drop"])
        )
    if sub == "add-flow":
        ovs.flows.setdefault(switch, []).append(arguments[5])
        return ""
    if sub == "del-flows":
        ovs.flows.pop(switch, None)
        return ""
    raise AssertionError(arguments)


def _ip(ovs: FakeOvs, arguments: list[str]) -> str:
    if arguments[1:4] == ["-j", "link", "show"]:
        if len(arguments) == 6:
            return json.dumps(
                [{"ifname": arguments[5], "address": "02:00:00:00:00:42"}]
            )
        return json.dumps([{"ifname": name} for name in sorted(ovs.interfaces)])
    if arguments[1] == "neigh":
        return ""
    raise AssertionError(arguments)


def root_command_from(ovs: FakeOvs):
    def run(arguments: list[str]) -> str:
        try:
            return ovs(arguments)
        except OvsUnavailable as unavailable:
            raise RuntimeError(f"{arguments!r} failed: {unavailable}") from None

    return run


def exists_command_from(ovs: FakeOvs):
    def exists(arguments: list[str]) -> bool:
        if arguments[1] == "br-exists":
            return arguments[2] in ovs.bridges
        if len(arguments) == 6:
            return arguments[5] in ovs.interfaces
        raise AssertionError(arguments)

    return exists


def fresh_harness(
    root: Any,
    class_id: str = "diamond_switch",
    placement: int = 0,
) -> tuple[ScenarioLiveHarness, Scenario, FakeOvs]:
    scenario = make_scenario(class_id, placement)
    ovs = FakeOvs()
    harness = ScenarioLiveHarness(
        scenario,
        root / "harness",
        root_command=root_command_from(ovs),
        exists_command=exists_command_from(ovs),
        net_factory=lambda plan: FakeMininet(plan, ovs),
        environment_check=lambda: None,
    )
    return harness, scenario, ovs


def test_setup_applies_flows_and_records_baseline(tmp_path):
    harness, scenario, ovs = fresh_harness(tmp_path)
    summary = harness.setup()
    inventory = cleanup_inventory(scenario)
    assert summary["switches"] == list(inventory["bridges"])
    expected_flows = plan_flows(scenario)["initial"] + plan_flows(scenario)["backup"]
    assert summary["initial_rules"] + summary["preinstalled_backup_rules"] == len(
        expected_flows
    )
    for flow in expected_flows:
        assert flow["flow"] in ovs.flows[flow["switch"]]
    assert sum(
        "cookie=0x1100,priority=0" in line
        for lines in ovs.flows.values()
        for line in lines
    ) == len(inventory["bridges"])
    events = harness.events_path.read_text()
    assert json.loads(events.splitlines()[0])["operation"] == "setup"


def test_verify_ports_cross_checks_the_declared_port_map(tmp_path):
    for class_id in ("diamond_link", "extended_switch", "tail_switch"):
        harness, scenario, _ovs = fresh_harness(tmp_path / class_id, class_id=class_id)
        harness.setup()
        verified = harness.verify_ports()
        for switch, ports in scenario.ports.items():
            assert verified["switches"][switch] == {
                f"{switch}-eth{number}": number for number in ports.values()
            }


def test_setup_refuses_to_replace_existing_state(tmp_path):
    harness, _scenario, ovs = fresh_harness(tmp_path)
    ovs.bridges.add("s1")
    with pytest.raises(RuntimeError, match="bridge"):
        harness.setup()

    harness2, scenario2, ovs2 = fresh_harness(tmp_path / "2")
    ovs2.interfaces |= owned_interfaces(ovs_plan_of(scenario2))
    with pytest.raises(RuntimeError, match="interface"):
        harness2.setup()


def ovs_plan_of(scenario: Scenario) -> dict[str, Any]:
    from examples.noms27.recovery_campaign.substrate import plan_topology

    return {"topology": plan_topology(scenario)}


@pytest.mark.parametrize(
    "class_id", ["diamond_switch", "extended_switch", "tail_switch"]
)
def test_isolation_takes_all_incident_links_down_and_records_liveness(
    tmp_path, class_id
):
    harness, scenario, _ovs = fresh_harness(tmp_path, class_id=class_id)
    harness.setup()
    harness.fault()
    check = harness.check_fault()
    assert check["kind"] == "transit_switch_isolation"
    assert check["both_endpoints_down"] is True
    assert check["endpoints_survive"] is True
    assert check["switch_process_stopped"] is False
    assert check["state_corruption"] is False
    down_pairs = {
        (left, right)
        for left, right, state in harness.net.status_operations
        if state == "down"
    }
    assert down_pairs == {
        (edge["left"], edge["right"]) for edge in plan_fault(scenario)["edges"]
    }


@pytest.mark.parametrize("class_id", ["diamond_switch", "tail_switch"])
def test_isolation_liveness_records_a_stopped_switch_process(tmp_path, class_id):
    scenario = make_scenario(class_id, 0)

    ovs = FakeOvs()
    ovs.show_failures = True
    harness = ScenarioLiveHarness(
        scenario,
        tmp_path / "harness-crash",
        root_command=root_command_from(ovs),
        exists_command=exists_command_from(ovs),
        net_factory=lambda plan: FakeMininet(plan, ovs),
        environment_check=lambda: None,
    )
    harness.setup()
    harness.fault()
    check = harness.check_fault()
    assert check["both_endpoints_down"] is True
    assert check["switch_process_stopped"] is True
    assert "ovsdb-server is not running" in check["probe_error"]


def test_link_fault_is_isolated_to_the_declared_edge(tmp_path):
    harness, _scenario, _ovs = fresh_harness(tmp_path, class_id="diamond_link")
    harness.setup()
    assert harness.fault()["kind"] == "link"
    check = harness.check_fault()
    assert check["both_endpoints_down"] is True
    assert check["link_states"] == {"s1-p0": [False, False]}
    down_pairs = {
        (left, right)
        for left, right, state in harness.net.status_operations
        if state == "down"
    }
    assert down_pairs == {("s1", "p0")}


def test_reset_restores_and_reinstalls_baseline(tmp_path):
    harness, scenario, ovs = fresh_harness(tmp_path)
    harness.setup()
    before = {switch: list(lines) for switch, lines in ovs.flows.items()}
    harness.fault()
    harness.reset()
    reset_pairs = {
        (left, right)
        for left, right, state in harness.net.status_operations
        if state == "up"
    }
    assert reset_pairs == {
        (edge["left"], edge["right"]) for edge in plan_fault(scenario)["reset_edges"]
    }
    for edge in plan_fault(scenario)["reset_edges"]:
        name = f"{edge['left']}-{edge['right']}"
        assert harness._link_states()[name] == "up"
    for switch, lines in ovs.flows.items():
        assert lines == before[switch]
    with pytest.raises(RuntimeError, match="nothing is faulted"):
        harness.reset()


def test_cleanup_verifies_absence_of_owned_state(tmp_path):
    harness, scenario, ovs = fresh_harness(tmp_path)
    harness.setup()
    ovs.interfaces |= owned_interfaces(ovs_plan_of(scenario)) | {FOREIGN_INTERFACE}
    result = harness.cleanup()
    assert result["status"] == "stopped"
    assert result["remaining_interfaces"] == []
    assert result["remaining_bridges"] == []
    assert result["verified"] is True
    assert harness.net is None


def test_cleanup_fails_closed_on_leftover_owned_state(tmp_path):
    harness, scenario, ovs = fresh_harness(tmp_path / "closed")
    harness.setup()
    plan = ovs_plan_of(scenario)
    survivor = min(owned_interfaces(plan) - {"h1-eth0"})
    # The modeled substrate removes its own state on stop, but one interface
    # survives: net.stop has already run, so list the survivor left behind.
    ovs.interfaces |= set(owned_interfaces(plan))
    original_stop = FakeMininet.stop

    def leaky_stop(self):
        original_stop(self)
        self._ovs.interfaces.add(survivor)

    FakeMininet.stop = leaky_stop
    try:
        result = harness.cleanup()
    finally:
        FakeMininet.stop = original_stop
    assert result["verified"] is False
    assert result["remaining_interfaces"] == [survivor]
