"""Scoped post-teardown verification for owner-led Mininet/OVS cleanup."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from mininet_ai.outcomes import CleanupOutcome, CleanupStatus
from mininet_ai.specification.models import ControllerType, ResourceKind
from mininet_ai.substrates.mininet_ovs.state import ProcessOwner

if TYPE_CHECKING:
    from mininet_ai.compiler.models import DeploymentPlan, PlannedController


def _interface_exists(name: str) -> bool:
    try:
        socket.if_nametoindex(name)
    except OSError:
        return False
    return True


def _ovs_available() -> None:
    completed = subprocess.run(
        ("ovs-vsctl", "--timeout=5", "list-br"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise OSError(detail or "ovs-vsctl is unavailable")


def _bridge_exists(name: str) -> bool:
    completed = subprocess.run(
        ("ovs-vsctl", "--timeout=5", "br-exists", name),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    # OVS implementations disagree on the absent exit status (1 historically,
    # 2 on newer Fedora builds). The caller checks OVS availability first, so a
    # nonzero answer here means the bridge is absent.
    return completed.returncode == 0


def _path_exists(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


@dataclass(frozen=True)
class CleanupTarget:
    """Resources owned by one run that teardown must make absent."""

    plan: DeploymentPlan
    processes: tuple[ProcessOwner, ...]
    active_state_path: Path


@runtime_checkable
class CleanupVerifier(Protocol):
    def verify(self, target: CleanupTarget) -> CleanupOutcome:
        """Return a scoped cleanup outcome for the target's owned resources."""
        ...


class MininetOVSCleanupVerifier:
    """Verify the declared local resource set without claiming host-wide absence.

    The live owner creates the planned switch bridges, port interfaces, controller
    artifacts, process groups, and active runtime state. This verifier checks those
    exact resources after teardown. It intentionally does not search for arbitrary
    processes, unrelated bridges, or remote Provider work.
    """

    def __init__(
        self,
        *,
        interface_exists: Callable[[str], bool] = _interface_exists,
        bridge_exists: Callable[[str], bool] = _bridge_exists,
        ovs_available: Callable[[], None] = _ovs_available,
        path_exists: Callable[[Path], bool] = _path_exists,
        process_alive: Callable[[ProcessOwner], bool] = ProcessOwner.is_alive,
    ) -> None:
        self._interface_exists = interface_exists
        self._bridge_exists = bridge_exists
        self._ovs_available = ovs_available
        self._path_exists = path_exists
        self._process_alive = process_alive

    def verify(self, target: CleanupTarget) -> CleanupOutcome:
        resources = target.plan.resources
        interfaces = sorted(
            {
                resource.name
                for resource in resources
                if resource.kind in {ResourceKind.SWITCH, ResourceKind.PORT}
            }
        )
        bridges = sorted(
            resource.name
            for resource in resources
            if resource.kind == ResourceKind.SWITCH
        )
        artifacts = sorted(
            str(Path("/tmp") / f"{resource.name}.log")
            for resource in resources
            if resource.kind == ResourceKind.CONTROLLER
            and cast("PlannedController", resource).controller_type
            == ControllerType.BUILTIN
        )

        remaining: set[str] = set()
        unknown: set[str] = set()
        evidence: list[str] = []
        gaps: list[str] = []

        checked = 0
        for name in interfaces:
            try:
                present = self._interface_exists(name)
            except Exception as error:
                unknown.add(f"interface:{name}")
                gaps.append(f"could not inspect interface {name!r}: {error}")
                continue
            checked += 1
            if present:
                remaining.add(f"interface:{name}")
        if interfaces:
            if checked == len(interfaces):
                evidence.append(
                    "planned interfaces absent: " + ", ".join(interfaces)
                )
        else:
            evidence.append("no planned interfaces to verify")

        if bridges:
            try:
                self._ovs_available()
            except Exception as error:
                for name in bridges:
                    unknown.add(f"bridge:{name}")
                gaps.append(f"could not inspect OVS bridges: {error}")
            else:
                checked = 0
                for name in bridges:
                    try:
                        present = self._bridge_exists(name)
                    except Exception as error:
                        unknown.add(f"bridge:{name}")
                        gaps.append(
                            f"could not inspect OVS bridge {name!r}: {error}"
                        )
                        continue
                    checked += 1
                    if present:
                        remaining.add(f"bridge:{name}")
                if checked == len(bridges):
                    evidence.append(
                        "planned OVS bridges absent: " + ", ".join(bridges)
                    )
        else:
            evidence.append("no planned OVS bridges to verify")

        for process in target.processes:
            try:
                running = self._process_alive(process)
            except Exception as error:
                unknown.add(f"process:{process.pid}")
                gaps.append(
                    f"could not inspect tracked process {process.pid}: {error}"
                )
                continue
            if running:
                remaining.add(f"process:{process.pid}")
        if target.processes:
            evidence.append(
                "tracked process identities stopped: "
                + ", ".join(str(process.pid) for process in target.processes)
            )
        else:
            evidence.append("no tracked processes to verify")

        checked = 0
        for artifact in artifacts:
            try:
                present = self._path_exists(Path(artifact))
            except Exception as error:
                unknown.add(f"artifact:{artifact}")
                gaps.append(f"could not inspect controller artifact {artifact!r}: {error}")
                continue
            checked += 1
            if present:
                remaining.add(f"artifact:{artifact}")
        if artifacts:
            if checked == len(artifacts):
                evidence.append("controller artifacts absent: " + ", ".join(artifacts))
        else:
            evidence.append("no controller artifacts to verify")

        try:
            state_present = self._path_exists(target.active_state_path)
        except Exception as error:
            unknown.add(f"state:{target.active_state_path}")
            gaps.append(
                f"could not inspect active runtime state {target.active_state_path}: {error}"
            )
        else:
            if state_present:
                remaining.add(f"state:{target.active_state_path}")
            else:
                evidence.append("active runtime state released")

        if gaps:
            return CleanupOutcome(
                status=CleanupStatus.UNKNOWN,
                remaining_resources=tuple(sorted(remaining)),
                unknown_resources=tuple(sorted(unknown)),
                evidence=tuple(evidence),
                evidence_gaps=tuple(gaps),
            )
        if remaining:
            return CleanupOutcome(
                status=CleanupStatus.INCOMPLETE,
                remaining_resources=tuple(sorted(remaining)),
                evidence=tuple(evidence),
            )
        return CleanupOutcome(
            status=CleanupStatus.VERIFIED,
            evidence=tuple(evidence),
            evidence_gaps=(),
        )
