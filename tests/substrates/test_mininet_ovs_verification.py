from pathlib import Path

from mininet_ai.outcomes import CleanupStatus
from mininet_ai.substrates import CleanupTarget, MininetOVSCleanupVerifier
from mininet_ai.substrates.mininet_ovs.state import ProcessOwner
from tests.substrates.test_mininet_ovs_runtime import mininet_plan

STATE_PATH = Path("/run/mininet-ai/mininet-ovs.json")


def target() -> CleanupTarget:
    return CleanupTarget(
        plan=mininet_plan(),
        processes=(ProcessOwner.current(),),
        active_state_path=STATE_PATH,
    )


def verifier(**overrides):
    probes = {
        "interface_exists": lambda name: False,
        "bridge_exists": lambda name: False,
        "ovs_available": lambda: None,
        "path_exists": lambda path: False,
        "process_alive": lambda process: False,
    }
    probes.update(overrides)
    return MininetOVSCleanupVerifier(**probes)


def test_healthy_teardown_verifies_declared_local_resources():
    outcome = verifier().verify(target())

    assert outcome.status == CleanupStatus.VERIFIED
    assert outcome.evidence_gaps == ()
    assert any("planned interfaces absent" in item for item in outcome.evidence)
    assert any("planned OVS bridges absent" in item for item in outcome.evidence)
    assert any("controller artifacts absent" in item for item in outcome.evidence)
    assert any("tracked process identities stopped" in item for item in outcome.evidence)
    assert "active runtime state released" in outcome.evidence


def test_known_leftovers_are_incomplete_not_unknown():
    process = target().processes[0]
    outcome = verifier(
        interface_exists=lambda name: name == "h1-eth0",
        bridge_exists=lambda name: name == "s1",
        path_exists=lambda path: path in {Path("/tmp/c0.log"), STATE_PATH},
        process_alive=lambda owner: owner == process,
    ).verify(target())

    assert outcome.status == CleanupStatus.INCOMPLETE
    assert "interface:h1-eth0" in outcome.remaining_resources
    assert "bridge:s1" in outcome.remaining_resources
    assert "artifact:/tmp/c0.log" in outcome.remaining_resources
    assert f"process:{process.pid}" in outcome.remaining_resources
    assert f"state:{STATE_PATH}" in outcome.remaining_resources
    assert outcome.evidence_gaps


def test_failed_inspection_is_unknown_even_with_known_residue():
    def broken_bridge(name: str) -> bool:
        if name == "s1":
            raise OSError("ovs-vsctl failed")
        return False

    outcome = verifier(
        interface_exists=lambda name: name == "h1-eth0",
        bridge_exists=broken_bridge,
    ).verify(target())

    assert outcome.status == CleanupStatus.UNKNOWN
    assert "interface:h1-eth0" in outcome.remaining_resources
    assert "bridge:s1" in outcome.unknown_resources
    assert any("ovs-vsctl failed" in gap for gap in outcome.evidence_gaps)


def test_unavailable_ovs_is_unknown_not_absent():
    def unavailable() -> None:
        raise OSError("ovs-vsctl failed")

    outcome = verifier(ovs_available=unavailable).verify(target())

    assert outcome.status == CleanupStatus.UNKNOWN
    assert "bridge:s1" in outcome.unknown_resources
    assert "bridge:s2" in outcome.unknown_resources
    assert any("ovs-vsctl failed" in gap for gap in outcome.evidence_gaps)


def test_process_identity_is_checked_instead_of_process_name():
    process = target().processes[0]
    seen: list[ProcessOwner] = []

    def alive(owner: ProcessOwner) -> bool:
        seen.append(owner)
        return False

    outcome = verifier(process_alive=alive).verify(target())

    assert outcome.status == CleanupStatus.VERIFIED
    assert seen == [process]
