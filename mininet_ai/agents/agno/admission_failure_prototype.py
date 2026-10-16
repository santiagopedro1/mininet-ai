"""THROWAWAY offline admission-failure boundary probe; not production or acceptance.

Item 2 of "Validate complete learning-gate and strict persistence coverage" (GH #58),
tracked as "Validate immediate admission shutdown after persistence failure" (GH #72):

    Can the long-lived per-run worker stop new admission at the first
    required-persistence failure while other operations are active, safely drain
    or contain admitted work, and report failure without releasing the learning
    gate early, losing known partial effects, or serializing away required
    independent concurrency?

Every run creates fresh disposable `PROTOTYPE-wipe-me-*` roots; no existing store is
opened. Model responses are scripted; Agno execution and SQLite are real. No Provider
client is constructed. This branch is throwaway: do not merge it.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import inspect
import json
import multiprocessing as mp
import platform
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from agno.agent import Agent
from agno.db.schemas.memory import UserMemory
from agno.memory.manager import MemoryManager
from agno.models.response import ModelResponse
from agno.run import RunStatus
from sqlalchemy import create_engine

sys.path.append(str(Path(__file__).resolve().parent))
from integrated_storage_prototype import OverlapModel, digest
from learning_gate_prototype import (
    OfflineOptimization,
    StrictDb,
    TrackedExecutor,
    event,
    prepare,
)
from storage_prototype import (
    BlockedLearningModel,
    ScriptedModel,
    database,
    gate,
    memories,
)

MEMORY_FREE = {"session", "overlap", "outcome"}
IDENTITIES = {"A", "B", "C"}
FAILURE_KINDS = {"db-error", "first-failure", "unquiesced"}


def profile_of(route: str) -> str:
    if route in MEMORY_FREE:
        return "memory-free"
    if route == "automatic":
        return "automatic"
    if route == "agentic":
        return "agentic"
    return "manager"


class LatchDb(StrictDb):
    """Adapter-armed failure latch: closes admission at the failing write.

    `StrictDb.checked` already journals and re-raises the required-write failure.
    This subclass additionally invokes `arm` synchronously *at the failure*,
    before Agno (or the operation loop) can swallow or finalize it. `arm is None`
    models the older boundary that only recognised failure at finalization.
    """

    def __init__(self, *args, arm=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.arm = arm
        self.required_failures: list[dict[str, Any]] = []

    def checked(self, method, **kwargs):
        try:
            return super().checked(method, **kwargs)
        except Exception as error:
            entry = {"method": method, "error": repr(error)}
            self.required_failures.append(entry)
            if self.arm is not None:
                self.arm(entry)
            raise


def latch_database(root, arm):
    root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{root / 'PROTOTYPE-wipe-me.sqlite3'}",
                           connect_args={"timeout": 0.2})
    return LatchDb(db_engine=engine, arm=arm)


class WorkerGate:
    """Worker-owned root-wide learning gate, held from first memory-capable
    admission through verified local quiescence.

    An flock held by this worker process denies the gate to any other process
    (controller or second worker). It is released only by the quiescence monitor,
    never per operation, so a late failure cannot unlock it early.
    """

    def __init__(self, root, timeout=3.0):
        self.root, self.timeout = Path(root), timeout
        self.file = None
        self.held = False
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            if self.held:
                return
            handle = (self.root / "PROTOTYPE-wipe-me.learning.lock").open("a")
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        handle.close()
                        raise TimeoutError("worker learning gate budget exceeded")
                    time.sleep(0.01)
            self.file, self.held = handle, True

    def release(self):
        with self.lock:
            if not self.held:
                return
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()
            self.file, self.held = None, False


class Ledger:
    """Operation state machine shared by owners, the monitor and the main loop.

    States: new -> (admitted) queued -> executing -> terminal, or queued ->
    refused-at-dispatch, or new -> refused-at-admission. `executing` counts only
    operations that actually began, so quiescence is "no executing and no queued".
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.states: dict[str, str] = {}
        self.queued = 0
        self.executing = 0

    def wake(self):
        with self.lock:
            self.cond.notify_all()


class RouteModel(ScriptedModel):
    def __init__(self, barrier=None):
        super().__init__()
        self.route = "reason"
        self.barrier = barrier

    def invoke(self, *args, **kwargs):
        messages = kwargs["messages"]
        self.seen = [m.to_dict() for m in messages]
        if messages[-1].role == "user" and messages[-1].content == "overlap":
            if self.barrier is None:
                raise RuntimeError("overlap requested without a fixture barrier")
            self.barrier.wait(timeout=3)
        if messages[-1].role != "tool" and self.route in {"tool", "agentic"}:
            name = "remember_fixture" if self.route == "tool" else "update_user_memory"
            return ModelResponse(tool_calls=[{
                "id": "admission-tool", "type": "function",
                "function": {"name": name, "arguments": '{"task":"offline tool fact"}'},
            }])
        return ModelResponse(content="offline response")


class FixtureLearningModel(BlockedLearningModel):
    """Blocks the stock automatic learning write at a fixture barrier.

    `announce` records entry so the controller can act while learning is active;
    `indefinite` never releases, modelling admitted work that cannot quiesce.
    """

    def __init__(self, entered, release, announce=None, indefinite=False):
        super().__init__(entered, release)
        self.announce = announce
        self.indefinite = indefinite

    def invoke(self, *args, **kwargs):
        if kwargs["messages"][-1].role != "tool":
            if self.announce is not None:
                self.announce()
            if self.indefinite:
                self.release.wait()
            elif not self.release.wait(5):
                raise TimeoutError("prototype background release safety limit")
        return ScriptedModel.invoke(self, *args, **kwargs)


def run_route(agent, manager, command, session, user):
    route = command["route"]
    if route in {"reason", "session", "overlap", "automatic", "agentic", "hooks", "tool"}:
        agent.model.route = route
        response = agent.run(command.get("text", "offline " + route), session_id=session,
                             user_id=user, run_id=command["id"])
        if response.status != RunStatus.completed:
            raise RuntimeError("Agno reasoning failed: " + str(response.status))
        return response.content
    if route == "outcome":
        return agent.update_session_state(command["state"], session_id=session)
    if route == "create":
        return manager.create_user_memories(message="offline", user_id=user)
    if route == "task":
        return manager.update_memory_task("offline", user_id=user)
    if route == "add":
        return manager.add_user_memory(
            UserMemory(memory_id=command.get("memory_id"), memory="direct " + command["id"]),
            user_id=user)
    if route == "replace":
        return manager.replace_user_memory(command["memory_id"],
                                           UserMemory(memory="replaced"), user_id=user)
    if route == "read":
        return manager.get_user_memory(command["memory_id"], user_id=user)
    if route == "search":
        return manager.search_user_memories(user_id=user, retrieval_method="last_n")
    if route == "delete":
        return manager.delete_user_memory(command["memory_id"], user_id=user)
    if route == "clear-user":
        return manager.clear_user_memories(user_id=user)
    if route == "optimize":
        return manager.optimize_memories(user_id=user, strategy=OfflineOptimization())
    raise ValueError("unsupported offline route: " + route)


ROUTES = ({"automatic", "agentic", "create", "task", "add", "replace", "read", "search",
           "delete", "clear-user", "optimize", "hooks", "tool", "overlap", "session",
           "outcome", "reason"})


def command(identity, route, **fields):
    return {"id": identity, "route": route, "agent": "A", **fields}


def admission_worker(root, channel, config=None):
    config = dict(config or {})
    latch_mode = config.get("latch_mode", "write")      # "write" | "finalize"
    dispatch_check = config.get("dispatch_check", True)
    quiescence_bound = config.get("quiescence_bound", 1.5)
    root = Path(root)
    path = root / "PROTOTYPE-wipe-me.sqlite3"
    owners, cached, seen = {}, {}, {}
    latch = threading.Event()
    latched: list[dict[str, Any]] = []
    latch_lock = threading.Lock()
    fallback: list[dict[str, Any]] = []
    send_lock = threading.Lock()
    barrier = threading.Barrier(2)
    learning_mutex = threading.Lock()
    worker_gate = WorkerGate(root)
    ledger = Ledger()
    quiesced = threading.Event()
    containment = threading.Event()
    background_releases: dict[str, threading.Event] = {}
    finalize_releases: dict[str, threading.Event] = {}
    monitor_started = threading.Event()

    def send(value):
        with send_lock:
            channel.send(value)

    def safe_event(operation, kind, payload):
        try:
            event(path, operation, kind, payload)
        except Exception as error:
            fallback.append({"kind": kind, "operation": operation, "error": repr(error)})

    def monitor():
        with ledger.lock:
            while ledger.executing or ledger.queued:
                if not ledger.cond.wait(timeout=quiescence_bound):
                    break
            quiet = not (ledger.executing or ledger.queued)
        if quiet:
            worker_gate.release()
            quiesced.set()
            safe_event("worker", "quiesced", {"executing": 0, "queued": 0})
        else:
            containment.set()
            safe_event("worker", "containment",
                       {"executing": ledger.executing, "queued": ledger.queued})

    def start_monitor():
        if monitor_started.is_set():
            return
        monitor_started.set()
        threading.Thread(target=monitor, daemon=True).start()

    def arm_latch(db, entry, source):
        with latch_lock:
            if latched:
                return
            record = {"source": source, "operation": db.operation, "entry": entry,
                      "executing": ledger.executing, "queued": ledger.queued}
            latched.append(record)
            latch.set()
        ledger.wake()
        try:
            db.emit("first-failure", record)
        except Exception as error:
            fallback.append({"kind": "first-failure", "operation": db.operation,
                             "error": repr(error)})
        start_monitor()

    def make_db():
        db = latch_database(root, None)
        if latch_mode == "write":
            db.arm = lambda entry, adapter=db: arm_latch(adapter, entry, "adapter")
        return db

    def execute(cmd):
        op, identity, route = cmd["id"], cmd["agent"], cmd["route"]
        session = "trial:" + identity
        user = session if cmd.get("scope", "run") == "run" else identity
        profile = profile_of(route)
        memory_free = profile == "memory-free"

        # --- dispatch boundary: admitted-but-not-started work is refused here ---
        with ledger.lock:
            ledger.queued -= 1
            if latch.is_set() and dispatch_check:
                ledger.states[op] = "refused-at-dispatch"
                ledger.cond.notify_all()
                refused = True
            else:
                ledger.executing += 1
                ledger.states[op] = "executing"
                refused = False
        if refused:
            safe_event(op, "dispatch-denied", {"command_hash": digest(cmd)})
            send({"id": op, "success": False, "run_failed": True, "dispatch": "refused",
                  "rejected": "queued work refused after failure latch"})
            return

        try:
            if memory_free:
                mutex: Any = nullcontext()
            else:
                worker_gate.acquire()
                mutex = learning_mutex
            with mutex:
                def emit(kind, payload):
                    if cmd.get("evidence_fault") == kind:
                        raise OSError("PROTOTYPE evidence write failure: " + kind)
                    event(path, op, kind, payload)

                emit("admitted", {"command_hash": digest(cmd), "user": user,
                                  "profile": profile})
                key = (identity, profile)
                if key not in cached:
                    db = make_db()
                    current = {"user": user}
                    manager = None
                    if not memory_free:
                        manager = MemoryManager(db=db, model=ScriptedModel("fact"))
                        current["manager"] = manager

                        def remember_fixture(task: str) -> str:
                            db.emit("custom-tool", {"task": task})
                            return manager.update_memory_task(task, user_id=current["user"])

                        def pre_hook():
                            manager.add_user_memory(UserMemory(memory="hook-pre"),
                                                    user_id=current["user"])

                        def post_hook():
                            manager.add_user_memory(UserMemory(memory="hook-post"),
                                                    user_id=current["user"])
                    options: dict[str, Any] = {}
                    if route == "hooks":
                        options = {"pre_hooks": [pre_hook], "post_hooks": [post_hook]}
                    if route == "tool":
                        options = {"tools": [remember_fixture]}
                    model = OverlapModel(barrier) if memory_free else RouteModel(barrier)
                    agent = Agent(id=identity, model=model, db=db, memory_manager=manager,
                                  update_memory_on_run=profile == "automatic",
                                  enable_agentic_memory=profile == "agentic",
                                  add_memories_to_context=not memory_free,
                                  add_history_to_context=True, cache_session=True,
                                  telemetry=False, **options)
                    agent._background_executor = TrackedExecutor()
                    cached[key] = (agent, manager, db, current)
                agent, manager, db, current = cached[key]
                current["user"] = user

                entered, release = threading.Event(), threading.Event()
                background_releases[op] = release
                if cmd.get("background") == "block":
                    manager.model = FixtureLearningModel(
                        entered, release, announce=lambda: emit("background-entered", {}))
                elif cmd.get("background") == "indefinite":
                    manager.model = FixtureLearningModel(
                        entered, release, announce=lambda: emit("background-entered", {}),
                        indefinite=True)
                elif manager is not None:
                    manager.model = ScriptedModel("fact-" + op)
                db.bind(user, op, emit, cmd.get("fault"))

                errors: list[str] = []
                status = None
                response = None
                route_error = None
                try:
                    response = run_route(agent, manager, cmd, session, user)
                    status = str(getattr(response, "status", "n/a"))
                except Exception as error:
                    route_error = repr(error)
                    errors.append(route_error)

                owned = len(agent.background_executor.owned)
                if cmd.get("foreground_return_event"):
                    emit("foreground-return", {"owned_futures": owned})
                try:
                    agent.background_executor.drain()
                except Exception as error:
                    errors.append("drain: " + repr(error))
                    if agent.background_executor.owned:
                        # Cannot verify local quiescence. Retain the gate and
                        # refuse to finalize; the controller must kill this exact
                        # worker. Not a deployment containment claim.
                        ledger.states[op] = "contained"
                        safe_event(op, "unquiesced",
                                   {"pending": len(agent.background_executor.owned)})
                        containment.set()
                        while True:
                            time.sleep(3600)

                if cmd.get("hold_forever"):
                    # Admitted work that cannot reach local quiescence. The worker
                    # must retain the gate and refuse successful cleanup.
                    safe_event(op, "hold-forever",
                               {"pending": len(agent.background_executor.owned)})
                    while True:
                        time.sleep(3600)

                if cmd.get("finalize_hold"):
                    emit("finalize-hold", {"op": op})
                    finalize_releases[op] = threading.Event()
                    finalize_releases[op].wait(6)

                errors.extend(db.errors)
                if errors and not latch.is_set():
                    arm_latch(db, {"errors": errors, "db_errors": list(db.errors),
                                   "route_error": route_error}, "finalize")

                run_failed = bool(errors) or latch.is_set()
                result = {
                    "id": op, "hash": digest(cmd), "success": not errors, "errors": errors,
                    "agno_status": status, "response": str(response),
                    "owner": threading.get_ident(), "agent_object": id(agent),
                    "adapter_object": id(db), "user": user, "profile": profile,
                    "memory_free": memory_free, "owned_futures": owned,
                    "dispatch_check": dispatch_check, "latch_mode": latch_mode,
                    "run_failed": run_failed,
                    "actionable_success": not errors and not latch.is_set(),
                    "memories": memories(db, user) if not memory_free else [],
                }
                db.active = False
                emit("terminal", result)
                if not cmd.get("drop_reply"):
                    send(result)
        except Exception as error:
            send({"id": op, "success": False, "run_failed": True,
                  "transport_only_error": repr(error)})
        finally:
            with ledger.lock:
                if ledger.states.get(op) != "contained":
                    ledger.states[op] = "terminal"
                ledger.executing -= 1
                ledger.cond.notify_all()

    send({"ready": True, "config": config})
    try:
        while True:
            try:
                cmd = channel.recv()
            except EOFError:
                break
            if "release_background" in cmd:
                background_releases[cmd["release_background"]].set()
                continue
            if "release_finalize" in cmd:
                finalize_releases[cmd["release_finalize"]].set()
                continue
            if cmd.get("route") == "stop":
                if containment.is_set():
                    send({"contained": True,
                          "refused": "gate retained; local quiescence unverified"})
                    while True:
                        time.sleep(3600)
                break
            if cmd.get("route") == "gate-probe":
                try:
                    with gate(root, timeout=0.06):
                        answer = "ACQUIRED"
                except TimeoutError:
                    answer = "denied"
                send({"gate_probe": answer})
                continue
            if cmd.get("route") not in ROUTES or cmd.get("agent") not in IDENTITIES:
                send({"id": cmd.get("id"), "rejected": "route/identity"})
                continue
            if cmd["id"] in seen:
                send({"id": cmd["id"], "duplicate": True,
                      "same_payload": seen[cmd["id"]] == digest(cmd)})
                continue
            if latch.is_set():
                # Admission boundary for new commands: no submit, no queue slot.
                with ledger.lock:
                    ledger.states[cmd["id"]] = "refused-at-admission"
                safe_event(cmd["id"], "admission-denied",
                           {"command_hash": digest(cmd), "latch": True})
                send({"id": cmd["id"], "success": False, "run_failed": True,
                      "rejected": "admission closed by failure latch"})
                continue
            seen[cmd["id"]] = digest(cmd)
            identity = cmd["agent"]
            if identity not in owners:
                owners[identity] = ThreadPoolExecutor(max_workers=1)
            with ledger.lock:
                ledger.queued += 1
            owners[identity].submit(execute, cmd)
    finally:
        for owner in owners.values():
            owner.shutdown(wait=False)
        channel.close()


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #

def reply(parent, timeout=10):
    if not parent.poll(timeout):
        raise TimeoutError("bounded prototype reply wait")
    return parent.recv()


def wait_event(path, kind, operation=None, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with sqlite3.connect(path) as conn:
            if operation is None:
                row = conn.execute("SELECT seq,payload FROM prototype_events WHERE kind=? "
                                   "ORDER BY seq LIMIT 1", (kind,)).fetchone()
            else:
                row = conn.execute("SELECT seq,payload FROM prototype_events WHERE kind=? AND op=? "
                                   "ORDER BY seq LIMIT 1", (kind, operation)).fetchone()
        if row:
            return {"seq": row[0], "payload": json.loads(row[1])}
        time.sleep(0.01)
    raise TimeoutError("event budget exceeded: " + kind)


def collect_replies(parent, wanted, timeout=10):
    """Return replies keyed by id, ignoring any others."""
    found: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    while set(found) != set(wanted) and time.monotonic() < deadline:
        if parent.poll(0.2):
            value = parent.recv()
            if value.get("id") in wanted:
                found[value["id"]] = value
    return found


def start_worker(root, config):
    prepare(root)
    path = root / "PROTOTYPE-wipe-me.sqlite3"
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=admission_worker, args=(str(root), child, config))
    process.daemon = True
    process.start()
    child.close()
    reply(parent, 8)
    return {"root": root, "path": path, "parent": parent, "process": process}


def collect(handle, *, kill=False, replies=None):
    parent, process, path, root = (handle["parent"], handle["process"],
                                   handle["path"], handle["root"])
    if not kill:
        parent.send({"route": "stop"})
        process.join(8)
    if process.is_alive():
        process.kill()
        process.join(2)
    with sqlite3.connect(path) as conn:
        events = [{"seq": s, "id": o, "kind": k, "payload": json.loads(p)}
                  for s, o, k, p in conn.execute("SELECT * FROM prototype_events ORDER BY seq")]
        pragmas = {name: [tuple(r) for r in conn.execute("PRAGMA " + name)]
                   for name in ("journal_mode", "synchronous", "integrity_check")}
    db = database(root)
    scopes = {user: memories(db, user) for user in ("trial:A", "trial:B", "trial:C", "A", "B", "C")}
    db.close()
    parent.close()
    return {"replies": list((replies or {}).values()) if isinstance(replies, dict) else (replies or []),
            "replies_by_id": replies if isinstance(replies, dict) else None,
            "events": events, "scopes": scopes, "exitcode": process.exitcode,
            "live_files": sorted(p.name for p in root.iterdir()),
            "pragmas_inspection_connection": pragmas}


def failure_marker(events):
    seqs = [e["seq"] for e in events if e["kind"] in FAILURE_KINDS]
    return min(seqs) if seqs else None


def first_failure(events):
    return next((e for e in events if e["kind"] == "first-failure"), None)


def started_after_failure(events):
    marker = failure_marker(events)
    if marker is None:
        return []
    return [e["id"] for e in events if e["kind"] == "admitted" and e["seq"] > marker]


def refused_after_failure(events, kind):
    marker = failure_marker(events)
    return [e for e in events if e["kind"] == kind
            and (marker is None or e["seq"] > marker)]


# --------------------------------------------------------------------------- #
# Groups
# --------------------------------------------------------------------------- #

def group_queued(root, config):
    """A required write fails while a memory-free op is already queued on A's owner."""
    handle = start_worker(root, config)
    parent = handle["parent"]
    parent.send(command("A", "add", memory_id="seed", fault="upsert_user_memory:raise"))
    parent.send(command("C", "session", text="queued before failure"))
    replies = collect_replies(parent, {"A", "C"})
    parent.send(command("D", "session", text="late after failure"))
    replies.update(collect_replies(parent, {"D"}))
    return collect(handle, replies=replies)


def group_arm_timing(root, config):
    """A's background learning write fails; a new command arrives during the
    finalization window, after the write failed but before errors were inspected."""
    handle = start_worker(root, config)
    parent, path = handle["parent"], handle["path"]
    parent.send(command("A", "automatic", fault="upsert_user_memory:raise",
                        background="block", finalize_hold=True))
    wait_event(path, "background-entered", "A")
    parent.send({"release_background": "A"})
    # The write has now failed; A holds at finalization so the window between
    # the failing write and the finalization latch is observable in both modes.
    wait_event(path, "finalize-hold", "A")
    parent.send(command("D", "session", agent="B", text="during finalization"))
    late = collect_replies(parent, {"D"})
    parent.send({"release_finalize": "A"})
    replies = collect_replies(parent, {"A"})
    replies.update(late)
    return collect(handle, replies=replies)


def group_active_sibling(root, config):
    """A's background write fails while a memory-free sibling is executing."""
    handle = start_worker(root, config)
    parent, path = handle["parent"], handle["path"]
    parent.send(command("A", "automatic", fault="upsert_user_memory:raise", background="block"))
    parent.send(command("B", "session", agent="B", text="active sibling", finalize_hold=True))
    wait_event(path, "finalize-hold", "B")
    wait_event(path, "background-entered", "A")
    parent.send({"release_background": "A"})
    wait_event(path, "first-failure")
    parent.send(command("D", "session", agent="B", text="late"))
    recent = collect_replies(parent, {"D"})
    parent.send({"release_finalize": "B"})
    recent.update(collect_replies(parent, {"A", "B"}))
    return collect(handle, replies=recent)


def group_failure_classes(root, config):
    results = {}
    for method in ("upsert_user_memory", "upsert_session", "upsert_run"):
        for fault in ("raise", "missing"):
            name = method + ":" + fault
            selected = root / (method + "-" + fault)
            handle = start_worker(selected, config)
            parent = handle["parent"]
            parent.send(command("A", "automatic", fault=name))
            first = collect_replies(parent, {"A"})
            parent.send(command("D", "session", text="after failure"))
            first.update(collect_replies(parent, {"D"}))
            results[name] = collect(handle, replies=first)
    selected = root / "memory-free-session-fault"
    handle = start_worker(selected, config)
    parent = handle["parent"]
    parent.send(command("A", "session", text="memory-free write", fault="upsert_session:raise"))
    first = collect_replies(parent, {"A"})
    parent.send(command("D", "session", text="after failure"))
    first.update(collect_replies(parent, {"D"}))
    results["memory-free:upsert_session:raise"] = collect(handle, replies=first)
    return results


def group_evidence_failures(root, config):
    results = {}
    for kind in ("db-error", "terminal"):
        selected = root / kind
        handle = start_worker(selected, config)
        parent, path = handle["parent"], handle["path"]
        parent.send(command("A", "automatic", fault="upsert_user_memory:raise",
                            background="block", evidence_fault=kind))
        wait_event(path, "background-entered", "A")
        parent.send({"release_background": "A"})
        wait_event(path, "first-failure")
        parent.send(command("D", "session", text="after failure"))
        results[kind] = collect(handle, replies=collect_replies(parent, {"D"}, timeout=5))
    return results


def group_quiescence(root, config):
    handle = start_worker(root, config)
    parent, path, root_dir = handle["parent"], handle["path"], handle["root"]
    parent.send(command("A", "add", memory_id="seed", fault="upsert_user_memory:raise"))
    replies = collect_replies(parent, {"A"})
    quiesced = wait_event(path, "quiesced")
    try:
        with gate(root_dir, timeout=0.3):
            gate_released = True
    except TimeoutError:
        gate_released = False
    parent.send(command("D", "session", text="after failure"))
    replies.update(collect_replies(parent, {"D"}))
    observed = collect(handle, replies=replies)
    observed["quiesced"] = quiesced
    observed["gate_released_after_quiescence"] = gate_released
    return observed


def group_containment(root, config):
    handle = start_worker(root, config)
    parent, path = handle["parent"], handle["path"]
    # A required write fails (latch armed at the write), then this admitted
    # operation cannot reach local quiescence.
    parent.send(command("A", "add", memory_id="seed", fault="upsert_user_memory:raise",
                        hold_forever=True))
    containment = wait_event(path, "containment")
    parent.send({"route": "gate-probe"})
    probe = reply(parent)
    parent.send({"route": "stop"})
    try:
        stop = reply(parent, 3)
    except TimeoutError:
        stop = {"no_stop_reply": True}
    observed = collect(handle, kill=True, replies=[stop])
    observed["containment"] = containment
    observed["gate_probe_while_contained"] = probe
    observed["local_contained"] = not handle["process"].is_alive()
    return observed


def group_healthy(root, config):
    handle = start_worker(root, config)
    parent, path = handle["parent"], handle["path"]
    parent.send(command("A", "automatic", background="block"))
    wait_event(path, "background-entered", "A")
    # Independent memory-free overlap; a global serial command loop breaks this.
    parent.send(command("B", "overlap", agent="B", text="overlap"))
    parent.send(command("C", "overlap", agent="C", text="overlap"))
    parent.send({"release_background": "A"})
    return collect(handle, replies=collect_replies(parent, {"A", "B", "C"}, timeout=12))


def check_report(groups):
    checks: dict[str, bool] = {}

    def group(name):
        return groups[name]

    # --- proposed boundary: write-armed latch + dispatch check ---
    proposed = group("queued/proposed")
    failure = first_failure(proposed["events"])
    checks["proposed:latch_armed_at_write"] = (
        failure is not None and failure["payload"]["source"] == "adapter")
    checks["proposed:no_start_after_failure"] = not started_after_failure(proposed["events"])
    checks["proposed:queued_refused_at_dispatch"] = bool(
        refused_after_failure(proposed["events"], "dispatch-denied"))
    checks["proposed:late_refused_at_admission"] = bool(
        refused_after_failure(proposed["events"], "admission-denied"))
    checks["proposed:latch_before_terminal"] = (
        failure is not None and failure["seq"] < max(
            (e["seq"] for e in proposed["events"] if e["kind"] == "terminal"), default=0))

    # --- dispatch check is load-bearing for already-admitted work ---
    no_dispatch = group("queued/no-dispatch")
    checks["no-dispatch:queued_runs_after_failure"] = bool(
        started_after_failure(no_dispatch["events"]))

    # --- write-armed latch is load-bearing for the finalization window ---
    write = group("arm-timing/write")
    finalize = group("arm-timing/finalize")
    checks["arm-timing:write_refuses_late"] = bool(
        refused_after_failure(write["events"], "admission-denied"))
    checks["arm-timing:finalize_admits_late"] = bool(
        started_after_failure(finalize["events"]))

    # --- active sibling drains; no actionable success after the gate ---
    active = group("concurrent/active-sibling")
    active_failure = first_failure(active["events"])
    checks["concurrent:two_executing_at_failure"] = (
        active_failure is not None and active_failure["payload"]["executing"] >= 2)
    checks["concurrent:no_start_after_failure"] = not started_after_failure(active["events"])
    checks["concurrent:late_refused"] = bool(
        refused_after_failure(active["events"], "admission-denied"))
    sibling = active["replies_by_id"].get("B") if active["replies_by_id"] else None
    checks["concurrent:sibling_drained_not_actionable"] = (
        sibling is not None and sibling.get("success") is True
        and sibling.get("run_failed") is True
        and sibling.get("actionable_success") is False)

    # --- failure classes all latch and refuse post-failure admission ---
    classes = group("failure-classes")
    for name, observed in classes.items():
        checks[f"classes:{name}:latched"] = first_failure(observed["events"]) is not None
        checks[f"classes:{name}:no_start_after"] = not started_after_failure(observed["events"])
        checks[f"classes:{name}:late_refused"] = bool(
            refused_after_failure(observed["events"], "admission-denied"))

    # --- evidence write failure: latches but cannot present durable evidence ---
    evidence = group("evidence-failures")
    for kind, observed in evidence.items():
        checks[f"evidence:{kind}:latched"] = first_failure(observed["events"]) is not None
        checks[f"evidence:{kind}:no_start_after"] = not started_after_failure(observed["events"])
    checks["evidence:db-error:cause_evidence_missing"] = not any(
        e["kind"] == "db-error" for e in evidence["db-error"]["events"])

    # --- quiescence releases the gate; containment retains it ---
    quiescence = group("quiescence")
    checks["quiescence:verified"] = quiescence.get("quiesced", {}).get("payload") == {
        "executing": 0, "queued": 0}
    checks["quiescence:gate_released"] = quiescence.get("gate_released_after_quiescence") is True
    checks["quiescence:late_still_refused"] = bool(
        refused_after_failure(quiescence["events"], "admission-denied"))
    containment = group("containment")
    checks["containment:declared"] = containment.get("containment", {}).get(
        "payload", {}).get("executing", 0) >= 1
    checks["containment:gate_retained"] = containment.get(
        "gate_probe_while_contained", {}).get("gate_probe") == "denied"
    checks["containment:local_contained"] = containment.get("local_contained") is True

    # --- healthy control: no latch, overlap retained ---
    healthy = group("healthy")
    checks["healthy:no_latch"] = first_failure(healthy["events"]) is None
    checks["healthy:all_success"] = all(r.get("success") is True for r in healthy["replies"])
    owners = {r["owner"] for r in healthy["replies"] if r.get("id") in {"B", "C"}}
    checks["healthy:independent_owners"] = len(owners) == 2
    return checks


def provenance():
    from agno.db.sqlite import SqliteDb
    sources = [Path(__file__), Path(__file__).with_name("learning_gate_prototype.py"),
               Path(__file__).with_name("storage_prototype.py"),
               Path(__file__).with_name("integrated_storage_prototype.py"),
               Path(inspect.getfile(MemoryManager)), Path(inspect.getfile(SqliteDb))]
    with sqlite3.connect(":memory:") as conn:
        source_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
        compile_options = conn.execute("PRAGMA compile_options").fetchall()
    return {"python": sys.version, "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version, "sqlite_source_id": source_id,
            "sqlite_compile_options": compile_options,
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
            "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}


CONFIGS = {
    "write+dispatch": {"latch_mode": "write", "dispatch_check": True},
    "write,no-dispatch": {"latch_mode": "write", "dispatch_check": False},
    "finalize+dispatch": {"latch_mode": "finalize", "dispatch_check": True},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-admission-",
                                 dir=args.scratch_parent)).resolve()
    report: dict[str, Any] = {
        "verdict": "PARTIAL; live human review required",
        "question": ("Can the per-run worker stop new admission at the first "
                     "required-persistence failure while other operations are active, "
                     "safely drain or contain admitted work, and report failure without "
                     "releasing the gate early, losing partial effects, or serializing "
                     "away independent concurrency?"),
        "scratch": str(root), "provenance": provenance(), "groups": {},
        "limits": [
            "No Provider client, credentials, network call or production code path.",
            "Scripted model responses; real Agno execution and real SQLite.",
            ("Controller and worker are offline prototype processes, not the production "
             "coordinator or a deployment supervisor."),
            ("Local drain/exit is not remote termination; unresolved remote work still "
             "blocks the next independent trial."),
            ("Containment reuses the existing prototype kill/join fixture; it does not "
             "establish the deployment ticket's descendant containment contract."),
        ]}
    groups: list[tuple[str, Any]] = [
        ("queued/proposed", lambda p: group_queued(p, CONFIGS["write+dispatch"])),
        ("queued/no-dispatch", lambda p: group_queued(p, CONFIGS["write,no-dispatch"])),
        ("arm-timing/write", lambda p: group_arm_timing(p, CONFIGS["write+dispatch"])),
        ("arm-timing/finalize", lambda p: group_arm_timing(p, CONFIGS["finalize+dispatch"])),
        ("concurrent/active-sibling", lambda p: group_active_sibling(p, CONFIGS["write+dispatch"])),
        ("failure-classes", lambda p: group_failure_classes(p, CONFIGS["write+dispatch"])),
        ("evidence-failures", lambda p: group_evidence_failures(p, CONFIGS["write+dispatch"])),
        ("quiescence", lambda p: group_quiescence(p, CONFIGS["write+dispatch"])),
        ("containment", lambda p: group_containment(
            p, {"latch_mode": "write", "dispatch_check": True, "quiescence_bound": 1.0})),
        ("healthy", lambda p: group_healthy(p, CONFIGS["write+dispatch"])),
    ]
    for name, run in groups:
        print("Running " + name, flush=True)
        try:
            report["groups"][name] = run(root / name)
        except Exception as error:
            report["groups"][name] = {"experiment_error": repr(error)}
            break
    if not any("experiment_error" in g for g in report["groups"].values()):
        report["checks"] = check_report(report["groups"])
    args.report.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(args.report)
    failed = [name for name, ok in report.get("checks", {}).items() if not ok]
    if "checks" in report:
        print("failed checks:", failed or "none")
    return int(bool(failed) or any("experiment_error" in g for g in report["groups"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
