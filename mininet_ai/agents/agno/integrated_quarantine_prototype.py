"""THROWAWAY GH-97: integrated offline cancellation and durable quarantine.

Run: .venv/bin/python -P mininet_ai/agents/agno/integrated_quarantine_prototype.py
Only authorized UUID transient fixture services; no Provider/network calls.
"""

import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
import containment_prototype as fixture
from systemd_containment_prototype import command, properties
from systemd_phase_prototype import (
    HELPERS,
    PIN,
    append,
    gate_available,
    load_helpers,
    read_rows,
)

HERE = str(Path(__file__).resolve())


def connect(root):
    conn = sqlite3.connect(root / "PROTOTYPE-wipe-me.sqlite3", timeout=2)
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def audit(conn, kind, **payload):
    conn.execute("INSERT INTO prototype_controller_events(kind,payload) VALUES (?,?)",
                 (kind, json.dumps(payload)))


def state(root):
    with connect(root) as conn:
        return conn.execute("SELECT state FROM prototype_quarantine").fetchone()[0]


def process_identity():
    # Cooperative fixture identity, not a general PID-reuse/host-reboot protocol.
    stat = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return f"{os.getpid()}:{stat[19]}"


def cancel(root):
    with connect(root) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR IGNORE INTO prototype_failed_runs SELECT run FROM prototype_quarantine")
        conn.execute("UPDATE prototype_quarantine SET state='quarantined'")
        audit(conn, "cancel-durable")


def admit(root, run):
    # This is the actual fixture service-launch gate, also invoked in a fresh
    # process after controller loss. No caller may start a new service first.
    with connect(root) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT state,run FROM prototype_quarantine").fetchone()
        owner = conn.execute("SELECT owner FROM prototype_quarantine").fetchone()[0]
        failed_run = conn.execute("SELECT 1 FROM prototype_failed_runs WHERE run=?", (run,)).fetchone()
        allowed = current == ("open", run) and owner == process_identity()
        if current[0] == "contained" and run != current[1] and not failed_run:
            # Reserve a genuinely new fixture generation, never resume the old
            # run. Caller must perform the actual launch through guarded_start.
            conn.execute("UPDATE prototype_quarantine SET state='open',run=?,generation=?,invocation='',"
                         "owner=?,claimed=0", (run, run, process_identity()))
            allowed = True
        if failed_run:
            allowed = False
        audit(conn, "controller-admission", run=run, allowed=allowed, state=current[0])
        return allowed


def guarded_start(root, run, argv):
    if not admit(root, run):
        return {"denied": True, "state": state(root)}
    return command(*argv)


def variant(source):
    """Exact bounded edits to immutable baseline; retain both hashes in report."""
    def replace(old, new):
        nonlocal source
        assert source.count(old) == 1, old
        source = source.replace(old, new)

    replace("from contextlib import nullcontext", "from contextlib import contextmanager, nullcontext")
    insert = '''
def quarantine_closed(root):
    with sqlite3.connect(Path(root) / "PROTOTYPE-wipe-me.sqlite3", timeout=2) as conn:
        return conn.execute("SELECT state FROM prototype_quarantine").fetchone()[0] != "open"

@contextmanager
def dispatch_boundary(root, operation):
    with sqlite3.connect(Path(root) / "PROTOTYPE-wipe-me.sqlite3", timeout=2) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        allowed = conn.execute("SELECT state FROM prototype_quarantine").fetchone()[0] == "open"
        yield allowed
        conn.execute("INSERT INTO prototype_controller_events(kind,payload) VALUES (?,?)",
                     ("worker-dispatch", json.dumps({"operation": operation, "allowed": allowed})))

'''
    replace("MEMORY_FREE =", insert + "MEMORY_FREE =")
    replace('        self.route = "reason"', '        self.route = "reason"\n        self.foreground_hold = None')
    replace('        self.seen = [m.to_dict() for m in messages]',
            '        self.seen = [m.to_dict() for m in messages]\n'
            '        if self.foreground_hold is not None:\n            self.foreground_hold()')
    replace('    send_lock = threading.Lock()',
            '    send_lock = threading.Lock()\n    boundary_lock = threading.RLock()\n    dispatch_releases = {}')
    replace('        with latch_lock:', '        with boundary_lock, latch_lock:')
    replace('        # --- dispatch boundary:', '''        if cmd.get("dispatch_hold"):
            held = threading.Event()
            dispatch_releases[op] = held
            safe_event(op, "before-dispatch", {})
            held.wait(20)

        # --- dispatch boundary:''')
    replace('        with ledger.lock:\n            ledger.queued -= 1',
            '        with boundary_lock, ledger.lock, dispatch_boundary(root, op) as permitted:\n'
            '            ledger.queued -= 1')
    replace('            if latch.is_set() and dispatch_check:',
            '            if (latch.is_set() or not permitted) and dispatch_check:')
    replace('                errors: list[str] = []', '''                if isinstance(agent.model, RouteModel):
                    def foreground_hold():
                        emit("foreground-blocked", {})
                        release.wait(20)
                    agent.model.foreground_hold = foreground_hold if cmd.get("foreground_hold") else None

                errors: list[str] = []''')
    replace('                run_failed = bool(errors) or latch.is_set()',
            '                run_failed = bool(errors) or latch.is_set() or quarantine_closed(root)')
    replace('"actionable_success": not errors and not latch.is_set(),',
            '"actionable_success": not errors and not latch.is_set() and not quarantine_closed(root),')
    replace('    send({"ready": True, "config": config})', '''    if config.get("startup_hold"):
        send({"startup_blocked": True})
        while not (root / "release-startup").exists():
            time.sleep(0.01)
    if quarantine_closed(root):
        send({"startup_rejected": True})
        channel.close()
        return
    send({"ready": True, "config": config})''')
    replace('            if "release_background" in cmd:', '''            if cmd.get("route") == "cancel":
                with boundary_lock:
                    latch.set()
                safe_event("worker", "cancel-observed", {})
                start_monitor()
                send({"cancel_ack": True})
                continue
            if "release_dispatch" in cmd:
                dispatch_releases[cmd["release_dispatch"]].set()
                continue
            if "release_background" in cmd:''')
    replace('            if latch.is_set():\n                # Admission boundary',
            '            if latch.is_set() or quarantine_closed(root):\n                # Admission boundary')
    return source


def bridge(root, helpers, scenario):
    af = load_helpers(helpers)
    with connect(root) as conn:
        conn.execute("BEGIN IMMEDIATE")
        claim = conn.execute("UPDATE prototype_quarantine SET owner=?,claimed=1 "
                             "WHERE claimed=0 AND state='open'", (process_identity(),))
        if claim.rowcount != 1:
            raise RuntimeError("fixture controller ownership cannot be reclaimed")
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    worker = ctx.Process(target=af.admission_worker, args=(str(root), child,
                        {"startup_hold": scenario == "startup", "quiescence_bound": 4}))
    worker.start()
    child.close()
    append(root / "replies.jsonl", {"worker_pid": worker.pid, "controller_pid": os.getpid()})
    consumed = set()
    eof = False
    while True:
        for path in sorted(root.glob("command-*.json")):
            if path.name in consumed:
                continue
            consumed.add(path.name)
            request = json.loads(path.read_text())
            action = request["action"]
            if action == "cancel":
                cancel(root)
                if scenario != "startup":
                    parent.send({"route": "cancel"})
                append(root / "replies.jsonl", {"controller_cancel_committed": True})
            elif action == "submit":
                if admit(root, request["run"]):
                    parent.send(request["command"])
                else:
                    append(root / "replies.jsonl", {"id": request["command"]["id"],
                                                   "controller_denied": True})
            elif action == "worker-diagnostic" or action == "release":
                # Authorized adversarial fixture bypass, not a supported public
                # submission path: verifies the worker itself rejects late work.
                parent.send(request["command"])
        if not eof and parent.poll(0.01):
            try:
                append(root / "replies.jsonl", parent.recv())
            except EOFError:
                eof = True
        if eof:
            time.sleep(0.01)


def reconcile(root, observation):
    with connect(root) as conn:
        conn.execute("BEGIN IMMEDIATE")
        generation, invocation = conn.execute(
            "SELECT generation,invocation FROM prototype_quarantine").fetchone()
        valid = (observation.get("generation") == generation and
                 observation.get("invocation") == invocation and invocation != "" and
                 observation.get("processes_exited") is True and
                 observation.get("cgroup_empty") is True)
        audit(conn, "exit-observation", accepted=valid, observation=observation)
        if valid:
            conn.execute("UPDATE prototype_quarantine SET state='contained'")
        return valid


def probe(root, helpers, scenario):
    af = load_helpers(helpers)
    root.mkdir()
    af.prepare(root)
    run = uuid.uuid4().hex
    unit = f"mininet-ai-containment-prototype-{run}.service"
    with connect(root) as conn:
        conn.execute("CREATE TABLE prototype_quarantine(state TEXT,run TEXT,generation TEXT,invocation TEXT,"
                     "owner TEXT,claimed INTEGER)")
        conn.execute("CREATE TABLE prototype_controller_events(seq INTEGER PRIMARY KEY,kind TEXT,payload TEXT)")
        conn.execute("CREATE TABLE prototype_failed_runs(run TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO prototype_quarantine VALUES ('open',?,?, '',?,0)",
                     (run, run, process_identity()))
    requests = []

    def send(action, cmd=None):
        row = {"action": action, "run": run, "command": cmd}
        requests.append(row)
        target = root / f"command-{len(requests):03}.json"
        staging = root / "staging.json"
        staging.write_text(json.dumps(row))
        staging.rename(target)

    def wait(predicate):
        assert fixture.until(lambda: predicate(read_rows(root / "replies.jsonl"))), scenario

    def reply(identity):
        wait(lambda rr: any(r.get("id") == identity for r in rr))
        return next(r for r in read_rows(root / "replies.jsonl") if r.get("id") == identity)

    def submit(cmd):
        send("submit", cmd)

    checks = {}
    cgroup = None
    pids = set()
    try:
        start = guarded_start(root, run, ["systemd-run", "--user", f"--unit={unit}", "--service-type=exec",
                        "--property=KillMode=control-group", "--property=TimeoutStopSec=1s",
                        "--property=SendSIGKILL=yes", "--property=RuntimeMaxSec=45s",
                        sys.executable, "-P", HERE, "bridge", str(root), str(helpers), scenario])
        assert not start.get("denied")
        wait(lambda rr: any("worker_pid" in r for r in rr))
        before = properties(unit)
        invocation = command("systemctl", "--user", "show", unit, "--property=InvocationID",
                             "--value")["stdout"].strip()
        assert invocation
        with connect(root) as conn:
            conn.execute("UPDATE prototype_quarantine SET invocation=?", (invocation,))
            owner_identity = conn.execute("SELECT owner FROM prototype_quarantine").fetchone()[0]
        cgroup = Path("/sys/fs/cgroup") / before["ControlGroup"].lstrip("/")
        if scenario == "startup":
            wait(lambda rr: any(r.get("startup_blocked") for r in rr))
        else:
            wait(lambda rr: any(r.get("ready") for r in rr))
            submit(af.command("seed", "add", memory_id="seed"))
            assert reply("seed")["actionable_success"]
            submit(af.command("warm", "session", text="establish session"))
            assert reply("warm")["actionable_success"]
        path = root / "PROTOTYPE-wipe-me.sqlite3"
        if scenario == "healthy":
            submit(af.command("overlap-B", "overlap", agent="B", text="overlap"))
            submit(af.command("overlap-C", "overlap", agent="C", text="overlap"))
            b, c = reply("overlap-B"), reply("overlap-C")
            submit(af.command("again-B", "session", agent="B", text="continuity"))
            again = reply("again-B")
            checks["independent_overlap_completed"] = b["actionable_success"] and c["actionable_success"]
            checks["distinct_owner_threads"] = b["owner"] != c["owner"]
            checks["stable_agent_and_owner"] = (b["owner"], b["agent_object"]) == (
                again["owner"], again["agent_object"])
        elif scenario != "startup":
            if scenario == "admission":
                submit(af.command("active", "reason", dispatch_hold=True))
                af.wait_event(path, "before-dispatch", "active")
            elif scenario == "foreground":
                submit(af.command("active", "reason", foreground_hold=True))
                af.wait_event(path, "foreground-blocked", "active")
            elif scenario == "outcome":
                submit(af.command("active", "outcome", state={"action_result": "known-installed"},
                                  finalize_hold=True))
                af.wait_event(path, "finalize-hold", "active")
            else:
                submit(af.command("active", "automatic", background="block"))
                af.wait_event(path, "background-entered", "active")
            submit(af.command("sibling", "session", agent="B", finalize_hold=True))
            af.wait_event(path, "finalize-hold", "sibling")
            submit(af.command("queued", "session"))
            send("release", {"route": "gate-probe"})
            wait(lambda rr: any("gate_probe" in r for r in rr))
            checks["worker_gate_held_while_active"] = not gate_available(root)
        if scenario != "controller-loss-before-cancel":
            send("cancel")
            wait(lambda rr: any(r.get("controller_cancel_committed") for r in rr))
            checks["quarantine_committed_before_ack"] = state(root) == "quarantined"
            submit(af.command("late-controller", "session"))
            checks["actual_controller_late_refusal"] = reply("late-controller").get("controller_denied") is True
            if scenario == "startup":
                (root / "release-startup").touch()
                wait(lambda rr: any(r.get("startup_rejected") for r in rr))
                checks["startup_never_ready"] = not any(r.get("ready") for r in read_rows(root / "replies.jsonl"))
            else:
                wait(lambda rr: any(r.get("cancel_ack") for r in rr))
                send("worker-diagnostic", af.command("late-worker", "session"))
                checks["actual_worker_late_refusal"] = "rejected" in reply("late-worker")
                if scenario not in ("healthy", "controller-loss"):
                    if scenario == "admission":
                        send("release", {"release_dispatch": "active"})
                    elif scenario == "outcome":
                        send("release", {"release_finalize": "active"})
                    else:
                        send("release", {"release_background": "active"})
                    send("release", {"release_finalize": "sibling"})
                    active, queued, sibling = reply("active"), reply("queued"), reply("sibling")
                    checks["queued_refused_at_actual_dispatch"] = queued.get("dispatch") == "refused"
                    checks["active_not_actionable"] = not active.get("actionable_success", False)
                    checks["sibling_not_actionable"] = sibling.get("actionable_success") is False
        pids = {int(pid) for pid in (cgroup / "cgroup.procs").read_text().split()}
        if scenario.startswith("controller-loss"):
            stopped = command("systemctl", "--user", "kill", "--kill-whom=main", "--signal=SIGKILL", unit)
        else:
            stopped = command("systemctl", "--user", "stop", unit)
        exited = fixture.until(lambda: not any(map(fixture.live, pids)))
        empty = fixture.until(lambda: not cgroup.exists() or
                              "populated 0" in (cgroup / "cgroup.events").read_text())
        checks["actual_process_exit"] = exited
        checks["actual_cgroup_empty"] = empty
        if scenario == "controller-loss-before-cancel":
            marker = root / "unexpected-resumed-generation"
            resume_unit = f"mininet-ai-containment-prototype-{uuid.uuid4().hex}.service"
            argv = ["systemd-run", "--user", "--wait", "--collect", f"--unit={resume_unit}",
                    "--service-type=exec", "--property=RuntimeMaxSec=5s", sys.executable, "-P", "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).write_text('unexpected resume')"]
            (root / "new-launch.json").write_text(json.dumps({"run": run, "argv": argv}))
            try:
                fresh = subprocess.run([sys.executable, "-P", HERE, "new-run", str(root)],
                                       capture_output=True, text=True, check=True, timeout=10)
                checks["fresh_controller_cannot_resume_before_quarantine_commit"] = (
                    state(root) == "open" and json.loads(fresh.stdout).get("denied") is True
                    and not marker.exists())
            finally:
                command("systemctl", "--user", "stop", resume_unit, check=False)
                command("systemctl", "--user", "reset-failed", resume_unit, check=False)
            # Recovery may close admission and record failure, never reclaim the
            # dead controller's live ownership or resume its volatile context.
            cancel(root)
        observation = {"generation": run, "invocation": invocation,
                       "processes_exited": exited, "cgroup_empty": empty}
        # Simulate an observer that cannot currently supply valid evidence.
        checks["missing_exit_evidence_rejected"] = not reconcile(root, {})
        checks["stale_incarnation_rejected"] = not reconcile(root, {**observation, "invocation": "stale"})
        checks["contradictory_observation_rejected"] = not reconcile(
            root, {**observation, "processes_exited": False})
        assert state(root) == "quarantined"
        next_run = uuid.uuid4().hex
        next_unit = f"mininet-ai-containment-prototype-{next_run}.service"
        next_marker = root / "new-independent-fixture-started"
        next_argv = ["systemd-run", "--user", "--wait", "--collect", f"--unit={next_unit}",
                     "--service-type=exec", "--property=KillMode=control-group",
                     "--property=RuntimeMaxSec=5s", sys.executable, "-P", "-c",
                     f"from pathlib import Path; Path({str(next_marker)!r}).write_text('new generation')"]
        (root / "new-launch.json").write_text(json.dumps({"run": next_run, "argv": next_argv}))
        fresh = subprocess.run([sys.executable, "-P", HERE, "new-run", str(root)],
                               capture_output=True, text=True, check=True, timeout=10)
        checks["fresh_process_denies_conflicting_service_launch"] = json.loads(fresh.stdout)["denied"]
        checks["denied_launch_has_no_process_effect"] = not next_marker.exists()
        checks["physical_flock_free_but_quarantine_retained"] = gate_available(root) and state(root) == "quarantined"
        checks["delayed_valid_observation_reconciles"] = reconcile(root, observation)
        checks["failed_run_cannot_be_resumed"] = not admit(root, run)
        try:
            new_launch = guarded_start(root, next_run, next_argv)
            checks["new_fixture_generation_launches_only_after_verification"] = (
                not new_launch.get("denied", False) and next_marker.exists())
        finally:
            command("systemctl", "--user", "stop", next_unit, check=False)
            command("systemctl", "--user", "reset-failed", next_unit, check=False)
        checks["old_failed_run_denied_after_successor_launch"] = not admit(root, run)
        with connect(root) as conn:
            controller_events = [{"seq": seq, "kind": kind, "payload": json.loads(payload)}
                                 for seq, kind, payload in conn.execute(
                                     "SELECT * FROM prototype_controller_events ORDER BY seq")]
            events = [{"seq": seq, "id": op, "kind": kind, "payload": json.loads(payload)}
                      for seq, op, kind, payload in conn.execute("SELECT * FROM prototype_events ORDER BY seq")]
            checks["sqlite_integrity"] = conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        cancelled_at = next(e["seq"] for e in controller_events if e["kind"] == "cancel-durable")
        checks["no_dispatch_authorized_after_cancel_commit"] = not any(
            e["kind"] == "worker-dispatch" and e["payload"]["allowed"] and e["seq"] > cancelled_at
            for e in controller_events)
        if scenario != "startup":
            db = af.database(root)
            retained = af.memories(db, "trial:A")
            session = db.get_session(session_id="trial:A")
            db.close()
            checks["seed_memory_preserved"] = any("seed" in str(m) for m in retained)
            if scenario == "outcome":
                checks["committed_outcome_preserved"] = session.session_data["session_state"]["action_result"] == "known-installed"
        else:
            retained = []
        replies = read_rows(root / "replies.jsonl")
        terminal_ids = [r["id"] for r in replies if "agent_object" in r]
        checks["no_duplicate_operation_terminals"] = len(terminal_ids) == len(set(terminal_ids))
        checks["one_worker_incarnation"] = len([r for r in replies if "worker_pid" in r]) == 1
        return {"scenario": scenario, "unit": unit, "checks": checks, "before": before,
                "invocation": invocation, "controller_owner_identity": owner_identity,
                "processes_before_stop": sorted(pids),
                "requests": requests, "replies": replies, "controller_events": controller_events,
                "worker_events": events, "retained_memories": retained,
                "start_command": start, "stop_command": stopped,
                "new_generation_command": new_launch,
                "files": sorted(p.name for p in root.iterdir())}
    finally:
        command("systemctl", "--user", "stop", unit, check=False)
        command("systemctl", "--user", "reset-failed", unit, check=False)
        if cgroup is not None:
            assert fixture.until(lambda: not cgroup.exists() or
                                 "populated 0" in (cgroup / "cgroup.events").read_text())


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "new-run":
            root = Path(sys.argv[2])
            request = json.loads((root / "new-launch.json").read_text())
            result = guarded_start(root, request["run"], request["argv"])
            print(json.dumps(result))
        else:
            bridge(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
        return
    with tempfile.TemporaryDirectory(prefix="integrated-quarantine-prototype-", dir="/tmp/opencode") as tmp:
        root = Path(tmp)
        helpers = root / "helpers"
        helpers.mkdir()
        sources = {}
        for name in HELPERS:
            data = subprocess.run(["git", "show", f"{PIN}:mininet_ai/agents/agno/{name}"],
                                  capture_output=True, check=True).stdout
            sources[name] = {"baseline_sha256": hashlib.sha256(data).hexdigest()}
            if name == "admission_failure_prototype.py":
                data = variant(data.decode()).encode()
            (helpers / name).write_bytes(data)
            sources[name]["executed_sha256"] = hashlib.sha256(data).hexdigest()
        cases = [probe(root / scenario, helpers, scenario) for scenario in (
            "startup", "admission", "foreground", "background", "outcome", "controller-loss",
            "controller-loss-before-cancel", "healthy")]
    with sqlite3.connect(":memory:") as conn:
        sqlite_source_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
    result = {"prototype": True, "cases": cases, "baseline_pin": PIN, "helpers": sources,
              "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                  Path(__file__), Path(fixture.HERE), Path(__file__).with_name("systemd_phase_prototype.py"),
                  Path(__file__).with_name("systemd_containment_prototype.py"))},
              "python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
              "sqlite_source_id": sqlite_source_id,
              "packages": {name: importlib.metadata.version(name) for name in ("agno", "sqlalchemy")},
              "systemd": command("systemd-run", "--version")["stdout"],
              "limits": "Offline trusted fixtures; observer failures injected, no real unkillable process or Provider"}
    print(json.dumps(result, indent=2, default=str))
    assert all(value for case in cases for value in case["checks"].values())


if __name__ == "__main__":
    main()
