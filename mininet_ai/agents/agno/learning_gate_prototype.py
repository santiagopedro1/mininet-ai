"""THROWAWAY offline learning boundary probe. Not production; do not merge.

Uses the isolated interpreter and scratch paths in docs/prototypes/agno-learning.md.
All model responses are scripts. No Provider clients or network calls.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import inspect
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
from agno.run import RunStatus

from agno.agent import Agent
from agno.db.schemas.memory import UserMemory
from agno.db.sqlite import SqliteDb
from agno.memory.manager import MemoryManager
from agno.memory.strategies.base import MemoryOptimizationStrategy

sys.path.append(str(Path(__file__).resolve().parent))
from storage_prototype import (
    AgenticModel, BlockedLearningModel, FailingForegroundModel, ScriptedModel,
    database, gate, memories, race,
)
from integrated_storage_prototype import OverlapModel, digest


class OfflineOptimization(MemoryOptimizationStrategy):
    def optimize(self, memories, model):
        return [UserMemory(memory_id=memories[0].memory_id,
                           user_id=memories[0].user_id, memory="optimized offline")]

    async def aoptimize(self, memories, model):
        raise RuntimeError("async route not eligible in this candidate")

    def count_tokens(self, memories):
        return 0  # Avoid tokenizer downloads; token accounting is not under test.


class TrackedExecutor(ThreadPoolExecutor):
    def __init__(self):
        super().__init__(max_workers=3)
        self.owned = []

    def submit(self, *args, **kwargs):
        future = super().submit(*args, **kwargs)
        self.owned.append(future)
        return future

    def drain(self):
        # Only enumerated routes may submit, all submissions are recorded here.
        while self.owned:
            future = self.owned[0]
            try:
                future.result(timeout=4)
            finally:
                if future.done():
                    self.owned.pop(0)


class StrictDb(SqliteDb):
    """Closed fixture boundary, NOT an exhaustive or tamper-proof adapter.

    One adapter per stable Agent/owner, with operation state shared with its
    tracked background threads. Root flock serializes memory-capable operations.
    """

    def bind(self, user, operation, emit, fault=None):
        self.user, self.operation, self.emit, self.fault = user, operation, emit, fault
        self.errors = []
        self.active = True

    def checked(self, method, **kwargs):
        try:
            if not self.active:
                raise RuntimeError("access outside owned operation")
            if method == "upsert_user_memory":
                memory = kwargs["memory"]
                if memory.user_id != self.user:
                    raise ValueError("wrong memory owner")
                # Stock upsert conflicts on ID alone; authorize against ANY owner.
                existing = super().get_user_memory(memory.memory_id) if memory.memory_id else None
                if existing and existing.user_id != self.user:
                    raise ValueError("foreign memory ID")
            elif method in {"get_user_memories", "get_user_memory", "delete_user_memory",
                            "delete_user_memories"}:
                if kwargs.get("user_id") != self.user:
                    raise ValueError("missing/wrong scoped user")
                if method in {"delete_user_memory", "delete_user_memories"}:
                    ids = kwargs.get("memory_ids", [kwargs.get("memory_id")])
                    if any(super(StrictDb, self).get_user_memory(i, user_id=self.user) is None for i in ids):
                        raise ValueError("required deletion target absent or foreign")
            self.emit("db-attempt", {"method": method})
            if self.fault == method + ":raise":
                raise OSError("PROTOTYPE required write exception")
            if self.fault == method + ":missing":
                result = None  # Emulate a no-row/quiet no-op backend result.
            else:
                result = getattr(super(), method)(**kwargs)
            if method in {"upsert_user_memory", "upsert_session"} and result is None:
                raise RuntimeError("required upsert returned no row")
            if method == "upsert_run":
                # None is NORMAL for this API: read back required row instead.
                run = kwargs["run"]
                run_id = run.get("run_id") if isinstance(run, dict) else run.run_id
                with self.db_engine.connect() as conn:
                    row = conn.exec_driver_sql(
                        "SELECT run_id FROM agno_runs WHERE run_id=? AND session_id=?",
                        (run_id, kwargs["session_id"]),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("required run row absent")
            if method in {"delete_user_memory", "delete_user_memories"}:
                ids = kwargs.get("memory_ids", [kwargs.get("memory_id")])
                if any(super(StrictDb, self).get_user_memory(i, user_id=self.user) for i in ids):
                    raise RuntimeError("required deletion left rows")
            self.emit("db-ack", {"method": method})
            return result
        except Exception as error:
            entry = {"method": method, "error": repr(error)}
            self.errors.append(entry)
            self.emit("db-error", entry)
            raise

    def upsert_user_memory(self, memory, deserialize=True):
        return self.checked("upsert_user_memory", memory=memory, deserialize=deserialize)

    def get_user_memories(self, user_id=None, **kwargs):
        return self.checked("get_user_memories", user_id=user_id, **kwargs)

    def get_user_memory(self, memory_id, deserialize=True, user_id=None):
        return self.checked("get_user_memory", memory_id=memory_id, user_id=user_id,
                            deserialize=deserialize)

    def delete_user_memory(self, memory_id, user_id=None):
        return self.checked("delete_user_memory", memory_id=memory_id, user_id=user_id)

    def delete_user_memories(self, memory_ids, user_id=None):
        return self.checked("delete_user_memories", memory_ids=memory_ids, user_id=user_id)

    def clear_memories(self):
        raise ValueError("unscoped global clear rejected")

    def upsert_session(self, session, deserialize=True):
        return self.checked("upsert_session", session=session, deserialize=deserialize)

    def upsert_run(self, run, session_id, user_id=None, run_index=None):
        return self.checked("upsert_run", run=run, session_id=session_id,
                            user_id=user_id, run_index=run_index)


def event(path, operation, kind, payload):
    # Separate worker-owned connection, same physical DB; no transaction over LLM.
    with sqlite3.connect(path, timeout=0.2) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("INSERT INTO prototype_events(op,kind,payload) VALUES(?,?,?)",
                     (operation, kind, json.dumps(payload, default=str, sort_keys=True)))


ROUTES = {"automatic", "agentic", "create", "task", "add", "replace", "read",
          "search", "delete", "clear-user", "optimize", "hooks", "tool", "overlap",
          "background-error", "wrong-owner", "foreign-id", "global-clear"}


def worker(root, channel):
    root = Path(root)
    path = root / "PROTOTYPE-wipe-me.sqlite3"
    owners, cached, seen = {}, {}, {}
    failed = threading.Event()
    send_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def send(value):
        with send_lock:
            channel.send(value)

    def execute(command):
        op, identity, route = command["id"], command["agent"], command["route"]
        user = f"trial:{identity}" if command.get("scope", "run") == "run" else identity
        key = (identity, user, route)
        # Session-only overlap bypasses learning gate; everything else conservatively gated.
        from contextlib import nullcontext
        try:
            with gate(root, timeout=3) if route != "overlap" else nullcontext():
                def emit(kind, payload):
                    if command.get("evidence_fault") == kind:
                        raise OSError("PROTOTYPE evidence write failure: " + kind)
                    event(path, op, kind, payload)

                emit("admitted", {"command_hash": digest(command), "user": user})
                if key not in cached:
                    db = database(root, StrictDb)
                    manager = MemoryManager(db=db, model=ScriptedModel("fact-" + op))
                    options = {}
                    if route == "hooks":
                        def hook():
                            manager.add_user_memory(UserMemory(memory="hook fact"), user_id=user)
                        options = {"pre_hooks": [hook], "post_hooks": [hook]}
                    if route == "tool":
                        def update_user_memory(task: str) -> str:
                            return manager.update_memory_task(task, user_id=user)
                        options = {"tools": [update_user_memory]}
                    model = (OverlapModel(barrier) if route == "overlap" else
                             AgenticModel() if route in {"agentic", "tool"} else ScriptedModel())
                    agent = Agent(id=identity, model=model, db=db, memory_manager=manager,
                                  update_memory_on_run=route in {"automatic", "background-error"},
                                  enable_agentic_memory=route == "agentic", telemetry=False,
                                  cache_session=True, **options)
                    agent._background_executor = TrackedExecutor()
                    cached[key] = agent, manager, db
                agent, manager, db = cached[key]
                db.bind(user, op, emit, command.get("fault"))
                errors, status, response = [], None, None
                try:
                    memory = UserMemory(memory_id=command.get("memory_id"), memory="direct " + op,
                                        user_id=user)
                    if route in {"automatic", "agentic", "hooks", "tool", "overlap", "background-error"}:
                        if route == "background-error":
                            entered, release = threading.Event(), threading.Event()
                            manager.model = BlockedLearningModel(entered, release)
                            agent.model = FailingForegroundModel(entered)
                            # Controller checks another thread AND process cannot enter gate.
                            def release_later():
                                if entered.wait(3):
                                    send({"background_entered": op})
                                time.sleep(0.5)
                                release.set()
                            releaser = threading.Thread(target=release_later)
                            releaser.start()
                        response = agent.run("overlap" if route == "overlap" else "offline",
                                             session_id=f"trial:{identity}", user_id=user, run_id=op)
                        status = str(response.status)
                        if response.status != RunStatus.completed:
                            errors.append("Agno returned non-completed status: " + status)
                    elif route == "create":
                        response = manager.create_user_memories(message="offline", user_id=user)
                    elif route == "task":
                        response = manager.update_memory_task("offline", user_id=user)
                    elif route == "add":
                        response = manager.add_user_memory(memory, user_id=user)
                    elif route == "replace":
                        response = manager.replace_user_memory(memory.memory_id, memory, user_id=user)
                    elif route == "read":
                        response = manager.get_user_memory(memory.memory_id, user_id=user)
                    elif route == "search":
                        response = manager.search_user_memories(user_id=user, retrieval_method="last_n")
                    elif route == "delete":
                        manager.delete_user_memory(memory.memory_id, user_id=user)
                    elif route == "clear-user":
                        manager.clear_user_memories(user_id=user)
                    elif route == "optimize":
                        response = manager.optimize_memories(user_id=user, strategy=OfflineOptimization())
                    elif route == "wrong-owner":
                        memory.user_id = "foreign"
                        db.upsert_user_memory(memory)
                    elif route == "foreign-id":
                        manager.replace_user_memory("foreign-seed", memory, user_id=user)
                    elif route == "global-clear":
                        manager.clear()
                except Exception as error:
                    errors.append(repr(error))
                finally:
                    try:
                        agent.background_executor.drain()
                    except Exception as error:
                        errors.append(repr(error))
                        if agent.background_executor.owned:
                            failed.set()
                            send({"id": op, "success": False, "unquiesced": True})
                            # Do NOT release the gate on a timeout. The bounded
                            # controller must kill and verify this exact worker.
                            threading.Event().wait()
                    if route == "background-error":
                        releaser.join(2)
                    emit("drained", {"pending": len(agent.background_executor.owned)})
                    db.active = False
                errors.extend(db.errors)
                if errors:
                    failed.set()
                result = {"id": op, "hash": digest(command), "success": not errors,
                          "errors": errors, "agno_status": status, "response": str(response),
                          "owner": threading.get_ident(), "agent_object": id(agent), "user": user}
                emit("terminal", result)
                if not command.get("drop_reply"):
                    send(result)
        except Exception as error:
            failed.set()
            # A failed evidence channel cannot be used to assert durable completion.
            send({"id": op, "success": False, "transport_only_error": repr(error)})

    send({"ready": True, "pid": os.getpid()})
    try:
        while True:
            command = channel.recv()
            if command.get("route") == "stop":
                break
            if command.get("route") not in ROUTES or command.get("agent") not in {"A", "B"}:
                send({"id": command.get("id"), "rejected": "route/identity"})
                continue
            if command["id"] in seen:
                send({"id": command["id"], "duplicate": True, "replayed": False,
                      "same_payload": seen[command["id"]] == digest(command)})
                continue
            if failed.is_set():
                send({"id": command["id"], "rejected": "failed worker; no new admission"})
                continue
            seen[command["id"]] = digest(command)
            if command["agent"] not in owners:
                owners[command["agent"]] = ThreadPoolExecutor(max_workers=1)
            owner = owners[command["agent"]]
            owner.submit(execute, command)
    finally:
        for owner in owners.values():
            owner.shutdown(wait=True)
        for agent, manager, db in cached.values():
            agent.background_executor.shutdown(wait=True)
            db.close()
        channel.close()


def prepare(root):
    db = database(root)
    # Bounded concurrent startup is probed separately. Do not let session-only
    # overlap implicitly race lazy DDL after readiness.
    for table_type in ("memories", "sessions", "runs"):
        db._get_table(table_type=table_type, create_table_if_not_found=True)
    for user, memory_id in (("trial:A", "seed"), ("foreign", "foreign-seed"), ("A", "agent-seed")):
        db.upsert_user_memory(UserMemory(memory_id=memory_id, user_id=user, memory="seed"))
    db.close()
    with sqlite3.connect(root / "PROTOTYPE-wipe-me.sqlite3") as conn:
        conn.execute("CREATE TABLE prototype_events(seq INTEGER PRIMARY KEY, op TEXT, kind TEXT, payload TEXT)")


def probe(root, commands):
    prepare(root)
    path = root / "PROTOTYPE-wipe-me.sqlite3"
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=worker, args=(str(root), child))
    process.start()
    child.close()
    results, sidecars, background_gate = [], [], None

    def receive():
        if not parent.poll(8):
            raise TimeoutError("bounded prototype reply wait")
        return parent.recv()

    try:
        results.append(receive())
        event(path, "coordinator", "independent-connection", {"worker_alive": process.is_alive()})
        for batch in commands:
            for command in batch:
                event(path, command["id"], "supervisor-admission", {"hash": digest(command)})
                parent.send(command)
            for command in batch:
                if command.get("drop_reply"):
                    deadline = time.monotonic() + 6
                    while time.monotonic() < deadline:
                        with sqlite3.connect(path) as conn:
                            terminal = conn.execute("SELECT payload FROM prototype_events WHERE op=? AND kind='terminal'",
                                                    (command["id"],)).fetchone()
                        if terminal:
                            results.append({"lost_reply_terminal": json.loads(terminal[0]),
                                            "no_reply": not parent.poll(0.1)})
                            break
                        time.sleep(0.01)
                    else:
                        raise TimeoutError("no existing terminal; deny success/replay")
                    continue
                value = receive()
                if "background_entered" in value:
                    # This controller is another process; the thread below is distinct too.
                    def contend():
                        try:
                            with gate(root, timeout=0.1):
                                return "ACQUIRED TOO EARLY"
                        except TimeoutError:
                            return "denied while background active"
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        background_gate = {"controller": contend(), "thread": pool.submit(contend).result()}
                    value = receive()
                results.append(value)
            sidecars.append(sorted(p.name for p in root.iterdir()))
        parent.send({"route": "stop"})
        process.join(5)
        if process.is_alive():
            raise TimeoutError("worker did not drain")
        with sqlite3.connect(path) as conn:
            events = [{"seq": s, "id": o, "kind": k, "payload": json.loads(p)}
                      for s, o, k, p in conn.execute("SELECT * FROM prototype_events ORDER BY seq")]
            pragmas = {n: conn.execute("PRAGMA " + n).fetchall()
                       for n in ("journal_mode", "synchronous", "busy_timeout", "integrity_check")}
        db = database(root)
        scopes = {u: memories(db, u) for u in ("trial:A", "trial:B", "A", "foreign")}
        db.close()
        return {"commands": commands, "replies": results, "events": events, "scopes": scopes,
                "background_gate": background_gate, "live_files": sidecars,
                "closed_files": sorted(p.name for p in root.iterdir()),
                "pragmas_inspection_connection": pragmas, "exitcode": process.exitcode}
    finally:
        if process.is_alive():
            process.kill()
            process.join(2)
        if process.is_alive():
            raise RuntimeError("UNVERIFIED worker exit; stop experiments")
        parent.close()


def command(identity, route, **fields):
    return {"id": identity, "route": route, "agent": "A", **fields}


def initialize(root, queue):
    """Fixture-only schema preflight, not a proposed schema/version policy."""
    start = time.monotonic()
    db = None
    try:
        with gate(root, timeout=0.25):
            db = database(root)
            for table_type, required in (("memories", {"memory_id", "user_id", "memory"}),
                                         ("sessions", {"session_id", "session_type"}),
                                         ("runs", {"run_id", "session_id"})):
                table = db._get_table(table_type=table_type, create_table_if_not_found=True)
                if table is None or not required.issubset(set(table.columns.keys())):
                    raise ValueError("incompatible fixture table: " + table_type)
            queue.put({"ready": True, "seconds": time.monotonic() - start})
    except Exception as error:
        queue.put({"ready": False, "error": repr(error), "seconds": time.monotonic() - start})
    finally:
        if db:
            db.close()


def startup_probe(root, mode):
    root.mkdir()
    path = root / "PROTOTYPE-wipe-me.sqlite3"
    blocker = None
    from contextlib import nullcontext
    if mode == "incompatible":
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE agno_memories (unexpected TEXT)")
    elif mode == "invalid-path":
        path.mkdir()
    elif mode == "writer-blocked":
        blocker = sqlite3.connect(path)
        blocker.execute("BEGIN IMMEDIATE")
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    workers = []
    try:
        with gate(root) if mode == "gate-blocked" else nullcontext():
            for _ in range(2 if mode == "concurrent" else 1):
                process = ctx.Process(target=initialize, args=(root, queue))
                process.start()
                workers.append(process)
            results = [queue.get(timeout=5) for _ in workers]
            for process in workers:
                process.join(2)
            if any(p.is_alive() for p in workers):
                raise TimeoutError("startup completion budget")
        return {"results": results, "exitcodes": [p.exitcode for p in workers],
                "files": sorted(p.name for p in root.iterdir()),
                "limits": "Scratch preflight under root gate; no lifecycle/migration policy, power-loss or deployment proof."}
    finally:
        for process in workers:
            if process.is_alive():
                process.kill()
                process.join(2)
            if process.is_alive():
                raise RuntimeError("UNVERIFIED startup child exit")
        if blocker:
            blocker.rollback()
            blocker.close()
        queue.close()


def eligibility_probe():
    # Candidate identity registry only, not actual production module loading.
    registry = {}
    singleton = Agent(model=ScriptedModel(), telemetry=False)

    def admit(agent, identity):
        previous = registry.get(id(agent))
        if previous is not None and previous != identity:
            return "reject shared mutable singleton; do not clone/serialize"
        registry[id(agent)] = identity
        return "eligible identity binding"

    return {"one_identity": admit(singleton, "A"), "same_identity": admit(singleton, "A"),
            "foreign_identity": admit(singleton, "B"),
            "factory_fresh": admit(Agent(model=ScriptedModel(), telemetry=False), "B"),
            "limits": "Modeled registry, not worker-local Python import/resource compatibility validation."}


def stock_foreign_probe(root):
    db = database(root)
    try:
        db.upsert_user_memory(UserMemory(memory_id="known-id", user_id="foreign", memory="original"))
        manager = MemoryManager(db=db, model=ScriptedModel())
        returned = manager.replace_user_memory("known-id", UserMemory(memory="changed by wrong owner"),
                                               user_id="trial:A")
        return {"manager_returned": returned, "foreign": memories(db, "foreign"),
                "caller": memories(db, "trial:A")}
    finally:
        db.close()


def check_report(groups):
    routes = groups["routes"]
    replies = {r["id"]: r for r in routes["replies"] if "id" in r}
    checks = {
        "all_groups_executed": not any("experiment_error" in g for g in groups.values()),
        "healthy_routes_success": all(r.get("success") is True for r in replies.values()),
        "independent_session_overlap": all(replies[i]["success"] for i in ("overlap-A", "overlap-B")),
        "cached_identity_stable": replies["agent-reuse"]["agent_object"] == replies["read-again"]["agent_object"],
        "cached_owner_stable": replies["agent-reuse"]["owner"] == replies["read-again"]["owner"],
        "other_scope_read_empty": replies["other-scope"]["response"] == "None",
        "agent_scope_read_present": "seed" in replies["agent-reuse"]["response"],
        "foreign_scope_unchanged": routes["scopes"]["foreign"][0]["memory"] == "seed",
        "gated_race_retains_both": groups["cross_process_gated"]["both_facts_retained"],
        "unguarded_control_loses_update": not groups["cross_process_unguarded_control"]["both_facts_retained"],
        "stock_foreign_id_mutation_observed": groups["stock-foreign-id"]["foreign"][0]["memory"] == "changed by wrong owner",
    }
    # Memory-capable contention must have no overlapping admitted-to-terminal intervals.
    intervals = {}
    for e in routes["events"]:
        if e["kind"] in {"admitted", "terminal"}:
            intervals.setdefault(e["id"], {})[e["kind"]] = e["seq"]
    a, b = intervals["contention-A"], intervals["contention-B"]
    checks["same_worker_root_gate_serializes"] = a["terminal"] < b["admitted"] or b["terminal"] < a["admitted"]
    background = groups["background"]
    checks["foreground_error_failed_after_drain"] = background["replies"][-1]["success"] is False
    checks["background_keeps_gate"] = all(v.startswith("denied") for v in background["background_gate"].values())
    checks["background_partial_learning_retained"] = "late-background-fact" in json.dumps(background["scopes"])
    for name, group in groups.items():
        if name.startswith("startup-"):
            expected = name in {"startup-fresh", "startup-concurrent"}
            checks[name] = all(r["ready"] == expected for r in group["results"]) and all(e == 0 for e in group["exitcodes"])
        elif ":" in name or name.startswith("evidence-"):
            checks[name] = group["replies"][1].get("success") is False and "rejected" in group["replies"][2]
        elif name in {"wrong-owner", "foreign-id", "global-clear"}:
            checks[name] = group["replies"][1]["success"] is False
        if "events" in group:
            # Every successful IPC response must already have a matching persisted terminal.
            checks[name + "-durable-success"] = all(any(
                e["kind"] == "terminal" and e["id"] == r["id"] and e["payload"]["hash"] == r["hash"]
                for e in group["events"]
            ) for r in group["replies"] if r.get("success") is True)
            checks[name + "-verified-exit"] = group["exitcode"] == 0
            checks[name + "-terminal-after-drain"] = all(any(
                d["kind"] == "drained" and d["id"] == e["id"] and d["seq"] < e["seq"]
                for d in group["events"]
            ) for e in group["events"] if e["kind"] == "terminal")
            checks[name + "-correlated-admission"] = all(any(
                a["kind"] == "supervisor-admission" and a["id"] == e["id"]
                and a["payload"]["hash"] == e["payload"]["hash"] and a["seq"] < e["seq"]
                for a in group["events"]
            ) for e in group["events"] if e["kind"] == "terminal")
    duplicate = groups["duplicates"]["replies"]
    checks["duplicate_not_replayed"] = duplicate[2]["same_payload"] and not duplicate[2]["replayed"]
    checks["conflicting_duplicate_not_replayed"] = not duplicate[3]["same_payload"] and not duplicate[3]["replayed"]
    checks["extension_rejected"] = "rejected" in duplicate[4]
    lost = groups["lost-reply"]
    checks["lost_reply_no_replay"] = lost["replies"][1]["no_reply"] and not lost["replies"][2]["replayed"]
    checks["one_existing_terminal"] = sum(e["kind"] == "terminal" for e in lost["events"]) == 1
    checks["missing_terminal_not_success"] = not any(e["kind"] == "terminal" for e in groups["evidence-terminal"]["events"])
    checks["committed_write_survives_evidence_failure"] = "direct evidence-fault" in json.dumps(groups["evidence-db-ack"]["scopes"])
    for name in ("foreign-delete", "absent-delete"):
        checks[name] = groups[name]["replies"][1]["success"] is False
    checks["optimization_failure_can_remove_old_memory"] = not groups["optimize:missing"]["scopes"]["trial:A"]
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-", dir=args.scratch_parent))
    sources = [Path(__file__), Path(__file__).with_name("storage_prototype.py"),
               Path(__file__).with_name("integrated_storage_prototype.py"),
               Path(inspect.getfile(MemoryManager)), Path(inspect.getfile(SqliteDb))]
    report = {"verdict": "PARTIAL; NOT VALIDATED; live human review required", "scratch": str(root),
              "provenance": {"python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
                             "sqlite_source_id": sqlite3.connect(":memory:").execute("SELECT sqlite_source_id()").fetchone()[0],
                             "sqlite_compile_options": sqlite3.connect(":memory:").execute("PRAGMA compile_options").fetchall(),
                             "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
                             "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}},
              "groups": {}}
    routes = [[command("overlap-A", "overlap"), command("overlap-B", "overlap", agent="B")]]
    for route in ("automatic", "agentic", "create", "task", "add", "replace", "read", "search",
                  "hooks", "tool", "optimize", "delete", "clear-user"):
        routes.append([command(route, route, memory_id="seed")])
    routes += [[command("agent-scope", "add", scope="agent")],
               [command("other-scope", "read", agent="B", memory_id="agent-seed")],
               [command("agent-reuse", "read", scope="agent", memory_id="agent-seed")],
               [command("read-again", "read", scope="agent", memory_id="agent-seed")],
               [command("contention-A", "task"), command("contention-B", "task", agent="B")]]
    groups = [("routes", lambda p: probe(p, routes)),
              ("background", lambda p: probe(p, [[command("background", "background-error")]])),
              ("cross_process_gated", lambda p: race(p, True)),
              ("cross_process_unguarded_control", lambda p: race(p, False))]
    groups.append(("stock-foreign-id", stock_foreign_probe))
    for mode in ("fresh", "concurrent", "incompatible", "invalid-path", "writer-blocked", "gate-blocked"):
        groups.append(("startup-" + mode, lambda p, m=mode: startup_probe(p, m)))
    for route in ("wrong-owner", "foreign-id", "global-clear"):
        groups.append((route, lambda p, r=route: probe(p, [[command(r, r)]])))
    for name, target in (("foreign-delete", "foreign-seed"), ("absent-delete", "nonexistent")):
        groups.append((name, lambda p, n=name, t=target: probe(p, [[command(n, "delete", memory_id=t)]])))
    for method, route in (("upsert_user_memory", "automatic"), ("upsert_session", "automatic"),
                          ("upsert_run", "automatic"), ("delete_user_memory", "delete"),
                          ("delete_user_memories", "clear-user")):
        for fault in ("raise", "missing"):
            name = method + ":" + fault
            groups.append((name, lambda p, n=name, r=route: probe(p, [
                [command("fault", r, fault=n, memory_id="seed")],
                [command("after-failure", "read")]])))
    for kind in ("admitted", "db-ack", "db-error", "terminal"):
        groups.append(("evidence-" + kind, lambda p, k=kind: probe(p, [
            [command("evidence-fault", "add", evidence_fault=k,
                     **({"fault": "upsert_user_memory:raise"} if k == "db-error" else {}))],
            [command("after-failure", "read")]])))
    for route in ("agentic", "hooks", "tool", "optimize"):
        for fault in ("raise", "missing"):
            groups.append((route + ":" + fault, lambda p, r=route, f=fault: probe(p, [
                [command("fault", r, fault="upsert_user_memory:" + f)],
                [command("after-failure", "read")]])))
    duplicate = command("duplicate", "add", memory_id="duplicate")
    groups.append(("duplicates", lambda p: probe(p, [[duplicate], [duplicate],
                   [dict(duplicate, memory_id="conflicting")], [command("extension", "async-extension")]])))
    lost = command("lost", "add", memory_id="lost", drop_reply=True)
    groups.append(("lost-reply", lambda p: probe(p, [[lost], [dict(lost, drop_reply=False)]])))
    groups.append(("eligibility-model", lambda p: eligibility_probe()))
    for name, run in groups:
        try:
            report["groups"][name] = run(root / name)
        except Exception as error:
            report["groups"][name] = {"experiment_error": repr(error)}
            break
    if not any("experiment_error" in g for g in report["groups"].values()):
        report["checks"] = check_report(report["groups"])
    args.report.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(args.report)
    return int(any("experiment_error" in g for g in report["groups"].values())
               or not all(report.get("checks", {}).values()))


if __name__ == "__main__":
    raise SystemExit(main())
