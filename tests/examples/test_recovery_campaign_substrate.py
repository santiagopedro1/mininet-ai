"""Scenario-directed substrate plans: bounded contract checks, no live network.

The contract under test is declarative only: frozen scenario -> nodes, links,
ports, initial/backup-aware flows, fault targets and the cleanup inventory.
Live correctness remains subject to the authorized feasibility slice; these
tests prove the plan is complete, frozen and consistent with the verified
controlled forwarding model.
"""

from __future__ import annotations

import asyncio

from examples.noms27.recovery_campaign.blocks import CampaignConfig
from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime
from examples.noms27.recovery_campaign.scenarios import CLASSES, Scenario, make_scenario
from examples.noms27.recovery_campaign.substrate import (
    DESTINATION_ADDRESS,
    FAULT_KINDS,
    SOURCE_ADDRESS,
    cleanup_inventory,
    edge_port,
    plan_fault,
    plan_flows,
    plan_topology,
)

CLASSES_AND_PLACEMENTS = [
    (class_id, placement) for class_id in CLASSES for placement in range(3)
]


def scenario_config(scenario: Scenario) -> CampaignConfig:
    return CampaignConfig(
        trial_id=f"plan-{scenario.placement_id}",
        treatment="s1_only",
        scenario="matched_scenario",
        scenario_instance=scenario,
        deadline_seconds=6,
        s1_cap_seconds=3,
        bottleneck_mbps=9.6,
        seed=1000,
    )


def controlled_rules(scenario: Scenario) -> set[tuple[str, int, int]]:
    """The verified controlled adapter's initial ingress-matched rule identity."""

    runtime = ControlledScenarioRuntime()

    async def collect():
        return await runtime.setup(scenario_config(scenario), object())

    result = asyncio.run(collect())
    return {
        (rule["switch"], rule["in_port"], rule["out_port"])
        for rule in result["initial_rules"]
    }


def rule_identity(flow: dict) -> tuple[str, int, int]:
    return (flow["switch"], flow["in_port"], flow["out_port"])


def priority_flow(flow: dict) -> tuple[int, str, int, int]:
    return (flow["priority"], flow["switch"], flow["in_port"], flow["out_port"])


def neighbor(scenario: Scenario, switch: str, port: int) -> str:
    return next(
        peer for peer, number in scenario.ports[switch].items() if number == port
    )


def initial_predecessors(scenario: Scenario, switch: str) -> set[str]:
    return {
        node for node, target in scenario.initial_forwarding.items() if target == switch
    }


def s2_runtime_installs(scenario: Scenario) -> set[tuple[str, int, int]]:
    """The scripted S2 repair set, mirroring decide_s2 against the same authority."""

    actions = set()
    for node, target in scenario.backup_forwarding.items():
        previous = (
            "h1"
            if node == "s1"
            else next(
                peer
                for peer, value in scenario.backup_forwarding.items()
                if value == node
            )
        )
        actions.add(
            (node, scenario.ports[node][previous], scenario.ports[node][target])
        )
    return {("s1", 1, scenario.ports["s1"]["s3"])} | actions


def s1_only_bypass(scenario: Scenario) -> tuple[str, int, int]:
    return ("s1", 1, scenario.ports["s1"]["s3"])


def walk(
    scenario: Scenario, rules: set[tuple[int, str, int, int]], *, down: frozenset
) -> bool:
    """Test-side walk; runtime rules (higher priority) outrank preinstalled ones."""

    previous, node, visited = "h1", "s1", set()
    while node != "h2":
        ports = scenario.ports.get(node)
        if ports is None:
            return False
        ingress = ports[previous]
        if (node, ingress) in visited:
            return False
        visited.add((node, ingress))
        candidates = [rule for rule in rules if rule[1] == node and rule[2] == ingress]
        if not candidates:
            return False
        output_port = max(candidates)[3]
        target = next(peer for peer, port in ports.items() if port == output_port)
        edge = frozenset((node, target))
        if edge in down or edge not in {frozenset(e) for e in scenario.links}:
            return False
        previous, node = node, target
    return True


def test_every_class_and_placement_yields_a_complete_frozen_plan():
    for class_id, placement in CLASSES_AND_PLACEMENTS:
        scenario = make_scenario(class_id, placement)
        topology = plan_topology(scenario)
        flows = plan_flows(scenario)
        fault = plan_fault(scenario)
        inventory = cleanup_inventory(scenario)

        switches = {
            node for edge in scenario.links for node in edge if not node.startswith("h")
        }
        assert topology["switches"] == tuple(sorted(switches))
        assert topology["hosts"] == ("h1", "h2")
        assert topology["source_address"] == SOURCE_ADDRESS == "10.0.0.1"
        assert topology["destination_address"] == DESTINATION_ADDRESS == "10.0.0.2"
        assert len(topology["links"]) == len(scenario.links)
        for link, edge in zip(topology["links"], scenario.links, strict=True):
            assert (link["left"], link["right"]) == edge
            assert link["left_port"] == edge_port(scenario, link["left"], link["right"])
            assert link["right_port"] == edge_port(
                scenario, link["right"], link["left"]
            )
        assert scenario.ports["s1"] == {"h1": 1, "p0": 2, "s3": 3}

        preinstalled = flows["initial"] + flows["backup"]
        assert {rule_identity(flow) for flow in preinstalled} == controlled_rules(
            scenario
        )
        assert all(flow["priority"] == 100 for flow in flows["initial"])
        assert all(flow["priority"] == 200 for flow in flows["backup"])
        assert all(
            flow["match"]
            == {
                "dl_type": 2048,
                "nw_src": SOURCE_ADDRESS,
                "nw_dst": DESTINATION_ADDRESS,
            }
            for flow in preinstalled
        )
        assert len({flow["cookie"] for flow in preinstalled}) == len(preinstalled)
        assert all(flow["switch"] in switches for flow in preinstalled)
        assert inventory["drops"] == [
            {"switch": switch, "cookie": 0x1100, "priority": 0, "actions": ["drop"]}
            for switch in topology["switches"]
        ]

        assert fault["kind"] == scenario.fault_kind
        assert fault["kind"] in FAULT_KINDS
        assert {
            frozenset((edge["left"], edge["right"])) for edge in fault["edges"]
        } == {frozenset(edge) for edge in scenario.disabled_links}
        expected_switch = None if fault["kind"] == "link" else scenario.fault_target[0]
        assert fault["switch"] == expected_switch
        if fault["kind"] == "transit_switch_isolation":
            assert fault["process_owner_probes"] == ["ovs-vsctl", "--timeout=3", "show"]

        assert inventory["bridges"] == tuple(topology["switches"])
        assert set(inventory["interfaces"]) == {
            f"{switch}-eth{number}"
            for switch, ports in scenario.ports.items()
            for number in ports.values()
        } | {f"{host}-eth0" for host in topology["hosts"]}
        assert {(edge["left"], edge["right"]) for edge in fault["reset_edges"]} == {
            (edge["left"], edge["right"]) for edge in fault["edges"]
        }


def test_backup_tier_contains_only_declared_resources():
    """Priority-200 rules preinstall only declared backup arrivals, never new authority."""

    for class_id, placement in CLASSES_AND_PLACEMENTS:
        scenario = make_scenario(class_id, placement)
        initial = {rule_identity(flow) for flow in plan_flows(scenario)["initial"]}
        for flow in plan_flows(scenario)["backup"]:
            switch = flow["switch"]
            backup_predecessor = next(
                (
                    node
                    for node, target in scenario.backup_forwarding.items()
                    if target == switch
                ),
                None,
            )
            assert backup_predecessor is not None
            output_neighbor = neighbor(scenario, switch, flow["out_port"])
            ingress_neighbor = neighbor(scenario, switch, flow["in_port"])
            assert scenario.initial_forwarding[switch] == output_neighbor
            assert rule_identity(flow) not in initial
            assert (
                ingress_neighbor in initial_predecessors(scenario, switch)
                or ingress_neighbor == backup_predecessor
            )


def test_diamond_instance_matches_the_original_diamond_substrate():
    """The original diamond is one configuration; its s2 is named p0 here.

    Only s1's port freeze is shared history (validated by the scenario itself);
    other port numbers are scenario-derived, and the original incident keeps
    its own diamond harness.
    """

    scenario = make_scenario("diamond_link", 0)
    rename = {"p0": "s2"}
    renamed_ports = {
        rename.get(node, node): {
            rename.get(peer, peer): port for peer, port in ports.items()
        }
        for node, ports in scenario.ports.items()
    }
    assert renamed_ports["s1"] == {"h1": 1, "s2": 2, "s3": 3}
    fault = plan_fault(scenario)
    assert (fault["edges"][0]["left"], fault["edges"][0]["right"]) == ("s1", "p0")


def test_walk_reproduces_regime_semantics_over_the_compiled_plan():
    for class_id, placement in CLASSES_AND_PLACEMENTS:
        scenario = make_scenario(class_id, placement)
        rules = {priority_flow(flow) for flow in plan_flows(scenario)["initial"]}
        down = frozenset(frozenset(edge) for edge in scenario.disabled_links)
        assert walk(scenario, rules, down=frozenset()) is True
        assert walk(scenario, rules, down=down) is False

        regime = scenario.structural_check()["regime"]
        preinstalled = rules | {
            priority_flow(flow) for flow in plan_flows(scenario)["backup"]
        }
        s1_paths = {(300, *s1_only_bypass(scenario))} | preinstalled
        s2_paths = {
            (300, *action) for action in s2_runtime_installs(scenario)
        } | preinstalled
        assert walk(scenario, s1_paths, down=down) is (
            regime == "local_repair_sufficient"
        )
        assert walk(scenario, s2_paths, down=down) is (regime != "no_surviving_path")


def test_s1_catalogue_bypass_is_runtime_only_in_every_plan():
    for class_id, placement in CLASSES_AND_PLACEMENTS:
        scenario = make_scenario(class_id, placement)
        preinstalled = plan_flows(scenario)["initial"] + plan_flows(scenario)["backup"]
        assert (300, "s1", 1, scenario.ports["s1"]["s3"]) not in {
            priority_flow(flow) for flow in preinstalled
        }
        assert scenario.ports["s1"]["s3"] == 3
