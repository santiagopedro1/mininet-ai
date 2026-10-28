#!/usr/bin/env python3
"""Opt-in, recorded Mininet/OVS harness for the diamond recovery scenario.

The harness owns setup, diagnostic flows, fault injection, baseline reset and
cleanup. It never makes a model decision or installs the model's recovery
bundle. Start it interactively so the in-memory Mininet run remains alive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from examples.diamond_recovery import (
    HOST_ADDRESSES,
    HOST_MACS,
    INITIAL_RULES,
    INTER_SWITCH_LINKS,
    LINK_ENDPOINTS,
    PORTS,
    RECOVERY_RULES,
    collect_observation_round,
    static_neighbors,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DiamondLiveHarness:
    """A single owned Mininet network with durable JSONL operation evidence."""

    def __init__(self, evidence_dir: Path) -> None:
        self.evidence_dir = evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = evidence_dir / "harness.jsonl"
        self.rounds_path = evidence_dir / "rounds.jsonl"
        self.net: Any | None = None
        self.links: dict[str, Any] = {}
        self.failed_link: str | None = None
        self.previous_outcomes: list[dict[str, Any]] = []
        self.round_history: list[dict[str, Any]] = []
        self.round_number = 0

    def _event(self, operation: str, status: str, **data: Any) -> dict[str, Any]:
        record = {
            "observedAt": _now(),
            "operation": operation,
            "status": status,
            **data,
        }
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        if operation in {"fault", "baseline-reset", "cleanup"}:
            self.previous_outcomes.append(record)
        return record

    def _run(self, operation: str, action):
        try:
            result = action()
        except Exception as error:
            self._event(operation, "failed", error=f"{type(error).__name__}: {error}")
            raise
        self._event(operation, "succeeded", result=result)
        return result

    @staticmethod
    def _root_command(arguments: list[str]) -> str:
        result = subprocess.run(arguments, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(
                f"{arguments!r} failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout

    def setup(self) -> dict[str, Any]:
        def apply():
            if self.net is not None:
                raise RuntimeError("this harness already owns a Mininet run")
            if sys.platform != "linux" or not all(
                shutil.which(command) for command in ("ovs-ofctl", "ovs-vsctl", "ip")
            ):
                raise RuntimeError("live setup requires Linux and Open vSwitch tools")
            if not hasattr(os, "geteuid") or os.geteuid() != 0:
                raise RuntimeError("live Mininet setup requires root")
            self._root_command(["ovs-vsctl", "show"])
            for switch in ("s1", "s2", "s3", "s4"):
                exists = subprocess.run(
                    ["ovs-vsctl", "br-exists", switch],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if exists.returncode == 0:
                    raise RuntimeError(
                        f"refusing to replace existing OVS bridge {switch!r}"
                    )
            planned_interfaces = {
                f"{switch}-eth{port}"
                for switch, peers in PORTS.items()
                for port in range(1, len(peers) + 1)
            } | {"h1-eth0", "h2-eth0"}
            for interface in planned_interfaces:
                exists = subprocess.run(
                    ["ip", "-j", "link", "show", "dev", interface],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if exists.returncode == 0:
                    raise RuntimeError(
                        f"refusing to replace existing interface {interface!r}"
                    )
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
            net.addHost("h1", ip="10.0.0.1/24", mac=HOST_MACS["h1"])
            net.addHost("h2", ip="10.0.0.2/24", mac=HOST_MACS["h2"])
            for switch in ("s1", "s2", "s3", "s4"):
                net.addSwitch(
                    switch,
                    cls=OVSSwitch,
                    failMode="secure",
                    protocols="OpenFlow13",
                )
            links = (
                ("h1-s1", "h1", "s1", 0, 1),
                ("s1-s2", "s1", "s2", 2, 1),
                ("s2-s4", "s2", "s4", 2, 2),
                ("s1-s3", "s1", "s3", 3, 1),
                ("s3-s4", "s3", "s4", 2, 3),
                ("s4-h2", "s4", "h2", 1, 0),
            )
            for name, left, right, left_port, right_port in links:
                kwargs = {"cls": TCLink}
                if left_port:
                    kwargs["port1"] = left_port
                if right_port:
                    kwargs["port2"] = right_port
                self.links[name] = net.addLink(net.get(left), net.get(right), **kwargs)
            net.build()
            self.net = net
            try:
                net.start()
            except Exception:
                self.cleanup()
                raise
            self._configure_hosts()
            self._install_baseline()
            return {
                "switches": ["s1", "s2", "s3", "s4"],
                "controller": None,
                "addressAliases": HOST_ADDRESSES,
                "staticNeighbors": static_neighbors(),
                "initialRules": len(INITIAL_RULES),
                "tableMiss": "drop",
            }

        return self._run("setup", apply)

    def _configure_hosts(self) -> None:
        assert self.net is not None
        for host_name in ("h1", "h2"):
            host = self.net.get(host_name)
            interface = f"{host_name}-eth0"
            for address in HOST_ADDRESSES[host_name].values():
                if address == HOST_ADDRESSES[host_name]["active"]:
                    continue
                self._host_command(
                    host, ["ip", "addr", "add", f"{address}/24", "dev", interface]
                )
        for host_name, neighbors in static_neighbors().items():
            host = self.net.get(host_name)
            interface = f"{host_name}-eth0"
            for address, mac in neighbors.items():
                self._host_command(
                    host,
                    [
                        "ip",
                        "neigh",
                        "replace",
                        address,
                        "lladdr",
                        mac,
                        "nud",
                        "permanent",
                        "dev",
                        interface,
                    ],
                )

    @staticmethod
    def _host_command(host: Any, arguments: list[str]) -> str:
        output, error, status = host.pexec(arguments)
        if status:
            raise RuntimeError(
                f"host command {arguments!r} failed: {(error or output).strip()}"
            )
        return output

    def _install_baseline(self) -> None:
        for switch in ("s1", "s2", "s3", "s4"):
            self._root_command(["ovs-ofctl", "-O", "OpenFlow13", "del-flows", switch])
            self._root_command(
                [
                    "ovs-ofctl",
                    "-O",
                    "OpenFlow13",
                    "add-flow",
                    switch,
                    "cookie=0x1100,priority=0,actions=drop",
                ]
            )
        for rule in INITIAL_RULES:
            match = ",".join(f"{key}={value}" for key, value in rule["match"].items())
            actions = ",".join(rule["actions"])
            flow = (
                f"cookie=0x{rule['cookie']:x},priority=100,idle_timeout=0,hard_timeout=0,"
                f"{match},actions={actions}"
            )
            self._root_command(
                ["ovs-ofctl", "-O", "OpenFlow13", "add-flow", rule["switch"], flow]
            )

    def verify_ports(self) -> dict[str, Any]:
        if self.net is None:
            raise RuntimeError("network is not set up")
        expected = {
            switch: {
                f"{switch}-eth{number}": number for number in range(1, len(peers) + 1)
            }
            for switch, peers in PORTS.items()
        }
        actual = {}
        for switch_name, expected_ports in expected.items():
            switch = self.net.get(switch_name)
            actual[switch_name] = {
                interface.name: number
                for interface, number in switch.ports.items()
                if number > 0 and interface.name != "lo"
            }
            if actual[switch_name] != expected_ports:
                raise RuntimeError(
                    f"deployed port mismatch for {switch_name}: "
                    f"expected {expected_ports}, got {actual[switch_name]}"
                )
            show = self._root_command(
                ["ovs-ofctl", "-O", "OpenFlow13", "show", switch_name]
            )
            deployed = {
                name: int(number)
                for number, name in re.findall(
                    r"^\s*(\d+)\(([^)]+)\):", show, re.MULTILINE
                )
                if name != "LOCAL"
            }
            if deployed != expected_ports:
                raise RuntimeError(
                    f"OpenFlow port mismatch for {switch_name}: "
                    f"expected {expected_ports}, got {deployed}"
                )
        return actual

    def inspect(self) -> dict[str, Any]:
        if self.net is None:
            raise RuntimeError("network is not set up")
        dumps = {
            switch: self._root_command(
                ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch]
            )
            for switch in ("s1", "s2", "s3", "s4")
        }
        if any("NORMAL" in output for output in dumps.values()):
            raise RuntimeError("NORMAL forwarding fallback found")

        def matching_rules(rules):
            states = []
            for rule in rules:
                cookie_token = f"cookie=0x{rule['cookie']:x}"
                lines = [
                    line
                    for line in dumps[rule["switch"]].splitlines()
                    if cookie_token in line
                ]
                expected = [
                    f"priority={rule['priority']}",
                    *(
                        f"{key}={value}"
                        for key, value in rule["match"].items()
                        if key != "dl_type"
                    ),
                    f"actions={','.join(rule['actions']) if isinstance(rule['actions'], list) else rule['actions']}",
                ]
                exact = False
                for line in lines:
                    match = all(token in line for token in expected)
                    for timeout_name in ("idle_timeout", "hard_timeout"):
                        timeout_value = re.search(
                            rf"(?:,|\s){timeout_name}=(\d+)", line
                        )
                        if (
                            timeout_value is not None
                            and int(timeout_value.group(1)) != 0
                        ):
                            match = False
                    if rule["match"]["dl_type"] == 2048:
                        match = match and any(
                            token in line
                            for token in ("ip,", "dl_type=0x800", "dl_type=2048")
                        )
                    exact = exact or match
                states.append("present" if exact else "wrong" if lines else "missing")
            return states

        initial_states = matching_rules(INITIAL_RULES)
        recovery_states = matching_rules(RECOVERY_RULES)
        if any(state != "present" for state in initial_states):
            raise RuntimeError(
                "initial policy inspection failed: "
                + ", ".join(
                    f"cookie {rule['cookie']}={state}"
                    for rule, state in zip(INITIAL_RULES, initial_states, strict=True)
                    if state != "present"
                )
            )
        miss_drop = all(
            any(
                "cookie=0x1100" in line
                and "priority=0" in line
                and "actions=drop" in line
                for line in output.splitlines()
            )
            for output in dumps.values()
        )
        if not miss_drop:
            raise RuntimeError("table-miss drop rule is missing")
        policy = (
            "verified-recovery"
            if all(state == "present" for state in recovery_states)
            else "verified-primary"
            if not any(state == "present" for state in recovery_states)
            else "partial-or-unknown"
        )
        return {
            "ports": self.verify_ports(),
            "flows": dumps,
            "tableMiss": "drop",
            "initialPolicy": "verified",
            "recoveryRules": recovery_states,
            "currentPolicy": policy,
        }

    def readiness(self) -> dict[str, Any]:
        def check():
            if self.net is None:
                raise RuntimeError("network is not set up")
            ports = self.verify_ports()
            policy = self.inspect()
            aliases = {}
            neighbors = {}
            for host_name in ("h1", "h2"):
                host = self.net.get(host_name)
                addresses = self._host_command(
                    host, ["ip", "-j", "addr", "show", "dev", f"{host_name}-eth0"]
                )
                address_data = json.loads(addresses)[0]["addr_info"]
                aliases[host_name] = sorted(
                    item["local"]
                    for item in address_data
                    if item.get("family") == "inet"
                )
                if not set(HOST_ADDRESSES[host_name].values()) <= set(
                    aliases[host_name]
                ):
                    raise RuntimeError(
                        f"host aliases missing on {host_name}: {aliases[host_name]}"
                    )
                link_data = json.loads(
                    self._host_command(
                        host,
                        ["ip", "-j", "link", "show", "dev", f"{host_name}-eth0"],
                    )
                )[0]
                if link_data.get("address", "").lower() != HOST_MACS[host_name]:
                    raise RuntimeError(f"host MAC mismatch on {host_name}: {link_data}")
                neighbor_output = self._host_command(
                    host, ["ip", "-j", "neigh", "show", "dev", f"{host_name}-eth0"]
                )
                neighbors[host_name] = json.loads(neighbor_output)
                learned = {
                    item.get("dst"): item.get("lladdr") for item in neighbors[host_name]
                }
                if any(
                    learned.get(ip) != mac
                    for ip, mac in static_neighbors()[host_name].items()
                ):
                    raise RuntimeError(
                        f"static neighbor verification failed on {host_name}"
                    )
                permanent = set()
                for item in neighbors[host_name]:
                    state = item.get("state", [])
                    states = [state] if isinstance(state, str) else state
                    if "PERMANENT" in states:
                        permanent.add(item.get("dst"))
                if not set(static_neighbors()[host_name]) <= permanent:
                    raise RuntimeError(
                        f"static neighbors are not permanent on {host_name}"
                    )
            modes = {
                switch: self._root_command(
                    ["ovs-vsctl", "get", "Bridge", switch, "fail_mode"]
                ).strip()
                for switch in ("s1", "s2", "s3", "s4")
            }
            if any(mode.strip('"') != "secure" for mode in modes.values()):
                raise RuntimeError(f"not all switches are fail-secure: {modes}")
            controllers = {
                switch: self._root_command(
                    ["ovs-vsctl", "get-controller", switch]
                ).strip()
                for switch in modes
            }
            if any(controllers.values()):
                raise RuntimeError(
                    f"unexpected controller/learning fallback: {controllers}"
                )
            protocols = {
                switch: self._root_command(
                    ["ovs-vsctl", "get", "Bridge", switch, "protocols"]
                ).strip()
                for switch in modes
            }
            if any("OpenFlow13" not in value for value in protocols.values()):
                raise RuntimeError(
                    f"OpenFlow13 is not enabled on every switch: {protocols}"
                )
            return {
                "ports": ports,
                "policy": policy["currentPolicy"],
                "aliases": aliases,
                "staticNeighbors": neighbors,
                "failModes": modes,
                "controllers": controllers,
                "protocols": protocols,
                "interSwitchLinks": self._link_states(),
                "tableMiss": "drop",
                "normalFallback": False,
            }

        return self._run("readiness", check)

    def fault(self, link_name: str) -> dict[str, Any]:
        if link_name not in {"s1-s2", "s2-s4"}:
            raise ValueError("only primary links s1-s2 and s2-s4 may be faulted")

        def apply():
            if self.net is None:
                raise RuntimeError("network is not set up")
            if self.failed_link is not None:
                raise RuntimeError(
                    f"link {self.failed_link} is already faulted; reset first"
                )
            left, right = LINK_ENDPOINTS[link_name]
            self.net.configLinkStatus(left, right, "down")
            self.failed_link = link_name
            return {
                "link": link_name,
                "state": "down",
                "otherPrimaryLinkUnaffected": True,
            }

        return self._run("fault", apply)

    def _link_states(self) -> dict[str, str]:
        state = {}
        for name in INTER_SWITCH_LINKS:
            link = self.links[name]
            state[name] = "up" if link.intf1.isUp() and link.intf2.isUp() else "down"
        return state

    def _probe(
        self,
        source: str,
        destination: str,
        source_ip: str,
        destination_ip: str,
        timeout: float,
    ):
        if self.net is None:
            raise RuntimeError("network is not set up")
        started = time.monotonic()
        observed_at = _now()
        try:
            process = self.net.get(source).popen(
                [
                    "ping",
                    "-n",
                    "-c",
                    "1",
                    "-W",
                    str(int(timeout)),
                    "-I",
                    source_ip,
                    destination_ip,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            output, _ = process.communicate(timeout=timeout + 0.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            return {
                "sent": None,
                "received": None,
                "state": "timeout",
                "error": "probe command exceeded the local collection timeout",
                "observedAt": observed_at,
                "durationSeconds": max(0.0, time.monotonic() - started),
            }
        except Exception as error:
            return {
                "sent": None,
                "received": None,
                "state": "unknown",
                "error": f"{type(error).__name__}: {error}",
                "observedAt": observed_at,
                "durationSeconds": max(0.0, time.monotonic() - started),
            }
        match = re.search(
            r"(\d+) packets transmitted, (\d+) (?:packets )?received", output
        )
        sent = int(match.group(1)) if match else None
        received = int(match.group(2)) if match else None
        state = (
            "unknown"
            if sent is None or received is None
            else "success"
            if received > 0
            else "timeout"
        )
        return {
            "sent": sent,
            "received": received,
            "state": state,
            "observedAt": observed_at,
            "durationSeconds": max(0.0, time.monotonic() - started),
        }

    def probe_round(
        self, *, scheduled_at: str | None = None, cadence_lateness_seconds: float = 0.0
    ) -> dict[str, Any]:
        self.round_number += 1
        scheduled_at = scheduled_at or _now()
        payload = collect_observation_round(
            self._probe,
            observed_at=_now(),
            link_states=self._link_states(),
            policy_state={**self.inspect(), "failedLink": self.failed_link},
            previous_outcomes=getattr(self, "previous_outcomes", []),
            previous_rounds=tuple(self.round_history[-2:]),
            round_number=self.round_number,
            scheduled_at=scheduled_at,
        )
        payload["cadenceLatenessSeconds"] = max(0.0, cadence_lateness_seconds)
        if cadence_lateness_seconds > 0.1:
            payload["history"]["gaps"].append(
                {
                    "code": "history.scheduled-round-late",
                    "latenessSeconds": cadence_lateness_seconds,
                }
            )
        self.round_history.append(payload)
        with self.rounds_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True) + "\n")
        self._event("probe-round", "succeeded", round=payload)
        return payload

    def probe_rounds(self, count: int) -> list[dict[str, Any]]:
        if count < 1:
            raise ValueError("round count must be positive")
        rounds = []
        next_tick = time.monotonic()
        for index in range(count):
            if index:
                next_tick += 1.0
                time.sleep(max(0.0, next_tick - time.monotonic()))
            started = time.monotonic()
            scheduled_at = _now()
            rounds.append(
                self.probe_round(
                    scheduled_at=scheduled_at,
                    cadence_lateness_seconds=max(0.0, started - next_tick)
                    if index
                    else 0.0,
                )
            )
        return rounds

    def confirm_active_recovery(self) -> dict[str, Any]:
        if self.failed_link is None:
            raise RuntimeError(
                "fault one primary link before active recovery confirmation"
            )
        started = time.monotonic()
        successes = 0
        rounds = []
        while time.monotonic() - started <= 5:
            directions = (("h1", "h2"), ("h2", "h1"))

            def active_probe(direction):
                source, destination = direction
                return self._probe(
                    source,
                    destination,
                    HOST_ADDRESSES[source]["active"],
                    HOST_ADDRESSES[destination]["active"],
                    1.0,
                )

            with ThreadPoolExecutor(max_workers=2) as workers:
                active = list(workers.map(active_probe, directions))
            links = self._link_states()
            if links[self.failed_link] != "down":
                raise RuntimeError(
                    "failed primary link came back up during confirmation"
                )
            successes = (
                successes + 1
                if all(
                    result.get("received") is not None and result["received"] > 0
                    for result in active
                )
                else 0
            )
            record = {
                "observedAt": _now(),
                "activeConfirmation": active,
                "linkStates": links,
                "consecutiveSuccesses": successes,
            }
            rounds.append(record)
            if successes == 3 and time.monotonic() - started <= 5:
                result = {
                    "status": "verified",
                    "elapsedSeconds": time.monotonic() - started,
                    "rounds": rounds,
                }
                self._event("active-recovery-confirmation", "succeeded", result=result)
                return result
            time.sleep(1)
        result = {
            "status": "failed",
            "reason": "three consecutive bidirectional active probe rounds not observed within five seconds",
            "rounds": rounds,
        }
        self._event("active-recovery-confirmation", "failed", result=result)
        return result

    def reset(self) -> dict[str, Any]:
        def apply():
            if self.net is None:
                raise RuntimeError("network is not set up")
            for link_name, (left, right) in LINK_ENDPOINTS.items():
                if link_name in self.links:
                    self.net.configLinkStatus(left, right, "up")
            self.failed_link = None
            self._install_baseline()
            self.round_history.clear()
            self.round_number = 0
            return self.readiness()

        return self._run("baseline-reset", apply)

    def cleanup(self) -> dict[str, Any]:
        def stop():
            if self.net is None:
                return {"status": "already-stopped"}
            names = [
                f"s{switch}-eth{port}"
                for switch, peers in PORTS.items()
                for port in range(1, len(peers) + 1)
            ] + ["h1-eth0", "h2-eth0"]
            self.net.stop()
            self.net = None
            remaining = []
            for name in names:
                result = subprocess.run(
                    ["ip", "-j", "link", "show", "dev", name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    remaining.append(name)
            bridges = []
            probe = subprocess.run(
                ["ovs-vsctl", "show"], capture_output=True, text=True, check=False
            )
            if probe.returncode:
                return {
                    "status": "unverified",
                    "reason": "could not verify OVS cleanup state",
                    "remainingInterfaces": remaining,
                    "remainingBridges": None,
                }
            for switch in ("s1", "s2", "s3", "s4"):
                result = subprocess.run(
                    ["ovs-vsctl", "br-exists", switch],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    bridges.append(switch)
            status = "verified" if not remaining and not bridges else "unverified"
            return {
                "status": status,
                "remainingInterfaces": remaining,
                "remainingBridges": bridges,
            }

        try:
            result = stop()
        except Exception as error:
            result = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
            self._event("cleanup", "failed", result=result)
            return result
        self._event("cleanup", result["status"], result=result)
        return result

    def command_loop(self) -> None:
        try:
            self.setup()
        except Exception:
            if self.net is not None:
                self.cleanup()
            raise
        print(
            "Commands: readiness, inspect, fault s1-s2|s2-s4, probe [rounds], confirm, reset, cleanup, quit"
        )
        try:
            while self.net is not None:
                try:
                    parts = input("diamond> ").strip().split()
                    if not parts:
                        continue
                    command, *arguments = parts
                    if command == "readiness":
                        print(json.dumps(self.readiness(), indent=2))
                    elif command == "inspect":
                        print(
                            json.dumps(
                                self._run("policy-inspection", self.inspect), indent=2
                            )
                        )
                    elif command == "fault" and len(arguments) == 1:
                        print(json.dumps(self.fault(arguments[0]), indent=2))
                    elif command == "probe":
                        count = int(arguments[0]) if arguments else 1
                        print(json.dumps(self.probe_rounds(count), indent=2))
                    elif command == "confirm":
                        print(json.dumps(self.confirm_active_recovery(), indent=2))
                    elif command == "reset":
                        print(json.dumps(self.reset(), indent=2))
                    elif command in {"cleanup", "quit"}:
                        print(json.dumps(self.cleanup(), indent=2))
                        break
                    else:
                        print("invalid command; see command list")
                except (ValueError, RuntimeError) as error:
                    print(f"operation failed: {error}", file=sys.stderr)
        finally:
            if self.net is not None:
                print(json.dumps(self.cleanup(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        required=True,
        help="directory for JSONL harness and probe records",
    )
    args = parser.parse_args()
    DiamondLiveHarness(args.evidence).command_loop()


if __name__ == "__main__":
    main()
