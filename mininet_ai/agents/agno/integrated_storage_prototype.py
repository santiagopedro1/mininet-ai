"""THROWAWAY offline protocol probes; not production IPC or acceptance evidence.

Run with -P and the isolated interpreter in docs/prototypes/agno-storage.md.
No Provider clients are constructed. Every run creates fresh disposable stores.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import threading
import time

from agno.agent import Agent

# Reuse the captured offline model and deliberately incomplete error journal.
sys.path.append(str(Path(__file__).resolve().parent))
from storage_prototype import ObservedDb, ScriptedModel, database, learn, memories


def record(path, value):
    """Single writer per journal; append and fsync before acknowledging."""
    with path.open("a") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class OverlapModel(ScriptedModel):
    def __init__(self, barrier):
        super().__init__()
        self.barrier = barrier

    def invoke(self, *args, **kwargs):
        # A global serial command loop would break this bounded barrier.
        if kwargs["messages"][-1].content == "overlap":
            self.barrier.wait(timeout=3)
        return super().invoke(*args, **kwargs)


def worker(root, persistent, channel):
    """Stable single-thread owners per identity, concurrent independent owners.

    This probe supports only session-only operations. Learned Memory extensions
    are deliberately NOT admitted, rather than bypassing an unimplemented gate.
    """
    root = Path(root)
    os.chdir(root)
    barrier = threading.Barrier(2)
    owners, agents, adapters, seen = {}, {}, {}, {}
    send_lock = threading.Lock()
    journal_lock = threading.Lock()
    identities = {"trial:A", "trial:B", "trial:stateless-1", "trial:stateless-2"}

    def send(value):
        with send_lock:
            channel.send(value)

    def execute(command):
        session = command["session"]
        if session not in agents:
            db = database(root, ObservedDb) if persistent else None
            adapters[session] = db
            agents[session] = Agent(
                id=session.split(":")[-1], model=OverlapModel(barrier), db=db,
                cache_session=True, add_history_to_context=True, telemetry=False,
            )
        agent, db = agents[session], adapters[session]
        if db:
            db.inject_session_error = command.get("inject_write_error", False)
        try:
            if command["op"] == "reason":
                response = agent.run(command["text"], session_id=session, user_id=session,
                                     run_id=command["id"])
                status = str(response.status)
            else:
                agent.update_session_state(command["state"], session_id=session)
                status = "outcome updated"
            result = {
                "id": command["id"], "session": session, "payload_hash": digest(command),
                "owner_thread": threading.get_ident(), "agent_object": id(agent),
                "state": agent.get_session_state(session_id=session),
                "messages": agent.model.seen, "agno_status": status,
                "errors": list(db.errors) if db else [],
            }
            result["success"] = not result["errors"]
        except Exception as error:
            result = {"id": command["id"], "session": session,
                      "payload_hash": digest(command), "success": False, "error": repr(error)}
        # Durable terminal record precedes IPC. Failure here emits no success ack.
        with journal_lock:
            if command.get("inject_terminal_error"):
                raise OSError("PROTOTYPE terminal evidence write failure")
            record(root / "PROTOTYPE-worker.jsonl", result)
        if not command.get("drop_reply"):
            send(result)

    send({"ready": True, "pid": os.getpid(), "limitation": "no death watchdog in this probe"})
    try:
        while True:
            command = channel.recv()
            if command.get("op") == "stop":
                break
            operation = command.get("id")
            valid = (command.get("version") == 1 and command.get("run") == "trial"
                     and command.get("session") in identities
                     and command.get("op") in {"reason", "outcome"})
            if not valid:
                send({"rejected": "identity/version/operation", "id": operation})
                continue
            if operation in seen:
                send({"id": operation, "duplicate": True,
                      "same_payload": seen[operation] == digest(command), "replayed": False})
                continue
            seen[operation] = digest(command)
            session = command["session"]
            if session not in owners:
                owners[session] = ThreadPoolExecutor(max_workers=1)
            owners[session].submit(execute, command)
    finally:
        for owner in owners.values():
            owner.shutdown(wait=True)
        for agent in agents.values():
            agent.background_executor.shutdown(wait=True)
        for db in adapters.values():
            if db:
                db.close()
        channel.close()


def session_probe(root, persistent):
    root.mkdir()
    coordinator = None
    if persistent:
        # Coordinator initializes schemas before worker readiness, not concurrently.
        db = database(root)
        learn(db, "seed", automatic=True)
        db.close()
        coordinator = sqlite3.connect(root / "PROTOTYPE-wipe-me.sqlite3", timeout=0.2)
        coordinator.execute("CREATE TABLE prototype_coordinator (value TEXT)")
        coordinator.commit()
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=worker, args=(str(root), persistent, child))
    process.start()
    child.close()
    results = []
    admission_open = True

    def receive():
        if not parent.poll(6):
            raise TimeoutError("prototype reply budget exceeded")
        value = parent.recv()
        results.append(value)
        return value

    def command(identity, session="trial:A", op="reason", **fields):
        return dict(version=1, run="trial", id=identity, session=session, op=op, **fields)

    def dispatch(value):
        if not admission_open or process.exitcode is not None:
            raise RuntimeError("DENIED: failed or exited worker; no cache replacement")
        record(root / "PROTOTYPE-admissions.jsonl", value)
        parent.send(value)

    def request(value):
        dispatch(value)
        return receive()

    try:
        receive()
        first = request(command("first", text="unique A history"))
        # Independent reasoning must overlap; same-owner calls remain serialized.
        dispatch(command("parallel-A", text="overlap"))
        dispatch(command("parallel-B", session="trial:B", text="overlap"))
        overlap = [receive(), receive()]
        if coordinator:
            coordinator.execute("INSERT INTO prototype_coordinator VALUES ('worker-still-live')")
            coordinator.commit()
        # Simulate failed shared-state commitment: still deliver actual outcome.
        outcome = request(command("outcome", op="outcome", state={
            "action_result": "installed", "shared_commit": "failed",
        }))
        back = request(command("back", text="continue A"))
        duplicate = request(command("outcome", op="outcome", state={
            "action_result": "installed", "shared_commit": "failed",
        }))
        conflict = request(command("outcome", op="outcome", state={"action_result": "WRONG"}))
        invalid = request(command("bad", session="other-run:A", text="reject"))
        dispatch(command("stateless-1", session="trial:stateless-1", text="overlap"))
        dispatch(command("stateless-2", session="trial:stateless-2", text="overlap"))
        isolated = [receive(), receive()]
        # Persist success but lose IPC: inspect existing terminal evidence, never rerun.
        dispatch(command("lost-reply", text="do not replay", drop_reply=True))
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            records = read_records(root / "PROTOTYPE-worker.jsonl")
            if any(r["id"] == "lost-reply" for r in records):
                break
            time.sleep(0.01)
        else:
            raise TimeoutError("prototype terminal journal budget exceeded")
        no_reply = not parent.poll(0.15)
        failure = request(command("write-error", text="persistence failure", inject_write_error=True)) if persistent else None
        # An admitted operation can finish locally but lose all terminal evidence.
        # No ack is sent; a missing record is not synthesized as success.
        if persistent:
            admission_open = False
            try:
                dispatch(command("after-failure", text="must not run"))
            except RuntimeError as error:
                denied_after_failure = str(error)
        else:
            dispatch(command("missing-terminal", text="missing evidence", inject_terminal_error=True))
            no_terminal_reply = not parent.poll(0.25)
            denied_after_failure = None
        process_stop = {"op": "stop"}
        parent.send(process_stop)
        process.join(4)
        if process.is_alive():
            raise TimeoutError("prototype drain exceeded budget")
        terminals = read_records(root / "PROTOTYPE-worker.jsonl")
        admission_open = False
        try:
            dispatch(command("after-exit", text="must not recreate"))
        except RuntimeError as error:
            denied_after_exit = str(error)
        # This candidate denies further admission without an existing matched terminal.
        admitted = read_records(root / "PROTOTYPE-admissions.jsonl")
        missing = [r["id"] for r in admitted if r["id"] == "bad" or not any(
            t["id"] == r["id"] and t["payload_hash"] == digest(r) for t in terminals)]
        return {
            "checks": {
                "independent_overlap": all(r["success"] for r in overlap),
                "stable_A_owner": len({r["owner_thread"] for r in [first, outcome, back, overlap[0] if overlap[0]["session"] == "trial:A" else overlap[1]]}) == 1,
                "stable_A_object": first["agent_object"] == back["agent_object"],
                "history_retained": "unique A history" in json.dumps(back["messages"]),
                "outcome_after_modeled_commit_failure": back["state"].get("action_result") == "installed",
                "other_session_isolated": "unique A history" not in json.dumps(next(r for r in overlap if r["session"] == "trial:B")["messages"]),
                "distinct_stateless_objects": len({r["agent_object"] for r in isolated}) == 2,
                "duplicate_not_replayed": duplicate.get("replayed") is False and duplicate.get("same_payload") is True,
                "conflicting_duplicate_rejected": conflict.get("same_payload") is False,
                "foreign_session_rejected": "rejected" in invalid,
                "lost_reply_has_one_terminal": no_reply and sum(r["id"] == "lost-reply" for r in terminals) == 1,
                "required_write_error_failed": failure["success"] is False if persistent else "not exercised in DB-free mode",
                "failed_run_denies_admission": bool(denied_after_failure) if persistent else "see missing terminal",
                "missing_terminal_denies_success": (no_terminal_reply and not any(t["id"] == "missing-terminal" for t in terminals)) if not persistent else "covered by zero-DB probe",
                "coordinator_same_file_write": coordinator.execute("SELECT value FROM prototype_coordinator").fetchall() == [("worker-still-live",)] if persistent else "not applicable",
                "verified_exit": process.exitcode == 0,
            },
            "missing_or_rejected_admissions": missing,
            "post_exit_admission": denied_after_exit,
            "post_failure_admission": denied_after_failure,
            "observations": results, "terminal_records": terminals,
            "files": sorted(p.name for p in root.iterdir()),
            "exitcode": process.exitcode,
            "limits": "No production coordinator, durable supervisor recovery, background learning, complete wire validation, or remote work. Missing records deny success; duplicate payload mismatch is reported, not executed.",
        }
    finally:
        if process.is_alive():
            process.kill()
            process.join(2)
        if process.is_alive():
            raise RuntimeError("UNVERIFIED local exit; no further probe authorized")
        if coordinator:
            coordinator.close()
        parent.close()


def read_records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def active_writer(root, channel):
    root = Path(root)
    db = database(root)
    learn(db, "committed-before-crash", automatic=True)
    db.close()
    connection = sqlite3.connect(root / "PROTOTYPE-wipe-me.sqlite3")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE agno_memories SET user_id='UNCOMMITTED'")
    channel.send({"active_transaction": connection.in_transaction})
    time.sleep(20)  # Controller kills only this known scratch child.


def crash_probe(root):
    root.mkdir()
    db = database(root)
    learn(db, "seed", automatic=True)
    db.close()
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=active_writer, args=(str(root), child))
    process.start()
    child.close()
    try:
        if not parent.poll(6):
            raise TimeoutError("active writer startup exceeded budget")
        active = parent.recv()
        live = sorted(p.name for p in root.iterdir())
        process.kill()
        process.join(2)
        if process.is_alive():
            raise RuntimeError("active writer exit unverified")
        db = database(root)
        retained = memories(db)
        foreign = memories(db, "UNCOMMITTED")
        with db.db_engine.connect() as connection:
            integrity = [tuple(row) for row in connection.exec_driver_sql("PRAGMA integrity_check")]
            pragmas = {name: [tuple(row) for row in connection.exec_driver_sql(f"PRAGMA {name}")]
                       for name in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys")}
        db.close()
        return {"active": active, "exitcode": process.exitcode, "retained": retained,
                "uncommitted_scope": foreign, "integrity": integrity, "pragmas": pragmas,
                "live_files": live, "closed_files": sorted(p.name for p in root.iterdir()),
                "checks": {"active_write_interrupted": active["active_transaction"],
                           "partial_learning_survives": "committed-before-crash" in json.dumps(retained),
                           "uncommitted_write_absent": not foreign,
                           "integrity_ok": integrity == [("ok",)]},
                "limits": "One raw SQLite UPDATE after an actual committed Agno learning call; not a crash inside every Agno multi-write route or proof of whole-operation rollback."}
    finally:
        if process.is_alive():
            process.kill()
            process.join(2)
        parent.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-", dir=args.scratch_parent)).resolve()
    report = {"verdict": "NOT VALIDATED; human review required", "scratch": str(root),
              "provenance": {"python": sys.version, "platform": platform.platform(),
                             "sqlite": sqlite3.sqlite_version,
                             "sqlite_source_id": sqlite3.connect(":memory:").execute("SELECT sqlite_source_id()").fetchone()[0],
                             "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
                             "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                             "helper_sha256": hashlib.sha256(Path(__file__).with_name("storage_prototype.py").read_bytes()).hexdigest()},
              "groups": {}}
    for name, probe in (("zero_db_sessions", lambda p: session_probe(p, False)),
                        ("one_db_sessions", lambda p: session_probe(p, True)),
                        ("active_write_crash", crash_probe)):
        try:
            report["groups"][name] = probe(root / name)
        except Exception as error:
            report["groups"][name] = {"experiment_error": repr(error)}
            # Do not continue after an uncertain containment/error outcome.
            break
    args.report.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(args.report)
    return int(any("experiment_error" in g for g in report["groups"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
