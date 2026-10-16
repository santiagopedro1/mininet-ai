"""THROWAWAY offline crash/reconciliation experiment for GH-60. DO NOT MERGE.

Real spawned processes, Agno, SQLite and flock; scripted inference and modeled
controller policy. Only fresh PROTOTYPE-wipe-me scratch roots are opened.
Reconciliation is read-only: it never dispatches, replaces a worker or replays.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
import platform
import select
import signal
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from agno.agent import Agent
from agno.db.schemas.memory import UserMemory

sys.path.append(str(Path(__file__).resolve().parent))
from admission_failure_prototype import WorkerGate, latch_database
from integrated_storage_prototype import OverlapModel
from learning_gate_prototype import TrackedExecutor, prepare
from storage_prototype import ScriptedModel

DB = "PROTOTYPE-wipe-me.sqlite3"
COMMAND = {
    "root_identity": "scratch-root", "run": "run-1", "session": "session-A",
    "agent": "A", "operation": "operation-1", "kind": "reason",
    "scope": "run", "profile": "fixed-synchronous", "payload": {"text": "offline"},
}
STAGES = [
    "before_admission", "during_admission", "after_admission", "before_dispatch", "after_dispatch",
    "before_memory", "before_memory_ack", "after_memory_ack", "after_memory",
    "before_session", "before_session_ack", "after_session_ack", "after_session",
    "before_run", "before_run_ack", "after_run_ack", "after_run", "before_ack", "after_ack",
    "before_drain", "before_background_write", "after_background_write", "after_drain",
    "before_terminal", "during_terminal", "after_terminal",
    "before_reply", "before_controller_ack", "during_controller_ack", "after_controller_ack", "after_reply",
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def operation_slot(command):
    # A run's operation ID is not reusable with a different session/agent/payload.
    return canonical([command["root_identity"], command["run"], command["operation"]])


def receive(channel):
    if not channel.poll(12):
        raise TimeoutError("prototype IPC budget exceeded")
    return channel.recv()


@contextmanager
def connection(root, readonly=False):
    path = Path(root) / DB
    conn = sqlite3.connect(f"file:{path}?mode=ro" if readonly else path,
                           uri=readonly, timeout=0.2)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        with conn:
            yield conn
    finally:
        conn.close()


def record(root, kind, value, fail: bool | str = False, before_commit=None, command=COMMAND):
    # A real SQLite write against a read-only handle, not a fabricated success.
    with connection(root, readonly=fail is True) as conn:
        if fail == "commit":
            conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("INSERT INTO crash_evidence(slot, kind, value) VALUES (?, ?, ?)",
                     (operation_slot(command), kind, canonical(value)))
        if fail == "commit":
            # The insert succeeds; SQLite itself rejects COMMIT, rolling back
            # this evidence transaction. Not an emulation of disk/power failure.
            conn.execute("INSERT INTO crash_commit_guard(parent_id) VALUES (404)")
        if before_commit:
            before_commit()


def rows(root):
    with connection(root, readonly=True) as conn:
        return [{"seq": seq, "slot": slot, "kind": kind, "value": json.loads(value)}
                for seq, slot, kind, value in conn.execute(
                    "SELECT seq,slot,kind,value FROM crash_evidence ORDER BY seq")]


def reconcile(root, expected=COMMAND):
    """No writes, retries, dispatch or inferred success from materialized effects."""
    try:
        entries = [r for r in rows(root) if r["slot"] == operation_slot(expected)]
        admissions = [r["value"] for r in entries if r["kind"] == "admission"]
        if len(admissions) != 1 or admissions[0] != expected:
            return {"verdict": "unknown", "reason": "absent/ambiguous/mismatched admission"}
        terminals = [r["value"] for r in entries if r["kind"] == "terminal"]
        if len(terminals) != 1:
            return {"verdict": "unknown", "reason": "absent/ambiguous terminal"}
        terminal = terminals[0]
        result = terminal["result"]
        if (terminal["command"] != expected or terminal["command_hash"] != sha(expected)
                or terminal["result_hash"] != sha(result)
                or result["status"] not in {"success", "failed"}):
            return {"verdict": "unknown", "reason": "invalid terminal correlation/integrity"}
        previous = 0
        for kind in ("admission", "dispatch", "ack", "drained", "terminal"):
            matching = [r for r in entries if r["kind"] == kind]
            if len(matching) != 1 or matching[0]["seq"] <= previous:
                return {"verdict": "unknown", "reason": "incomplete/ambiguous protocol ordering"}
            entry = matching[0]
            if kind != "admission" and entry["value"].get("command_hash") != sha(expected):
                return {"verdict": "unknown", "reason": "uncorrelated protocol record"}
            previous = entry["seq"]
        return {"verdict": "recorded-" + result["status"], "result": result,
                "actionable": False, "reason": "historical outcome only; no resumed run"}
    except (sqlite3.Error, ValueError, KeyError, TypeError) as error:
        return {"verdict": "unknown", "reason": repr(error)}


def duplicate(root, command):
    admissions = [r["value"] for r in rows(root)
                  if r["kind"] == "admission" and r["slot"] == operation_slot(command)]
    if not admissions:
        return "unadmitted-no-dispatch"
    if admissions != [command]:
        return "conflict-rejected"
    outcome = reconcile(root, command)
    return outcome if outcome["verdict"].startswith("recorded-") else "in-flight-no-replay"


def pause(channel, stage):
    channel.send({"stage": stage, "pid": os.getpid()})
    try:
        if receive(channel) != "continue":
            raise RuntimeError("unexpected prototype control")
    except EOFError:
        # Deliberate unquiesced controller-loss fixture. Never replace this worker.
        # Harness knows its PID and explicitly kills and verifies it within budget.
        time.sleep(20)
        raise


def worker(root, channel, fault):
    gate = WorkerGate(Path(root))
    gate.acquire()
    db = latch_database(Path(root), None)
    executor = TrackedExecutor()
    failures = []
    checkpoints = set()
    ack_checkpoints = set()

    def emit(kind, payload):
        # Adapter events are separately committed evidence. Failure cannot be
        # promoted to durable error evidence when the error writer also fails.
        label = {"upsert_user_memory": "memory", "upsert_session": "session",
                 "upsert_run": "run"}.get(payload.get("method"))
        first_ack = kind == "db-ack" and label is not None and label not in ack_checkpoints
        if first_ack:
            assert label is not None
            ack_checkpoints.add(label)
            pause(channel, "before_" + label + "_ack")
        record(root, kind, {"command_hash": sha(COMMAND), "detail": payload},
               fail=fault == "adapter-evidence" and kind == "db-ack")
        if first_ack:
            assert label is not None
            pause(channel, "after_" + label + "_ack")

    db.bind(COMMAND["session"], COMMAND["operation"], emit,
            "upsert_user_memory:raise" if fault in {"required-write", "error", "error-commit"} else None)
    original = db.checked

    def checked(method, **kwargs):
        label = {"upsert_user_memory": "memory", "upsert_session": "session",
                 "upsert_run": "run"}.get(method)
        first = label is not None and label not in checkpoints
        if first:
            assert label is not None
            checkpoints.add(label)
            pause(channel, "before_" + label)
        value = original(method, **kwargs)
        if first:
            assert label is not None
            pause(channel, "after_" + label)
        return value

    db.checked = checked
    background_release = threading.Event()
    try:
        with db.db_engine.connect() as conn:
            record(root, "worker-pragmas", {
                "command_hash": sha(COMMAND),
                "journal_mode": conn.exec_driver_sql("PRAGMA journal_mode").scalar(),
                "synchronous": conn.exec_driver_sql("PRAGMA synchronous").scalar(),
            })
        pause(channel, "after_dispatch")
        try:
            db.upsert_user_memory(UserMemory(memory_id="effect", user_id=COMMAND["session"],
                                             memory="committed foreground fact"))
        except Exception as error:
            failures.append(repr(error))
            pause(channel, "before_error")
            record(root, "error", {"command_hash": sha(COMMAND), "error": repr(error)},
                   fail="commit" if fault == "error-commit" else fault == "error")
            pause(channel, "after_error")
        # Agno uses real session/run storage and a scripted model; no Provider.
        class ObservedModel(ScriptedModel):
            def invoke(self, *args, **kwargs):
                record(root, "reasoning", {"command_hash": sha(COMMAND),
                                          "pid": os.getpid(), "thread": threading.get_ident()})
                return super().invoke(*args, **kwargs)

        agent = Agent(id="A", model=ObservedModel(), db=db, telemetry=False,
                      cache_session=True, add_history_to_context=True)
        result = agent.run("offline", session_id=COMMAND["session"],
                           user_id=COMMAND["session"], run_id=COMMAND["operation"])
        if str(result.status) != "RunStatus.completed":
            failures.append(str(result.status))
        db.upsert_run(result, session_id=COMMAND["session"], user_id=COMMAND["session"])

        def background():
            if not background_release.wait(10):
                raise TimeoutError("background fixture bound")
            pause(channel, "before_background_write")
            db.upsert_user_memory(UserMemory(memory_id="background-effect",
                                             user_id=COMMAND["session"], memory="late fact"))
            pause(channel, "after_background_write")

        executor.submit(background)
        pause(channel, "before_ack")
        record(root, "ack", {"command_hash": sha(COMMAND), "foreground": True},
               fail="commit" if fault == "ack-commit" else fault == "ack")
        pause(channel, "after_ack")
        pause(channel, "before_drain")
        background_release.set()
        try:
            executor.drain()
        except Exception as error:
            failures.append(repr(error))
        failures.extend(str(entry) for entry in db.errors)
        record(root, "drained", {"command_hash": sha(COMMAND)}, fail=fault == "drained")
        pause(channel, "after_drain")
        pause(channel, "before_terminal")
        outcome = {"status": "failed" if failures else "success", "errors": failures,
                   "whole_operation_rollback": False}
        terminal = {"command": COMMAND, "command_hash": sha(COMMAND), "result": outcome,
                    "result_hash": sha(outcome)}
        record(root, "terminal", terminal, fail="commit" if fault == "terminal-commit" else fault == "terminal",
               before_commit=lambda: pause(channel, "during_terminal"))
        pause(channel, "after_terminal")
        pause(channel, "before_reply")
        channel.send({"reply": terminal})
        pause(channel, "after_reply")
    except Exception as error:
        channel.send({"volatile_failure": repr(error), "durable_error_not_assumed": True})
    finally:
        background_release.set()
        executor.shutdown(wait=True)
        db.close()
        gate.release()
        channel.close()


def controller(root, channel, fault):
    child = None
    try:
        pause(channel, "before_admission")
        record(root, "admission", COMMAND, fail="commit" if fault == "admission-commit" else fault == "admission",
               before_commit=lambda: pause(channel, "during_admission"))
        pause(channel, "after_admission")
        pause(channel, "before_dispatch")
        record(root, "dispatch", {"command_hash": sha(COMMAND)}, fail=fault == "dispatch")
        parent, endpoint = mp.get_context("spawn").Pipe()
        child = mp.get_context("spawn").Process(target=worker, args=(root, endpoint, fault))
        child.start()
        endpoint.close()
        channel.send({"worker_pid": child.pid})
        while True:
            try:
                message = receive(parent)
            except EOFError:
                break
            if "reply" in message:
                pause(channel, "before_controller_ack")
                record(root, "controller_ack", {"command_hash": sha(COMMAND)},
                       fail="commit" if fault == "supervisor-ack-commit" else fault == "supervisor-ack",
                       before_commit=lambda: pause(channel, "during_controller_ack"))
                pause(channel, "after_controller_ack")
            channel.send(message)
            if "stage" in message:
                try:
                    control = receive(channel)
                    while isinstance(control, dict) and "duplicates" in control:
                        channel.send({"duplicate_delivery": [duplicate(root, cmd)
                                                              for cmd in control["duplicates"]],
                                      "at_stage": message["stage"]})
                        control = receive(channel)
                    parent.send(control)
                except EOFError:
                    break
        child.join(3)
        channel.send({"worker_exit": child.exitcode})
    except Exception as error:
        channel.send({"controller_volatile_failure": repr(error)})
    finally:
        if child is not None and child.is_alive():
            child.kill()
            child.join(3)
        channel.close()


def gate_available(root):
    import fcntl
    with (Path(root) / "PROTOTYPE-wipe-me.learning.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False


def inspect_store(root):
    with connection(root, readonly=True) as conn:
        return {"memory_ids": [r[0] for r in conn.execute(
                    "SELECT memory_id FROM agno_memories ORDER BY memory_id")],
                "session_rows": conn.execute("SELECT count(*) FROM agno_sessions").fetchone()[0],
                "run_rows": conn.execute("SELECT count(*) FROM agno_runs").fetchone()[0],
                "integrity": conn.execute("PRAGMA integrity_check").fetchall(),
                "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
                "inspection_synchronous": conn.execute("PRAGMA synchronous").fetchone()[0]}


def experiment(base, name, death=None, stage=None, fault=None, mutate=None):
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-" + name + "-", dir=base))
    prepare(root)
    with connection(root) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE crash_evidence(seq INTEGER PRIMARY KEY, slot TEXT NOT NULL, kind TEXT, value TEXT)")
        conn.execute("CREATE TABLE crash_guard_parent(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE crash_commit_guard(parent_id INTEGER REFERENCES crash_guard_parent(id) DEFERRABLE INITIALLY DEFERRED)")
    channel, endpoint = mp.get_context("spawn").Pipe()
    process = mp.get_context("spawn").Process(target=controller,
                                               args=(str(root), endpoint, fault))
    process.start()
    endpoint.close()
    events, worker_pid, worker_fd = [], None, None
    killed, held_after_controller_death, duplicates = False, None, None
    completed_duplicates = None
    try:
        while True:
            if not channel.poll(15):
                raise TimeoutError("experiment budget")
            try:
                message = channel.recv()
            except EOFError:
                break
            events.append(message)
            if "stage" in message:
                message["live_files"] = sorted(p.name for p in root.iterdir())
            if "worker_pid" in message:
                worker_pid = message["worker_pid"]
                worker_fd = os.pidfd_open(worker_pid)
            if message.get("stage") == "before_drain":
                duplicates = {"gate_held": not gate_available(root)}
            if "duplicate_delivery" in message:
                identical, conflicting = message["duplicate_delivery"]
                if message["at_stage"] == "before_drain":
                    assert duplicates is not None
                    duplicates.update(identical=identical, conflicting=conflicting)
                else:
                    completed_duplicates = message["duplicate_delivery"]
                channel.send("continue")
                continue
            if stage is not None and message.get("stage") == stage and death:
                killed = True
                if death == "controller":
                    process.kill()
                    process.join(3)
                    if worker_pid:
                        assert worker_fd is not None
                        held_after_controller_death = not gate_available(root)
                        os.kill(worker_pid, signal.SIGKILL)
                        if not select.select([worker_fd], [], [], 3)[0]:
                            raise TimeoutError("orphan worker exit not verified")
                    break
                assert worker_pid is not None
                os.kill(worker_pid, signal.SIGKILL)
                # Controller is waiting for a release; allow it to observe EOF.
                channel.send("continue")
                death = "worker-killed"
                continue
            if "stage" in message:
                if message["stage"] in {"before_drain", "after_terminal"}:
                    channel.send({"duplicates": [COMMAND, {**COMMAND, "payload": {"text": "different"}}]})
                    continue
                channel.send("continue")
        process.join(3)
        if process.is_alive():
            raise TimeoutError("controller did not exit")
        before_mutation = rows(root)
        if mutate:
            with connection(root) as conn:
                if mutate == "missing":
                    conn.execute("DELETE FROM crash_evidence WHERE kind='terminal'")
                elif mutate == "missing-ack":
                    conn.execute("DELETE FROM crash_evidence WHERE kind='ack'")
                elif mutate == "corrupt":
                    conn.execute("UPDATE crash_evidence SET value='broken-json' WHERE kind='terminal'")
                elif mutate == "ambiguous":
                    conn.execute("INSERT INTO crash_evidence(slot,kind,value) SELECT slot,kind,value FROM crash_evidence WHERE kind='terminal'")
                elif mutate == "mismatch":
                    value = next(r["value"] for r in before_mutation if r["kind"] == "terminal")
                    value["command"]["payload"] = {"text": "substituted"}
                    conn.execute("UPDATE crash_evidence SET value=? WHERE kind='terminal'", (canonical(value),))
                elif mutate == "result-corrupt":
                    value = next(r["value"] for r in before_mutation if r["kind"] == "terminal")
                    value["result"]["status"] = "failed"
                    conn.execute("UPDATE crash_evidence SET value=? WHERE kind='terminal'", (canonical(value),))
                elif mutate == "uncorrelated-ack":
                    conn.execute("UPDATE crash_evidence SET value=? WHERE kind='ack'",
                                 (canonical({"command_hash": "foreign"}),))
                elif mutate == "out-of-order":
                    conn.execute("UPDATE crash_evidence SET seq=10000 WHERE kind='drained'")
        verdict = reconcile(root)
        snapshot = inspect_store(root)
        files = sorted(p.name for p in root.iterdir())
        expected = "unknown"
        if not mutate and not fault and (not death or stage in {
                "after_terminal", "before_reply", "before_controller_ack", "during_controller_ack",
                "after_controller_ack", "after_reply"}):
            expected = "recorded-success"
        if fault == "required-write" and not death:
            expected = "recorded-failed"
        if fault in {"supervisor-ack", "supervisor-ack-commit"}:
            expected = "recorded-success"
        checks = {"verdict": verdict["verdict"] == expected,
                  "controller_exit_observed": process.exitcode is not None,
                  "worker_exit_verified": worker_fd is None
                    or bool(select.select([worker_fd], [], [], 0)[0]),
                  "gate_available_after_verified_exit": gate_available(root),
                  "integrity": snapshot["integrity"] == [("ok",)],
                  "single_database": [f for f in files if f.endswith(".sqlite3")] == [DB],
                  "requested_death_reached": not death or killed,
                  "no_reasoning_replay": sum(r["kind"] == "reasoning" for r in before_mutation) <= 1,
                  "commit_failure_observed": not (fault and fault.endswith("-commit"))
                    or any("FOREIGN KEY constraint failed" in str(e) for e in events),
                  "all_identity_fields_checked": all(
                      reconcile(root, {**COMMAND, field: "foreign"})["verdict"] == "unknown"
                      for field in COMMAND),
                  "no_success_reply_without_terminal": not any("reply" in e for e in events)
                    or any(r["kind"] == "terminal" for r in before_mutation)}
        if duplicates and "identical" in duplicates:
            checks["identical_inflight_not_replayed"] = duplicates["identical"] == "in-flight-no-replay"
            checks["conflict_rejected"] = duplicates["conflicting"] == "conflict-rejected"
            checks["gate_held_through_background"] = duplicates["gate_held"]
        if completed_duplicates:
            checks["completed_duplicate_returns_historical_outcome"] = (
                completed_duplicates[0]["verdict"] in {"recorded-success", "recorded-failed"}
                and completed_duplicates[0]["actionable"] is False)
            checks["completed_conflict_rejected"] = completed_duplicates[1] == "conflict-rejected"
        if held_after_controller_death is not None:
            checks["controller_death_not_quiescence"] = held_after_controller_death
        return {"name": name, "root": str(root), "events": events,
                "controller_exit": process.exitcode, "worker_exit_verified": worker_fd is None
                    or bool(select.select([worker_fd], [], [], 0)[0]),
                "evidence_before_mutation": before_mutation, "mutation": mutate,
                "duplicates": duplicates, "completed_duplicates": completed_duplicates,
                "snapshot": snapshot, "files": files,
                "reconciliation": verdict, "expected": expected, "checks": checks}
    finally:
        if process.is_alive():
            process.kill()
            process.join(3)
        if worker_fd is not None:
            if not select.select([worker_fd], [], [], 0)[0]:
                assert worker_pid is not None
                os.kill(worker_pid, signal.SIGKILL)
                select.select([worker_fd], [], [], 3)
            os.close(worker_fd)
        channel.close()


def overlap_experiment(base):
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-overlap-", dir=base))
    prepare(root)
    with connection(root) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE crash_evidence(seq INTEGER PRIMARY KEY, slot TEXT NOT NULL, kind TEXT, value TEXT)")
    barrier = threading.Barrier(2)

    def invoke(identity):
        command = {**COMMAND, "session": identity, "agent": identity, "operation": identity}

        def emit(kind, detail):
            record(root, kind, {"command_hash": sha(command), "detail": detail}, command=command)

        record(root, "admission", command, command=command)
        emit("dispatch", {})
        db = latch_database(root, None)
        db.bind(identity, identity, emit)
        agent = Agent(id=identity, model=OverlapModel(barrier), db=db, telemetry=False)
        try:
            result = agent.run("overlap", session_id=identity, user_id=identity, run_id=identity)
            emit("ack", {})
            emit("drained", {})  # This fixed profile submits no background work.
            outcome = {"status": "success" if not db.errors else "failed", "errors": db.errors}
            record(root, "terminal", {"command": command, "command_hash": sha(command),
                                      "result": outcome, "result_hash": sha(outcome)}, command=command)
            return {"identity": identity, "owner": threading.get_ident(),
                    "agent": id(agent), "status": str(result.status), "errors": db.errors,
                    "command": command}
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, identity) for identity in ("independent-A", "independent-B")]
        replies = [future.result(timeout=12) for future in futures]
    return {"name": "independent-memory-free-overlap", "root": str(root), "replies": replies,
            "snapshot": inspect_store(root), "evidence": rows(root), "checks": {
                "two_owner_threads": len({r["owner"] for r in replies}) == 2,
                "both_completed_barrier": all(r["status"] == "RunStatus.completed" and not r["errors"]
                                              for r in replies),
                "isolated_sessions": inspect_store(root)["session_rows"] == 2,
                "both_outcomes_correlated": all(reconcile(root, r["command"])["verdict"] == "recorded-success"
                                                for r in replies)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report must not exist")
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    groups = [experiment(args.scratch_parent, "healthy"), overlap_experiment(args.scratch_parent)]
    for who in ("worker", "controller"):
        for stage in STAGES:
            if who == "worker" and stage in STAGES[:4]:
                continue  # No worker exists yet.
            if args.quick and stage not in {"after_memory", "after_terminal"}:
                continue
            groups.append(experiment(args.scratch_parent, who + "-" + stage, who, stage))
    for fault in ("admission", "dispatch", "adapter-evidence", "ack", "drained", "terminal",
                  "required-write", "error", "supervisor-ack", "admission-commit", "ack-commit",
                  "terminal-commit", "error-commit", "supervisor-ack-commit"):
        groups.append(experiment(args.scratch_parent, "fault-" + fault, fault=fault))
    if not args.quick:
        for who in ("worker", "controller"):
            for stage in ("before_error", "after_error"):
                groups.append(experiment(args.scratch_parent, who + "-" + stage, who, stage,
                                         fault="required-write"))
    for mutation in ("missing", "missing-ack", "corrupt", "ambiguous", "mismatch",
                     "result-corrupt", "uncorrelated-ack", "out-of-order"):
        groups.append(experiment(args.scratch_parent, "terminal-" + mutation, mutate=mutation))
    with sqlite3.connect(":memory:") as conn:
        sqlite = {"version": sqlite3.sqlite_version,
                  "source_id": conn.execute("SELECT sqlite_source_id()").fetchone()[0],
                  "compile_options": [r[0] for r in conn.execute("PRAGMA compile_options")]}
    report = {"warning": "THROWAWAY; not architecture acceptance or power-loss durability",
              "python": sys.version, "platform": platform.platform(), "sqlite": sqlite,
              "packages": {p: importlib.metadata.version(p) for p in ("agno", "sqlalchemy")},
              "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), Path(__file__).with_name("learning_gate_prototype.py"),
                                          Path(__file__).with_name("storage_prototype.py"),
                                          Path(__file__).with_name("integrated_storage_prototype.py"),
                                          Path(__file__).with_name("admission_failure_prototype.py"))},
              "groups": groups}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    failed = [(g["name"], k) for g in groups for k, v in g["checks"].items() if not v]
    print(json.dumps({"groups": len(groups), "checks": sum(len(g["checks"]) for g in groups),
                      "failed": failed, "report": str(args.report)}))
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
