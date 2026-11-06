"""In-process graph/packet adapter. Scripted responses are never model evidence.

No Mininet, sockets, subprocesses or model clients are constructed here. Each
instance owns fresh forwarding, link state, traffic records and decision history.
"""

from __future__ import annotations

import asyncio
import time

from .runner import INSTRUCTIONS, NS


class AcceleratedClock:
    def __init__(self):
        self.origin = time.monotonic_ns()

    def now_ns(self):
        return (time.monotonic_ns() - self.origin) * 30

    async def sleep_until(self, tick):
        await asyncio.sleep(max(0, tick - self.now_ns()) / (30 * NS))


class ControlledScenarioRuntime:
    evidence_kind = "mechanism_validation"

    def __init__(self, *, failure: str | None = None):
        self.clock = AcceleratedClock()
        self.failure = failure

    async def setup(self, config, root):
        self.config, self.scenario = config, config.scenario_instance
        if self.scenario is None:
            raise ValueError("controlled graph requires a frozen scenario")
        self.forwarding = dict(self.scenario.initial_forwarding)
        self.down = set()
        self.history = []
        self.checks = []
        return {
            "initial_state": self.failure != "initial_state",
            "fresh_context": True,
            "automatic_backup": False,
            "topology_id": self.scenario.topology_id,
            "initial_forwarding": dict(self.forwarding),
            "backup_resources": [list(edge) for edge in self.scenario.links],
            "preinstalled_backup_forwarding": {
                node: target
                for node, target in self.scenario.backup_forwarding.items()
                if self.forwarding.get(node) == target
            },
            "state_owner": config.trial_id,
        }

    async def start_traffic(self):
        return self.clock.now_ns()

    def _delivers(self):
        # Independent directed packet walk, not the structural checker or a
        # treatment/status label. Link isolation remains active after all repairs.
        node, visited = "s1", set()
        while node != "h2":
            if node in visited or node not in self.forwarding:
                return False
            visited.add(node)
            target = self.forwarding[node]
            edge = frozenset((node, target))
            if edge in self.down or edge not in {
                frozenset(e) for e in self.scenario.links
            }:
                return False
            node = target
        return True

    def _check_fault(self):
        links = self.scenario.disabled_links
        result = {
            "tick_ns": self.clock.now_ns(),
            "both_endpoints_down": all(frozenset(edge) in self.down for edge in links),
            "disabled_links": [list(edge) for edge in links],
            "fault_kind": self.scenario.fault_kind,
            "switch_process_stopped": False,
            "state_corruption": False,
            "endpoints_survive": all(
                frozenset(edge) not in self.down
                for edge in self.scenario.links
                if "h1" in edge or "h2" in edge
            ),
        }
        self.checks.append(result)
        return result

    async def inject(self, start_ns):
        self.injection = start_ns
        if self.failure != "injection":
            self.down.update(frozenset(e) for e in self.scenario.disabled_links)
        self.history.append((start_ns, self._delivers()))
        return self._check_fault()

    async def observe_network(self):
        return {
            "switches": {
                node: {
                    "ports": {
                        str(port): frozenset((node, neighbor)) not in self.down
                        for neighbor, port in ports.items()
                    },
                    "flows": [{"out_port": ports[self.forwarding[node]]}]
                    if node in self.forwarding
                    else [],
                }
                for node, ports in self.scenario.ports.items()
            }
        }

    async def diagnose(self):
        state = (await self.observe_network())["switches"]["s1"]
        return {"switch": "s1", **state}

    async def decide(self, diagnostics, remaining_seconds):
        # Fixed controlled output, independently measured by the packet walk.
        choice = "bypass"
        if self.failure == "model":
            choice = "malformed-choice"
        return {
            "choice": choice,
            "confidence": 0.1,
            "request": {"instructions": INSTRUCTIONS, "diagnostics": diagnostics},
            "response": {"choice": choice},
            "tokens": {"input": 12, "output": 4, "reasoning": None},
        }

    async def execute(self, choice):
        if self.failure != "action":
            self.forwarding["s1"] = "s3"
        self.history.append((self.clock.now_ns(), self._delivers()))
        self._check_fault()
        return {"success": self.failure != "action"}

    async def decide_s2(self, context, remaining_seconds):
        # A frozen scripted downstream repair, not an effectiveness prediction.
        actions = []
        for node, target in self.scenario.backup_forwarding.items():
            ports = self.scenario.ports[node]
            previous = (
                "h1"
                if node == "s1"
                else next(
                    n for n, t in self.scenario.backup_forwarding.items() if t == node
                )
            )
            actions.append(
                {"switch": node, "in_port": ports[previous], "out_port": ports[target]}
            )
        repair = {"status": "repair", "actions": actions}
        if self.failure == "model":
            repair = {"status": "repair", "actions": []}
        return {
            "repair": repair,
            "request": context,
            "response": {"done": True, "repair": repair},
            "tokens": {"input": 20, "output": 30, "reasoning": None},
        }

    async def execute_s2(self, repair):
        if self.failure != "action":
            for action in repair["actions"]:
                ports = self.scenario.ports[action["switch"]]
                self.forwarding[action["switch"]] = next(
                    n for n, p in ports.items() if p == action["out_port"]
                )
        self.history.append((self.clock.now_ns(), self._delivers()))
        self._check_fault()
        return {"success": self.failure != "action"}

    async def service_signal(self, since_ns):
        await self.clock.sleep_until(since_ns + 1_100_000_000)
        return {"recovered": self._delivers()}

    async def cancel(self, phase):
        return {"local_cancel_requested": True, "remote_termination": "unknown"}

    async def collect(self, observation_end_ns):
        rate = self.config.packet_rate
        count = round((self.config.deadline_seconds + 10) * rate)
        packets, index, good = [], 0, True
        for seq in range(count):
            sent = self.injection - 5 * NS + round(seq * NS / rate)
            while index < len(self.history) and self.history[index][0] <= sent:
                good = self.history[index][1]
                index += 1
            packets.append(
                {
                    "seq": seq,
                    "send_ns": sent,
                    "arrivals_ns": [sent + 1_000_000] if good else [],
                }
            )
        self._check_fault()
        return {
            "packets": packets,
            "fault_checks": self.checks,
            "measurement_errors": ["controlled_essential_measurement_failure"]
            if self.failure == "essential_measurement"
            else [],
        }

    async def cleanup(self):
        self.forwarding.clear()
        self.down.clear()
        self.history.clear()
        return {"verified": True, "owned_state_released": True, "controlled_only": True}
