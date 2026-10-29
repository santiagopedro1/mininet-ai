"""Independent, read-only cleanup evidence for the trusted diagnosis harness.

This is not a general Mininet cleanup guarantee: it is restricted to current-only
diagnosis with no capabilities, no custom agent factories and synchronous trusted
setup commands. It never kills a process or removes a network resource.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.outcomes import CleanupOutcome, CleanupStatus
from mininet_ai.specification.models import ResourceKind
from mininet_ai.substrates.mininet_ovs.observations import (
    CommandExecutor,
    LocalCommandExecutor,
)


@dataclass(frozen=True)
class _Process:
    pid: int
    parent: int
    group: int
    started: int
    namespace: str | None


class DiagnosisCleanupObserver:
    """Capture owned identities before teardown and independently inspect absence."""

    def __init__(
        self,
        plan: DeploymentPlan,
        evidence: Path,
        *,
        proc: Path = Path("/proc"),
        executor: CommandExecutor | None = None,
    ) -> None:
        if any(
            agent.capabilities
            or agent.decision_scenario is None
            or agent.decision_scenario.get("inputMode") != "current-only"
            for agent in plan.agents
        ):
            raise ValueError(
                "cleanup observer is restricted to capability-free current-only diagnosis"
            )
        self.evidence = evidence
        self.proc = proc
        self.executor = executor or LocalCommandExecutor()
        self.interfaces = {
            item.name
            for item in plan.resources
            if item.kind in {ResourceKind.PORT, ResourceKind.SWITCH}
        }
        self.bridges = {
            item.name for item in plan.resources if item.kind == ResourceKind.SWITCH
        }
        self.nodes = {
            item.name
            for item in plan.resources
            if item.kind
            in {ResourceKind.HOST, ResourceKind.SWITCH, ResourceKind.CONTROLLER}
        }
        self.roots: dict[int, int] = {}
        self.tracked: dict[int, int] = {}
        self.groups: set[int] = set()
        self.namespaces: set[str] = set()
        self.gaps: list[str] = []
        self.boot: str | None = None
        self.attached = False
        self.captured = False

    def _write(self, name: str, data: Any) -> None:
        self.evidence.mkdir(parents=True, exist_ok=True)
        (self.evidence / name).write_text(
            json.dumps(
                {
                    "observedAt": datetime.now(UTC).isoformat(),
                    "data": data,
                },
                indent=2,
            )
            + "\n"
        )

    def _processes(self) -> dict[int, _Process]:
        result = {}
        try:
            entries = list(self.proc.iterdir())
        except OSError as error:
            self.gaps.append(f"process inventory unreadable: {error}")
            return result
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            try:
                raw = (entry / "stat").read_text()
                fields = raw[raw.rfind(")") + 2 :].split()
                try:
                    namespace = os.readlink(entry / "ns/net")
                except FileNotFoundError:
                    namespace = None
                result[int(entry.name)] = _Process(
                    int(entry.name),
                    int(fields[1]),
                    int(fields[2]),
                    int(fields[19]),
                    namespace,
                )
            except FileNotFoundError:
                continue
            except (OSError, ValueError, IndexError) as error:
                self.gaps.append(
                    f"process identity unreadable for pid {entry.name}: {error}"
                )
        return result

    def _owned(self, processes: dict[int, _Process]) -> dict[int, _Process]:
        owned = {
            pid: item
            for pid, item in processes.items()
            if self.roots.get(pid) == item.started
            or self.tracked.get(pid) == item.started
            or item.group in self.groups
            or item.namespace in self.namespaces
        }
        while True:
            children = {
                pid: item for pid, item in processes.items() if item.parent in owned
            }
            if children.keys() <= owned.keys():
                return owned
            owned.update(children)

    def attach(self, network: Any) -> None:
        if self.attached:
            raise RuntimeError("cleanup observer is already attached")
        self.attached = True
        try:
            self.boot = (self.proc / "sys/kernel/random/boot_id").read_text().strip()
            if not self.boot:
                self.gaps.append("boot identity was empty")
        except OSError as error:
            self.gaps.append(f"boot identity unavailable: {error}")
        processes = self._processes()
        own = processes.get(os.getpid())
        if own is None or own.namespace is None:
            self.gaps.append("runner process namespace/group could not be established")
        nodes = [*network.hosts, *network.switches, *network.controllers]
        if {node.name for node in nodes} != self.nodes:
            self.gaps.append(
                "owned shell inventory does not cover the deployed plan's nodes"
            )
        if not nodes:
            self.gaps.append("no owned Mininet node identities supplied")
        for node in nodes:
            pid = getattr(node, "pid", None)
            item = processes.get(pid) if isinstance(pid, int) else None
            if item is None or pid == os.getpid():
                self.gaps.append(f"unverifiable owned shell identity for {node.name}")
                continue
            self.roots[item.pid] = item.started
            if item.namespace is None:
                self.gaps.append(
                    f"owned namespace identity unavailable for {node.name}"
                )
            if own is not None and item.group != own.group:
                self.groups.add(item.group)
            if (
                own is not None
                and own.namespace is not None
                and item.namespace is not None
                and item.namespace != own.namespace
            ):
                self.namespaces.add(item.namespace)
        self.tracked.update(
            {pid: item.started for pid, item in self._owned(processes).items()}
        )
        self._write(
            "cleanup-initial.json",
            {
                "bootId": self.boot,
                "roots": self.roots,
                "groups": sorted(self.groups),
                "namespaces": sorted(self.namespaces),
                "gaps": self.gaps,
            },
        )

    def capture(self) -> None:
        """Retain identities while the network and its owned shells still exist."""
        if not self.attached:
            self.gaps.append(
                "cleanup observer was never attached to a deployed network"
            )
        owned = self._owned(self._processes())
        self.tracked.update({pid: item.started for pid, item in owned.items()})
        self.captured = True
        self._write(
            "cleanup-before.json",
            {
                "ownedProcesses": [asdict(item) for item in owned.values()],
                "gaps": self.gaps,
            },
        )

    def verify(self) -> CleanupOutcome:
        """Observe only; unknown observations never become successful cleanup."""
        if not self.captured:
            self.gaps.append(
                "owned process identities were not captured before teardown"
            )
        try:
            if (
                self.boot
                != (self.proc / "sys/kernel/random/boot_id").read_text().strip()
            ):
                self.gaps.append("boot identity changed or was unavailable")
        except OSError as error:
            self.gaps.append(f"boot identity unreadable after teardown: {error}")
        remaining = [
            f"process:{item.pid}:{item.started}"
            for item in self._owned(self._processes()).values()
        ]
        unknown = []
        evidence = []
        for label, arguments, names in (
            ("interfaces", ("ip", "-j", "link", "show"), self.interfaces),
            ("bridges", ("ovs-vsctl", "--timeout=5", "list-br"), self.bridges),
        ):
            try:
                result = self.executor.run(arguments, timeout_seconds=5)
                if result.returncode:
                    raise RuntimeError(f"status {result.returncode}")
                live = (
                    {item["ifname"] for item in json.loads(result.stdout)}
                    if label == "interfaces"
                    else set(result.stdout.splitlines())
                )
                remaining.extend(sorted(names & live))
                evidence.append(f"independently inspected owned {label} after teardown")
            except Exception as error:
                self.gaps.append(f"{label} absence unverifiable: {error}")
                unknown.extend(sorted(names))
        if self.gaps:
            unknown.extend(
                f"process:{pid}:{started}" for pid, started in self.tracked.items()
            )
        elif not any(name.startswith("process:") for name in remaining):
            evidence.append(
                "owned shell identities, descendants, process groups and private network namespaces are absent"
            )
        outcome = CleanupOutcome(
            status=CleanupStatus.INCOMPLETE
            if remaining
            else CleanupStatus.UNKNOWN
            if self.gaps
            else CleanupStatus.VERIFIED,
            remaining_resources=tuple(sorted(set(remaining))),
            unknown_resources=tuple(sorted(set(unknown))),
            evidence=tuple(evidence),
            evidence_gaps=tuple(dict.fromkeys(self.gaps)),
        )
        self._write("cleanup-after.json", outcome.model_dump(mode="json"))
        return outcome
