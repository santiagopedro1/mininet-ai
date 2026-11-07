"""Pure scenario -> substrate plans. No Mininet objects, processes or models.

A frozen `Scenario` compiles into declarative topology, flow, fault and
cleanup plans that a live adapter (or its offline stub) applies verbatim.
Compilation mirrors the verified controlled adapter's ingress-matched
forwarding: rules are keyed by switch and ingress port, and no treatment
outcome, diagnostic or model response ever enters a plan.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from .scenarios import Scenario

FAULT_KINDS = ("link", "transit_switch_isolation")
SOURCE_ADDRESS = "10.0.0.1"
DESTINATION_ADDRESS = "10.0.0.2"
TABLE_MISS_COOKIE = 0x1100
FLOW_KEY_ORDER = ("dl_type", "nw_src", "nw_dst")


def _hosts_and_switches(scenario: Scenario) -> tuple[tuple[str, ...], tuple[str, ...]]:
    nodes = {node for edge in scenario.links for node in edge}
    hosts = tuple(sorted(node for node in nodes if node.startswith("h")))
    switches = tuple(sorted(node for node in nodes if not node.startswith("h")))
    if hosts != ("h1", "h2"):
        raise ValueError("substrate expects exactly the h1 and h2 endpoints")
    return hosts, switches


def edge_port(scenario: Scenario, node: str, peer: str) -> int:
    """A host attachment is always port 1; switches follow the scenario ports."""

    if node.startswith("h"):
        return 1
    return scenario.ports[node][peer]


def plan_topology(scenario: Scenario) -> dict[str, Any]:
    """Nodes, links and per-switch port numbers, frozen and validated upstream."""

    hosts, switches = _hosts_and_switches(scenario)
    links = []
    for left, right in scenario.links:
        links.append(
            {
                "name": f"{left}-{right}",
                "left": left,
                "right": right,
                "left_port": edge_port(scenario, left, right),
                "right_port": edge_port(scenario, right, left),
            }
        )
    return {
        "topology_id": scenario.topology_id,
        "placement_id": scenario.placement_id,
        "hosts": tuple(hosts),
        "switches": tuple(switches),
        "source_address": SOURCE_ADDRESS,
        "destination_address": DESTINATION_ADDRESS,
        "links": links,
    }


def _cookie_base(scenario: Scenario) -> int:
    digest = hashlib.sha256(scenario.model_dump_json().encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _flow(
    *,
    scenario: Scenario,
    switch: str,
    in_port: int,
    out_port: int,
    priority: Literal[100, 200],
    cookie: int,
) -> dict[str, Any]:
    match = {
        "dl_type": 2048,
        "nw_src": SOURCE_ADDRESS,
        "nw_dst": DESTINATION_ADDRESS,
    }
    flow = (
        f"cookie={cookie:#x},priority={priority},idle_timeout=0,hard_timeout=0,"
        f"ip,in_port={in_port},nw_src={SOURCE_ADDRESS},nw_dst={DESTINATION_ADDRESS},"
        f"actions=output:{out_port}"
    )
    return {
        "switch": switch,
        "in_port": in_port,
        "out_port": out_port,
        "match": match,
        "actions": [f"output:{out_port}"],
        "priority": priority,
        "cookie": cookie,
        "idle_timeout": 0,
        "hard_timeout": 0,
        "flow": flow,
        "purpose": "directional h1-to-h2 forwarding",
    }


def plan_flows(scenario: Scenario) -> dict[str, Any]:
    """Initial-state and preinstalled-backup arrival rules for every switch.

    The compilation keeps the verified controlled semantics: every declared
    predecessor's ingress forwards to that switch's own initial next hop.
    Rules on the active initial forwarding path are the substrate's active
    initial forwarding (priority 100). Every other declared arrival is
    preinstalled downstream backup (priority 200), mirroring the original
    diamond live adapter's REC[0]/REC[2] preinstall. Runtime policy rules use
    priority 300 in the live layer; nothing at priority 300 is preinstalled,
    so no automatic failover exists. Broader-repair instances stay missing
    the interior backup-switch rules on purpose: S2 must install them.
    """

    ports = scenario.ports
    predecessors = [
        *scenario.initial_forwarding.items(),
        *scenario.backup_forwarding.items(),
        ("h1", "s1"),
    ]
    active = _active_edges(scenario)
    base = _cookie_base(scenario)
    initial, backup, identities = [], [], set()
    for node, target in scenario.initial_forwarding.items():
        node_ports = ports[node]
        for previous, _ in (pair for pair in predecessors if pair[1] == node):
            identity = (node, node_ports[previous], node_ports[target])
            if identity in identities:
                continue
            identities.add(identity)
            on_active_path = (previous, node) in active
            tier = initial if on_active_path else backup
            tier.append(
                _flow(
                    scenario=scenario,
                    switch=node,
                    in_port=node_ports[previous],
                    out_port=node_ports[target],
                    priority=100 if tier is initial else 200,
                    cookie=base + len(initial) + len(backup),
                )
            )
    return {
        "scenario_sha256": hashlib.sha256(
            scenario.model_dump_json().encode()
        ).hexdigest(),
        "initial": initial,
        "backup": backup,
    }


def _active_edges(scenario: Scenario) -> frozenset[tuple[str, str]]:
    """Edges of the h1-to-h2 walk over the declared initial forwarding."""

    edges: set[tuple[str, str]] = {("h1", "s1")}
    node = "s1"
    for _ in range(len(scenario.links) + 1):
        target = scenario.initial_forwarding.get(node)
        if target is None or (node, target) in edges:
            break
        edges.add((node, target))
        if target == "h2":
            break
        node = target
    return frozenset(edges)


def plan_fault(scenario: Scenario) -> dict[str, Any]:
    """Injected outage targets and the reset edges of the same incident."""

    edges = [{"left": left, "right": right} for left, right in scenario.disabled_links]
    if not edges:
        raise ValueError("a substrate incident requires disabled links")
    switch = None if scenario.fault_kind == "link" else scenario.fault_target[0]
    return {
        "kind": scenario.fault_kind,
        "switch": switch,
        "edges": edges,
        "reset_edges": [dict(edge) for edge in edges],
        "process_owner_probes": ["ovs-vsctl", "--timeout=3", "show"]
        if scenario.fault_kind == "transit_switch_isolation"
        else [],
        "checks": {
            "kind": scenario.fault_kind,
            "both_endpoints_down": True,
            "endpoints_survive": True,
        },
    }


def cleanup_inventory(scenario: Scenario) -> dict[str, Any]:
    """Owned names for post-teardown absence inspection and table-miss drops."""

    hosts, switches = _hosts_and_switches(scenario)
    return {
        "bridges": switches,
        "interfaces": {
            f"{switch}-eth{number}"
            for switch, ports in scenario.ports.items()
            for number in ports.values()
        }
        | {f"{host}-eth0" for host in hosts},
        "drops": [
            {
                "switch": switch,
                "cookie": TABLE_MISS_COOKIE,
                "priority": 0,
                "actions": ["drop"],
            }
            for switch in switches
        ],
    }


def substrate_plan(scenario: Scenario) -> dict[str, Any]:
    """Everything one incident needs from the substrate, in one frozen record."""

    return {
        "topology": plan_topology(scenario),
        "flows": plan_flows(scenario),
        "fault": plan_fault(scenario),
        "cleanup": cleanup_inventory(scenario),
    }
