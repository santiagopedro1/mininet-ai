"""Scenario-directed Mininet/OVS harness for one owned incident substrate.

The harness owns node/link/port construction, baseline forwarding, injected
outages (link cut or transit-switch isolation), persistence checks, reset and
budgeted teardown. It never makes a model decision, never installs a model's
recovery bundle, and never revives or reverses an injected outage outside
`reset`. Evidence is durable JSONL, mirroring the original diamond harness.

`--check` compiles the substrate plan offline without building a network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .scenarios import Scenario
from .substrate import (
    DESTINATION_ADDRESS,
    SOURCE_ADDRESS,
    cleanup_inventory,
    substrate_plan,
)

HOST_MACS = {"h1": "02:00:00:00:00:01", "h2": "02:00:00:00:00:02"}
REQUIRED_TOOLS = ("ovs-ofctl", "ovs-vsctl", "ip")


def root_command(arguments: list[str]) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(
            f"{arguments!r} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def exists_command(arguments: list[str]) -> bool:
    """Returncode-only existence probe: 0 means the ovs/ip object is present."""

    result = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError(
            f"{arguments!r} could not determine existence ({result.returncode}): "
            f"{result.stderr.strip()}"
        )
    return result.returncode == 0


def build_mininet(plan: dict[str, Any]) -> Any:
    """Real Mininet construction; injectable so mechanism tests use a stub."""

    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import Host, OVSSwitch

    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        host=Host,
        link=TCLink,
        build=False,
        autoSetMacs=False,
        autoStaticArp=False,
        waitConnected=False,
    )
    net.addHost("h1", ip=f"{SOURCE_ADDRESS}/24", mac=HOST_MACS["h1"])
    net.addHost("h2", ip=f"{DESTINATION_ADDRESS}/24", mac=HOST_MACS["h2"])
    for switch in plan["topology"]["switches"]:
        net.addSwitch(switch, cls=OVSSwitch, failMode="secure", protocols="OpenFlow13")
    for link in plan["topology"]["links"]:
        kwargs: dict[str, Any] = {"cls": TCLink}
        # Host interfaces keep Mininet's default naming (eth0); only switch
        # sides receive the declared port numbers.
        if not link["left"].startswith("h"):
            kwargs["port1"] = link["left_port"]
        if not link["right"].startswith("h"):
            kwargs["port2"] = link["right_port"]
        net.addLink(net.get(link["left"]), net.get(link["right"]), **kwargs)
    net.build()
    net.start()
    return net


def environment_check() -> None:
    """Real live substrate requires Linux, the OVS tools and root privileges."""

    if sys.platform != "linux" or not all(
        shutil.which(command) for command in REQUIRED_TOOLS
    ):
        raise RuntimeError("live setup requires Linux and Open vSwitch tools")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise RuntimeError("live Mininet setup requires root")


ALLOWED_ENVIRONMENT = environment_check


class ScenarioLiveHarness:
    """A single owned Mininet network with durable JSONL operation evidence."""

    def __init__(
        self,
        scenario: Scenario,
        evidence_dir: Path,
        *,
        root_command: Callable[[list[str]], str] = root_command,
        exists_command: Callable[[list[str]], bool] = exists_command,
        net_factory: Callable[[dict[str, Any]], Any] = build_mininet,
        environment_check: Callable[[], None] = ALLOWED_ENVIRONMENT,
    ) -> None:
        self.scenario = scenario
        self.evidence_dir = evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = evidence_dir / "harness.jsonl"
        self._root_command = root_command
        self._exists = exists_command
        self._net_factory = net_factory
        self._environment_check = environment_check
        self.events_offset = 0
        self.net: Any = None
        self.links: dict[str, Any] = {}
        self.faulted: bool = False

    @property
    def plan(self) -> dict[str, Any]:
        return substrate_plan(self.scenario)

    def _event(self, operation: str, status: str, **data: Any) -> dict[str, Any]:
        record = {
            "observed_at": datetime.now(UTC).isoformat(),
            "operation": operation,
            "status": status,
            **data,
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def _run(self, operation: str, action):
        try:
            result = action()
        except Exception as error:
            self._event(operation, "failed", error=f"{type(error).__name__}: {error}")
            raise
        self._event(operation, "succeeded")
        return result

    def _refuse_existing_state(self) -> None:
        inventory = cleanup_inventory(self.scenario)
        for bridge in inventory["bridges"]:
            if self._exists(["ovs-vsctl", "br-exists", bridge]):
                raise RuntimeError(
                    f"refusing to replace existing OVS bridge {bridge!r}"
                )
        for name in sorted(inventory["interfaces"]):
            if self._exists(["ip", "-j", "link", "show", "dev", name]):
                raise RuntimeError(f"refusing to replace existing interface {name!r}")

    def setup(self) -> dict[str, Any]:
        def apply():
            if self.net is not None:
                raise RuntimeError("this harness already owns a Mininet run")
            self._environment_check()
            self._refuse_existing_state()
            self.net = self._net_factory(self.plan)
            self.links = {
                link["name"]: self.net.links[index]
                for index, link in enumerate(self.plan["topology"]["links"])
            }
            self._configure_hosts()
            self._install_baseline()
            self.verify_ports()
            flows = self.plan["flows"]
            return {
                "switches": list(self.plan["topology"]["switches"]),
                "controller": None,
                "source_address": SOURCE_ADDRESS,
                "destination_address": DESTINATION_ADDRESS,
                "host_macs": HOST_MACS,
                "initial_rules": len(flows["initial"]),
                "preinstalled_backup_rules": len(flows["backup"]),
                "table_miss": "drop",
                "scenario_placement": self.scenario.placement_id,
            }

        return self._run("setup", apply)

    def _configure_hosts(self) -> None:
        # The table-miss drop forbids ARP broadcasts for the h2 destination, so
        # h1 learns a single permanent neighbor entry instead.
        h1 = self.net.get("h1")
        output, error, status = h1.pexec(
            [
                "ip",
                "neigh",
                "replace",
                DESTINATION_ADDRESS,
                "lladdr",
                HOST_MACS["h2"],
                "nud",
                "permanent",
                "dev",
                "h1-eth0",
            ]
        )
        if status:
            raise RuntimeError(f"static neighbor failed: {(error or output).strip()}")

    def _install_flow(self, switch: str, flow: str) -> None:
        self._root_command(["ovs-ofctl", "-O", "OpenFlow13", "add-flow", switch, flow])

    def _install_baseline(self) -> None:
        topology = self.plan["topology"]
        for switch in topology["switches"]:
            self._root_command(["ovs-ofctl", "-O", "OpenFlow13", "del-flows", switch])
            self._install_flow(
                switch,
                "cookie=0x1100,priority=0,idle_timeout=0,hard_timeout=0,actions=drop",
            )
        for flow in self.plan["flows"]["initial"] + self.plan["flows"]["backup"]:
            self._install_flow(flow["switch"], flow["flow"])

    def inspect_flows(self) -> dict[str, str]:
        if self.net is None:
            raise RuntimeError("network is not set up")
        return {
            switch: self._root_command(
                ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch]
            )
            for switch in self.plan["topology"]["switches"]
        }

    def verify_ports(self) -> dict[str, Any]:
        if self.net is None:
            raise RuntimeError("network is not set up")
        ports = self.scenario.ports
        expected = {
            switch: {
                f"{switch}-eth{number}": number
                for number in set(ports[switch].values())
            }
            for switch in ports
        }
        actual = {}
        for switch, expected_ports in expected.items():
            switch_node = self.net.get(switch)
            actual[switch] = {
                getattr(interface, "name", interface): number
                for interface, number in switch_node.ports.items()
                if number > 0 and getattr(interface, "name", interface) != "lo"
            }
            if actual[switch] != expected_ports:
                raise RuntimeError(
                    f"deployed port mismatch for {switch}: "
                    f"expected {expected_ports}, got {actual[switch]}"
                )
            self._root_command(["ovs-ofctl", "-O", "OpenFlow13", "show", switch])
        for host in ("h1", "h2"):
            if self.net.get(host) is None:
                raise RuntimeError(f"missing host {host}")
        return {"switches": actual, "hosts": ("h1", "h2")}

    def readiness(self) -> dict[str, Any]:
        def check():
            if self.net is None:
                raise RuntimeError("network is not set up")

            switches = self.plan["topology"]["switches"]
            modes = {
                switch: self._root_command(
                    ["ovs-vsctl", "get", "Bridge", switch, "fail_mode"]
                ).strip()
                for switch in switches
            }
            if any(mode.strip('"') != "secure" for mode in modes.values()):
                raise RuntimeError(f"not all switches are fail-secure: {modes}")
            controllers = {
                switch: self._root_command(
                    ["ovs-vsctl", "get-controller", switch]
                ).strip()
                for switch in switches
            }
            if any(controllers.values()):
                raise RuntimeError(
                    f"unexpected controller/learning fallback: {controllers}"
                )
            protocols = {
                switch: self._root_command(
                    ["ovs-vsctl", "get", "Bridge", switch, "protocols"]
                ).strip()
                for switch in switches
            }
            if any("OpenFlow13" not in value for value in protocols.values()):
                raise RuntimeError(
                    f"OpenFlow13 is not enabled on every switch: {protocols}"
                )
            for name, host_name in (("h1", "h1"), ("h2", "h2")):
                host = self.net.get(name)
                link_data = json.loads(
                    (host.pexec(["ip", "-j", "link", "show", "dev", f"{name}-eth0"]))[0]
                )[0]
                if link_data.get("address", "").lower() != HOST_MACS[host_name]:
                    raise RuntimeError(f"host MAC mismatch on {host_name}: {link_data}")
            return {
                "ports": self.verify_ports(),
                "failModes": modes,
                "controllers": controllers,
                "protocols": protocols,
                "linkStates": self._link_states(),
                "tableMiss": "drop",
                "normalFallback": False,
            }

        return self._run("readiness", check)

    def _link_states(self) -> dict[str, str]:
        state = {}
        for name, link in self.links.items():
            state[name] = "up" if link.intf1.isUp() and link.intf2.isUp() else "down"
        return state

    def check_fault(self) -> dict[str, Any]:
        """One locking state observation of the injected outage, without policy input."""

        fault_plan = self.plan["fault"]
        kind = fault_plan["kind"]
        states = {}
        for edge in fault_plan["edges"]:
            name = f"{edge['left']}-{edge['right']}"
            link = self.links[name]
            states[name] = [link.intf1.isUp(), link.intf2.isUp()]
        check = {
            "kind": kind,
            "link_states": states,
            "both_endpoints_down": all(
                state == [False, False] for state in states.values()
            ),
            "endpoints_survive": all(
                self._link_state_of(edge) == [True, True]
                for edge in self.plan["topology"]["links"]
                if edge["left"].startswith("h") or edge["right"].startswith("h")
            ),
        }
        if kind == "transit_switch_isolation":
            liveness = self._isolation_liveness(fault_plan["switch"])
            check.update(liveness)
        return check

    def _link_state_of(self, link_plan: dict[str, Any]) -> list[bool]:
        link = self.links[link_plan["name"]]
        return [link.intf1.isUp(), link.intf2.isUp()]

    def _isolation_liveness(self, switch: str) -> dict[str, Any]:
        """A persistent isolation is not a process crash or state corruption."""

        liveness: dict[str, Any] = {
            "switch": switch,
            "switch_process_stopped": False,
            "state_corruption": False,
        }
        try:
            self._root_command(["ovs-vsctl", "--timeout=3", "show"])
            flows = self._root_command(
                ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch]
            )
            if not flows.strip():
                liveness["state_corruption"] = True
        except Exception as error:
            liveness["switch_process_stopped"] = True
            liveness["probe_error"] = f"{type(error).__name__}: {error}"
        return liveness

    def fault(self) -> dict[str, Any]:
        def apply():
            if self.net is None:
                raise RuntimeError("network is not set up")
            if self.faulted:
                raise RuntimeError("outage already injected; reset first")
            fault_plan = self.plan["fault"]
            for edge in fault_plan["edges"]:
                self.net.configLinkStatus(edge["left"], edge["right"], "down")
            self.faulted = True
            check = self.check_fault()
            return {
                "kind": fault_plan["kind"],
                "switch": fault_plan["switch"],
                "edges": fault_plan["edges"],
                "state": "down",
                **check,
            }

        return self._run("fault", apply)

    def reset(self) -> dict[str, Any]:
        def apply():
            if self.net is None:
                raise RuntimeError("network is not set up")
            if not self.faulted:
                raise RuntimeError("nothing is faulted; reset requires an outage")
            for edge in self.plan["fault"]["reset_edges"]:
                self.net.configLinkStatus(edge["left"], edge["right"], "up")
            self.faulted = False
            self._install_baseline()
            return self.readiness()

        return self._run("baseline-reset", apply)

    def cleanup(self) -> dict[str, Any]:
        def stop():
            status = "stopped" if self.net is not None else "already-stopped"
            if self.net is not None:
                self.net.stop()
                self.net = None
                self.links = {}
            remaining = self._count_remaining(
                ["ip", "-j", "link", "show"],
                set(cleanup_inventory(self.scenario)["interfaces"]),
            )
            if remaining is None:
                return {"status": "unverified", "reason": "interface inspection failed"}
            bridges = self._remaining_bridges()
            if bridges is None:
                return {
                    "status": "unverified",
                    "reason": "could not verify OVS cleanup state",
                    "remaining_interfaces": remaining,
                }
            return {
                "status": status,
                "remaining_interfaces": remaining,
                "remaining_bridges": bridges,
            }

        result = self._run("cleanup", stop)
        return {
            "verified": result.get("status") in {"stopped", "already-stopped"}
            and not result.get("remaining_interfaces")
            and not result.get("remaining_bridges"),
            **result,
        }

    def _count_remaining(self, command: list[str], names: set[str]) -> list[str] | None:
        try:
            inventory = json.loads(self._root_command(command))
        except Exception:
            return None
        if not isinstance(inventory, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("ifname"), str)
            for row in inventory
        ):
            return None
        return sorted(names & {row["ifname"] for row in inventory})

    def _remaining_bridges(self) -> list[str] | None:
        try:
            listed = self._root_command(["ovs-vsctl", "list-br"])
        except Exception:
            return None
        owned = set(self.plan["topology"]["switches"])
        return sorted(owned & set(listed.splitlines()))


def main() -> int:
    import argparse
    import json

    from .scenarios import CLASSES, make_scenario

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class-id", choices=sorted(CLASSES), required=True)
    parser.add_argument("--placement", type=int, default=0)
    args = parser.parse_args()
    scenario = make_scenario(args.class_id, args.placement)
    print(json.dumps(substrate_plan(scenario), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
