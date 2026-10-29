"""Trusted-slice cleanup verification must preserve unknown and remaining state."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from examples.diamond_recovery.cleanup import DiagnosisCleanupObserver
from examples.diamond_recovery.diagnosis import evaluate_runtime
from mininet_ai.compiler import compile_experiment
from mininet_ai.outcomes import CleanupOutcome
from mininet_ai.substrates import FakeSubstrateRuntime
from tests.substrates.test_mininet_ovs_observations import ScriptedExecutor


def process(proc, pid, parent, group, start, namespace):
    entry = proc / str(pid)
    (entry / "ns").mkdir(parents=True, exist_ok=True)
    fields = ["S", str(parent), str(group), *(["0"] * 16), str(start)]
    (entry / "stat").write_text(f"{pid} (test shell) {' '.join(fields)}")
    link = entry / "ns/net"
    link.unlink(missing_ok=True)
    link.symlink_to(namespace)


def remove_process(proc, pid):
    entry = proc / str(pid)
    (entry / "stat").unlink()
    (entry / "ns/net").unlink()
    (entry / "ns").rmdir()
    entry.rmdir()


@pytest.fixture
def owned(tmp_path):
    plan = compile_experiment(
        Path(__file__).parents[2] / "examples/diamond-recovery/experiment.yaml"
    )
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("test-boot")
    process(proc, os.getpid(), 1, os.getpgrp(), 1, "net:[runner]")
    nodes = []
    for index, name in enumerate(("h1", "h2", "s1", "s2", "s3", "s4")):
        pid = os.getpid() + 1000 + index
        process(proc, pid, os.getpid(), pid, 10 + index, f"net:[{name}]")
        nodes.append(SimpleNamespace(name=name, pid=pid))
    network = SimpleNamespace(hosts=nodes[:2], switches=nodes[2:], controllers=[])
    executor = ScriptedExecutor()
    executor.add(("ip", "-j", "link", "show"), stdout="[]")
    executor.add(("ovs-vsctl", "--timeout=5", "list-br"), stdout="")
    observer = DiagnosisCleanupObserver(
        plan, tmp_path / "cleanup", proc=proc, executor=executor
    )
    observer.attach(network)
    return plan, proc, nodes, executor, observer


def test_absence_after_owned_capture_is_verified(owned):
    _, proc, nodes, _, observer = owned
    observer.capture()
    for node in nodes:
        remove_process(proc, node.pid)
    outcome = observer.verify()
    assert outcome.verified
    assert not outcome.evidence_gaps


def test_live_or_reparented_descendant_is_not_verified(owned):
    _, proc, nodes, _, observer = owned
    root = nodes[0].pid
    child = root + 2000
    process(proc, child, root, child, 99, "net:[different]")
    observer.capture()
    for node in nodes:
        remove_process(proc, node.pid)
    process(proc, child, 1, child, 99, "net:[different]")
    outcome = observer.verify()
    assert outcome.status.value == "incomplete"
    assert f"process:{child}:99" in outcome.remaining_resources
    assert not any("descendants" in evidence for evidence in outcome.evidence)


@pytest.mark.parametrize("scope", ["group", "namespace"])
def test_untracked_orphan_in_owned_group_or_namespace_is_not_verified(owned, scope):
    _, proc, nodes, _, observer = owned
    root = nodes[0].pid
    child = root + 2000
    process(
        proc,
        child,
        1,
        root if scope == "group" else child,
        99,
        "net:[h1]" if scope == "namespace" else "net:[different]",
    )
    observer.capture()
    for node in nodes:
        remove_process(proc, node.pid)
    assert f"process:{child}:99" in observer.verify().remaining_resources


def test_reused_pid_with_unrelated_identity_does_not_claim_ownership(owned):
    _, proc, nodes, _, observer = owned
    observer.capture()
    for node in nodes:
        remove_process(proc, node.pid)
    process(proc, nodes[0].pid, 1, os.getpgrp(), 9999, "net:[runner]")
    assert observer.verify().verified


def test_unreadable_process_inventory_is_not_verified(owned):
    _, proc, nodes, _, observer = owned
    observer.capture()
    for node in nodes:
        remove_process(proc, node.pid)
    (proc / str(os.getpid()) / "stat").write_text("malformed")
    assert observer.verify().status.value == "unknown"


@pytest.mark.parametrize("which", ["owned-node", "runner"])
def test_missing_initial_namespace_identity_cannot_verify_cleanup(
    owned, tmp_path, which
):
    plan, proc, nodes, executor, _ = owned
    pid = nodes[0].pid if which == "owned-node" else os.getpid()
    (proc / str(pid) / "ns/net").unlink()
    observer = DiagnosisCleanupObserver(
        plan, tmp_path / "missing-namespace", proc=proc, executor=executor
    )
    observer.attach(
        SimpleNamespace(hosts=nodes[:2], switches=nodes[2:], controllers=[])
    )
    observer.capture()
    for node in nodes:
        entry = proc / str(node.pid)
        (entry / "stat").unlink()
        (entry / "ns/net").unlink(missing_ok=True)
        (entry / "ns").rmdir()
        entry.rmdir()
    outcome = observer.verify()
    assert outcome.status.value == "unknown"
    assert any("namespace" in gap for gap in outcome.evidence_gaps)


@pytest.mark.parametrize(
    "kind", ["interface", "bridge", "unreadable", "missing-capture"]
)
def test_remaining_and_unverified_resources_fail_closed(owned, kind):
    _, proc, nodes, executor, observer = owned
    if kind != "missing-capture":
        observer.capture()
    for node in nodes:
        remove_process(proc, node.pid)
    if kind == "interface":
        executor.add(("ip", "-j", "link", "show"), stdout='[{"ifname":"s1-eth1"}]')
    elif kind == "bridge":
        executor.add(("ovs-vsctl", "--timeout=5", "list-br"), stdout="s1\n")
    elif kind == "unreadable":
        executor.add(("ovs-vsctl", "--timeout=5", "list-br"), returncode=1)
    outcome = observer.verify()
    assert not outcome.verified
    assert outcome.remaining_resources or outcome.evidence_gaps


@pytest.mark.parametrize("choice, passed", [("degraded", True), ("healthy", False)])
def test_runtime_preserves_legacy_unknown_but_records_independent_verification(
    owned, tmp_path, choice, passed
):
    plan, proc, nodes, _, observer = owned

    class LegacyRuntime(FakeSubstrateRuntime):
        def teardown(self, run_id):
            result = super().teardown(run_id)
            for node in nodes:
                remove_process(proc, node.pid)
            unknown = CleanupOutcome()
            return result.model_copy(
                update={
                    "cleanup": unknown,
                    "run": result.run.model_copy(update={"cleanup": unknown}),
                }
            )

    class Transport:
        def get_json(self, url, **kwargs):
            return {"status": "ok"} if url.endswith("/health") else {}

        def post_json(self, url, payload, **kwargs):
            return {
                "answers": {"next_action": {"type": "choice", "choice": choice}},
                "usage": {"output_tokens": 0},
            }

    summary = evaluate_runtime(
        plan,
        LegacyRuntime(),
        tmp_path / "evaluation",
        decision_transport=Transport(),
        cleanup_observer=observer,
    )
    assert summary["passed"] is passed, summary
    assert summary["cleanup"]["status"] == "verified"
    assert summary["runtimeCleanup"]["status"] == "unknown"
    report = json.loads((tmp_path / "evaluation/runtime-report.json").read_text())
    assert report["cleanup"]["status"] == "unknown"
    assert report["operation"] == "succeeded"
