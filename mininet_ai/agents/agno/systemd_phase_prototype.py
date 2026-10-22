"""THROWAWAY real offline Agno worker inside authorized transient user services.

Run with .venv/bin/python -P mininet_ai/agents/agno/systemd_phase_prototype.py
Requires the pinned historical prototype Git object; no fetching or live models.
"""

import fcntl
import hashlib
import importlib
import importlib.metadata
import json
import multiprocessing as mp
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import containment_prototype as fixture
from systemd_containment_prototype import command, properties

PIN = "d96928ecd8cb608b69942e4fb08c957134621b28"
HELPERS = ("admission_failure_prototype.py", "learning_gate_prototype.py",
           "integrated_storage_prototype.py", "storage_prototype.py")
HERE = str(Path(__file__).resolve())


def load_helpers(directory):
    sys.path.insert(0, str(directory))
    return importlib.import_module("admission_failure_prototype")


def append(path, row):
    with path.open("a") as stream:
        stream.write(json.dumps(row, default=str) + "\n")


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def supervisor(root, helpers):
    af = load_helpers(helpers)
    af.prepare(root)
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    worker = ctx.Process(target=af.admission_worker, args=(str(root), child,
                        {"latch_mode": "write", "dispatch_check": True}))
    worker.start()
    child.close()
    append(root / "replies.jsonl", af.reply(parent, 8))
    append(root / "replies.jsonl", {"worker_pid": worker.pid, "supervisor_pid": os.getpid()})
    consumed = set()
    while True:
        for path in sorted(root.glob("command-*.json")):
            if path.name not in consumed:
                parent.send(json.loads(path.read_text()))
                consumed.add(path.name)
        if parent.poll(0.01):
            append(root / "replies.jsonl", parent.recv())


def gate_available(root):
    with (root / "PROTOTYPE-wipe-me.learning.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle, fcntl.LOCK_UN)
        return True


class Quarantine:
    """MODELED controller policy, not a production coordinator or durable latch."""

    def __init__(self):
        self.failed = False
        self.exit_verified = False

    def cancel(self):
        self.failed = True

    def admit(self, *, independent=False):
        # No cache replacement/resume for a failed run, even after verified exit.
        return self.exit_verified if independent else not self.failed


def probe(root, helpers, scenario):
    af = load_helpers(helpers)
    root.mkdir()
    unit = f"mininet-ai-containment-prototype-{uuid.uuid4().hex}.service"
    requests = []
    policy = Quarantine()

    def send(row):
        requests.append(row)
        target = root / f"command-{len(requests):03}.json"
        staging = root / "staging.json"
        staging.write_text(json.dumps(row))
        staging.rename(target)

    def reply_for(identity):
        assert fixture.until(lambda: any(r.get("id") == identity
                                        for r in read_rows(root / "replies.jsonl")))
        return next(r for r in read_rows(root / "replies.jsonl") if r.get("id") == identity)

    cgroup = None
    try:
        start = command("systemd-run", "--user", f"--unit={unit}", "--service-type=exec",
                        "--property=KillMode=control-group", "--property=TimeoutStopSec=1s",
                        "--property=SendSIGKILL=yes", "--property=RuntimeMaxSec=30s",
                        sys.executable, "-P", HERE, "supervisor", str(root), str(helpers))
        assert fixture.until(lambda: any("worker_pid" in r
                                        for r in read_rows(root / "replies.jsonl")))
        send(af.command("seed", "add", memory_id="seed"))
        seed = reply_for("seed")
        assert seed["actionable_success"]
        send(af.command("warm-session", "session", text="establish session before outcome"))
        assert reply_for("warm-session")["actionable_success"]
        path = root / "PROTOTYPE-wipe-me.sqlite3"
        if scenario == "outcome":
            send(af.command("active", "outcome", state={"action_result": "known-installed"},
                            finalize_hold=True))
            marker = af.wait_event(path, "finalize-hold", "active")
        else:
            send(af.command("active", "automatic", background="indefinite"))
            marker = af.wait_event(path, "background-entered", "active")
        send(af.command("sibling", "session", agent="B", text="active sibling",
                        finalize_hold=True))
        af.wait_event(path, "finalize-hold", "sibling")
        send(af.command("queued", "session", text="queued on active owner"))
        # Same-worker roundtrip proves that the preceding command reached its
        # loop, but not its busy owner thread. Real background/executors remain.
        send({"route": "gate-probe"})
        assert fixture.until(lambda: any("gate_probe" in r
                                        for r in read_rows(root / "replies.jsonl")))
        held_before = not gate_available(root)
        before = properties(unit)
        cgroup = Path("/sys/fs/cgroup") / before["ControlGroup"].lstrip("/")
        pids = {int(pid) for pid in (cgroup / "cgroup.procs").read_text().split()}
        policy.cancel()
        late_denied = not policy.admit()
        independent_denied_before = not policy.admit(independent=True)
        stop = command("systemctl", "--user", "stop", unit)
        actual_exit = fixture.until(lambda: not any(map(fixture.live, pids)))
        empty = fixture.until(lambda: not cgroup.exists() or
                              "populated 0" in (cgroup / "cgroup.events").read_text())
        # Deliberately withhold evidence from the modeled decision boundary,
        # despite retaining actual host observations for fixture cleanup.
        policy.exit_verified = actual_exit and empty and scenario != "unverified"
        independent_after = policy.admit(independent=True)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            events = [{"seq": seq, "id": op, "kind": kind, "payload": json.loads(payload)}
                      for seq, op, kind, payload in conn.execute(
                          "SELECT * FROM prototype_events ORDER BY seq")]
            integrity = conn.execute("PRAGMA integrity_check").fetchall()
        db = af.database(root)
        memories = af.memories(db, "trial:A")
        session = db.get_session(session_id="trial:A")
        db.close()
        checks = {
            "real_seed_committed": bool(memories),
            "gate_denied_while_active": held_before,
            "active_and_sibling_no_terminal": not any(
                e["kind"] == "terminal" and e["id"] in {"active", "sibling"} for e in events),
            "queued_never_started": not any(
                e["kind"] == "admitted" and e["id"] == "queued" for e in events),
            "modeled_late_denied": late_denied,
            "modeled_independent_denied_until_exit": independent_denied_before,
            "actual_process_exit": actual_exit,
            "actual_cgroup_empty": empty,
            "sqlite_integrity": integrity == [("ok",)],
            "gate_physically_available_after_exit": gate_available(root),
            "modeled_failed_run_no_replacement": not policy.admit(),
            "modeled_unknown_exit_denial" if scenario == "unverified" else
            "modeled_verified_independent_eligibility":
                not independent_after if scenario == "unverified" else independent_after,
        }
        if scenario == "outcome":
            checks["committed_outcome_preserved"] = (
                session is not None and
                session.session_data["session_state"]["action_result"] == "known-installed")
        after = properties(unit)
        load = command("systemctl", "--user", "show", unit, "--property=LoadState")
        after["LoadStateObservation"] = load["stdout"].strip()
        return {"scenario": scenario, "checks": checks, "unit": unit,
                "before": before, "after": after, "pids": sorted(pids),
                "requests": requests, "replies": read_rows(root / "replies.jsonl"),
                "marker": marker, "events": events, "memories": memories,
                "session": session.to_dict() if session else None,
                "start": start, "stop": stop,
                "files": sorted(p.name for p in root.iterdir())}
    finally:
        command("systemctl", "--user", "stop", unit, check=False)
        command("systemctl", "--user", "reset-failed", unit, check=False)
        if cgroup is not None:
            assert fixture.until(lambda: not cgroup.exists() or
                                 "populated 0" in (cgroup / "cgroup.events").read_text())


def main():
    if len(sys.argv) > 1:
        supervisor(Path(sys.argv[2]), Path(sys.argv[3]))
        return
    with tempfile.TemporaryDirectory(prefix="systemd-phase-prototype-", dir="/tmp/opencode") as tmp:
        root = Path(tmp)
        helpers = root / "pinned-helpers"
        helpers.mkdir()
        for name in HELPERS:
            data = subprocess.run(["git", "show", f"{PIN}:mininet_ai/agents/agno/{name}"],
                                  capture_output=True, check=True).stdout
            (helpers / name).write_bytes(data)
        cases = [probe(root / name, helpers, name)
                 for name in ("admission", "reasoning", "outcome", "unverified")]
        helper_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in helpers.iterdir() if p.is_file()}
    with sqlite3.connect(":memory:") as conn:
        sqlite_source = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
    print(json.dumps({"prototype": True, "cases": cases, "helper_pin": PIN,
                      "helper_hashes": helper_hashes, "python": sys.version,
                      "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
                      "sqlite_source": sqlite_source,
                      "packages": {name: importlib.metadata.version(name)
                                   for name in ("agno", "sqlalchemy")},
                      "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (Path(__file__), Path(fixture.HERE),
                                            Path(__file__).with_name("systemd_containment_prototype.py"))},
                      "limits": "Real offline Agno/SQLite; modeled controller quarantine, no remote evidence"},
                     indent=2, default=str))
    assert all(value for case in cases for value in case["checks"].values())


if __name__ == "__main__":
    main()
