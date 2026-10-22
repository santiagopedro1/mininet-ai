"""THROWAWAY Linux containment counterexample probe; not production supervision.

Run: python mininet_ai/agents/agno/containment_prototype.py
Only creates/signals its own disposable process groups. No Provider or Agno calls.
"""

import ctypes
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = str(Path(__file__).resolve())
BOUND = 3.0  # Experiment control, NOT a proposed production timeout.


def prctl(option, value):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(option, value, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl failed")


def record(root, event, **fields):
    # Observational trace only: no crash-durability guarantee.
    row = dict(event=event, pid=os.getpid(), ppid=os.getppid(),
               monotonic_ns=time.monotonic_ns(), **fields)
    with (root / "events.jsonl").open("a") as stream:
        stream.write(json.dumps(row) + "\n")


def rows(root):
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def until(predicate):
    deadline = time.monotonic() + BOUND
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def live(pid):
    try:
        # Zombies have exited; they cannot execute or retain DB connections.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


def launch(role, root, mode, parent=0, **kwargs):
    return subprocess.Popen([sys.executable, HERE, role, str(root), mode, str(parent)], **kwargs)


def worker(root, mode, parent):
    record(root, "worker_prearm")
    if mode.startswith("prearm") and not until(lambda: (root / "arm").exists()):
        raise RuntimeError("fixture did not release prearm barrier")
    prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG: tied to creator THREAD.
    record(root, "watchdog_armed")
    if mode != "prearm_unguarded" and os.getppid() != parent:
        record(root, "parent_mismatch_rejected")
        return
    if mode == "descendants":
        child = launch("child", root, mode)
        record(root, "tracked_child", child=child.pid)
        if not until(lambda: any(r["event"] == "grandchild_ready" for r in rows(root))):
            raise RuntimeError("descendant fixture not ready")
    record(root, "ready")
    while True:
        time.sleep(0.1)


def supervisor(root, mode):
    def create():
        child = launch("worker", root, mode, os.getpid())
        record(root, "worker_created", worker=child.pid)
        if mode == "creator_thread" and not until(
            lambda: any(r["event"] == "ready" for r in rows(root))
        ):
            raise RuntimeError("worker did not become ready")

    if mode == "creator_thread":
        thread = threading.Thread(target=create)
        thread.start()
        thread.join()
        record(root, "creator_thread_exited")
    else:
        create()
    while True:
        time.sleep(0.1)


def descendant(root, role, mode):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if role == "child":
        child = launch("grandchild", root, mode)
        record(root, "child_ready", child=child.pid)
    else:
        record(root, "grandchild_ready")
    while True:
        time.sleep(0.1)


def probe(root, mode):
    root.mkdir()
    supervisor_process = launch("supervisor", root, mode, start_new_session=True)
    observations = {}
    try:
        event = "worker_prearm" if mode.startswith("prearm") else "ready"
        assert until(lambda: any(r["event"] == event for r in rows(root))), rows(root)
        worker_pid = next(r["pid"] for r in rows(root) if r["event"] == "worker_prearm")
        if mode == "creator_thread":
            assert until(lambda: any(r["event"] == "creator_thread_exited" for r in rows(root)))
            observations["worker_exited_after_creator_thread"] = until(lambda: not live(worker_pid))
            observations["supervisor_still_live"] = live(supervisor_process.pid)
        else:
            supervisor_process.kill()
            supervisor_process.wait(timeout=BOUND)
            if mode.startswith("prearm"):
                (root / "arm").touch()
                assert until(lambda: any(r["event"] == "watchdog_armed" for r in rows(root)))
            if mode == "prearm_unguarded":
                assert until(lambda: any(r["event"] == "ready" for r in rows(root)))
                observations["orphan_wrongly_reached_ready"] = live(worker_pid)
            else:
                observations["worker_exit_observed"] = until(lambda: not live(worker_pid))
            if mode == "prearm_guarded":
                observations["parent_mismatch_rejected"] = any(
                    r["event"] == "parent_mismatch_rejected" for r in rows(root))
                observations["no_ready"] = not any(r["event"] == "ready" for r in rows(root))
            if mode == "descendants":
                descendant_pids = [r["pid"] for r in rows(root)
                                   if r["event"] in ("child_ready", "grandchild_ready")]
                observations["descendants_survive_worker_death"] = all(map(live, descendant_pids))
                os.killpg(supervisor_process.pid, signal.SIGTERM)
                time.sleep(0.1)
                observations["descendants_ignore_term"] = all(map(live, descendant_pids))
                os.killpg(supervisor_process.pid, signal.SIGKILL)
                observations["external_group_kill_stops_descendants"] = until(
                    lambda: not any(map(live, descendant_pids)))
        observations["trace"] = rows(root)
        return {"case": mode, "observations": observations}
    finally:
        # A fresh session/group belongs exclusively to this fixture. Never signal
        # a group obtained from an external PID or arbitrary user configuration.
        try:
            os.killpg(supervisor_process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        supervisor_process.wait(timeout=BOUND)
        fixture_pids = {r["pid"] for r in rows(root)} | {supervisor_process.pid}
        assert until(lambda: not any(map(live, fixture_pids))), "fixture cleanup unverified"
        for pid in fixture_pids:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


def main():
    if len(sys.argv) > 1:
        role, path, mode, parent = sys.argv[1:]
        root = Path(path)
        if role == "supervisor":
            supervisor(root, mode)
        elif role == "worker":
            worker(root, mode, int(parent))
        else:
            descendant(root, role, mode)
        return
    assert sys.platform == "linux", "Linux-only disposable probe"
    prctl(36, 1)  # Become a subreaper solely to collect our orphan fixtures.
    with tempfile.TemporaryDirectory(prefix="containment-prototype-", dir="/tmp/opencode") as tmp:
        cases = [probe(Path(tmp) / mode, mode) for mode in (
            "prearm_unguarded", "prearm_guarded", "creator_thread", "descendants")]
    checks = {f'{case["case"]}:{key}': value
              for case in cases for key, value in case["observations"].items() if key != "trace"}
    result = {
        "prototype": True, "verdict": "candidate insufficient",
        "platform": platform.platform(),
        "source_sha256": hashlib.sha256(Path(HERE).read_bytes()).hexdigest(),
        "python": sys.version, "experiment_bound_seconds": BOUND,
        "source": "Linux prctl parent-death + post-arm parent check; no durable guardian",
        "checks": checks, "cases": cases,
    }
    print(json.dumps(result, indent=2))
    assert all(checks.values()), checks


if __name__ == "__main__":
    main()
