"""THROWAWAY GH-73 offline worker/import/extension/alias probe. Do not merge.

Only the generated, enumerated trusted fixtures are eligible. This is not a
production Python loader or a validator for arbitrary Python. No Provider calls.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import multiprocessing as mp
import os
import platform
import sqlite3
import sys
import tempfile
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from agno.agent import Agent
from agno.db.schemas.memory import UserMemory
from agno.memory.manager import MemoryManager
from agno.run import RunStatus
from sqlalchemy import create_engine

sys.path.append(str(Path(__file__).resolve().parent))
from admission_failure_prototype import LatchDb, RouteModel, WorkerGate, reply
from integrated_storage_prototype import OverlapModel
from learning_gate_prototype import TrackedExecutor, prepare
from storage_prototype import ScriptedModel, gate

DB_NAME = "PROTOTYPE-wipe-me.sqlite3"
FORMS = {
    "factory", "module-agent", "singleton-factory", "local-resources",
    "parent-state", "running-loop", "import-thread", "hooks", "tool", "background",
    "raw-engine", "alternate-store", "untracked", "async-factory",
}
REJECTED_CONTRACTS = {"raw-engine", "alternate-store", "untracked", "async-factory"}

# The actual files and imports, not an object-identity registry simulation.
# Each module is imported once per worker/form, on its first owning thread.
FIXTURE_SOURCE = '''\
import asyncio
import os
import sqlite3
import threading
from eligibility_fixture_context import current as context
IMPORT_PID = os.getpid()
IMPORT_THREAD = threading.get_ident()
FORM = __FORM__
context.note("module-import", {"form": FORM, "pid": IMPORT_PID, "thread": IMPORT_THREAD})
if FORM == "import-thread":
    connection = sqlite3.connect(context.path)
if FORM in {"module-agent", "singleton-factory"}:
    agent = context.make()

def factory():
    if FORM == "import-thread":
        try:
            connection.execute("SELECT 1").fetchone()
        except sqlite3.ProgrammingError as error:
            raise RuntimeError("module resource belongs to its import thread; cannot reuse on another owner") from error
    if FORM == "singleton-factory":
        return agent
    if FORM == "parent-state":
        import prototype_parent_state
        if prototype_parent_state.VALUE is None:
            raise RuntimeError("parent-only global/resource absent in spawned worker; initialize locally")
    if FORM == "running-loop":
        asyncio.get_running_loop()
    return context.make(resources=FORM == "local-resources")

async def async_factory():
    return context.make()
'''


def load_module(name, path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("fixture import spec unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class StoragePin:
    """Bounded local path checks. Not a defense against hostile filesystem races.

    Root symlinks canonicalize. DB symlinks/hardlinks are rejected. Pin the root,
    DB and gate inode and recheck before each adapter access. Cooperative callers
    must not mutate the namespace during access; no production manifest implied.
    """

    def __init__(self, root):
        self.requested = Path(root).absolute()
        self.root = self.requested.resolve(strict=True)
        self.path = self.root / DB_NAME
        self.lock_path = self.root / "PROTOTYPE-wipe-me.learning.lock"
        self.lock_path.touch(exist_ok=True)
        self.pinned = self.signature()

    def signature(self):
        if self.requested.resolve(strict=True) != self.root:
            raise ValueError("selected root alias was retargeted")
        result = []
        for path in (self.root, self.path, self.lock_path):
            if path.is_symlink():
                raise ValueError("store/gate symlink unsupported")
            stat = path.stat()
            if path != self.root and stat.st_nlink != 1:
                raise ValueError("store/gate hardlink unsupported")
            result.append((stat.st_dev, stat.st_ino))
        return result

    def check(self):
        stat = self.root.stat()
        if (stat.st_dev, stat.st_ino) != self.pinned[0]:
            raise ValueError("storage root inode replaced")
        if self.signature() != self.pinned:
            raise ValueError("storage root/store/gate inode replaced")


class EligibilityDb(LatchDb):
    pin: StoragePin
    worker_gate: WorkerGate

    def checked(self, method, **kwargs):
        self.pin.check()
        if "memor" in method and not self.worker_gate.held:
            raise RuntimeError("memory access before root gate")
        return super().checked(method, **kwargs)


def eligibility_worker(root, fixture_dir, channel):
    root, fixture_dir = Path(root), Path(fixture_dir)
    sys.path.insert(0, str(fixture_dir))
    pin = StoragePin(root)
    worker_gate = WorkerGate(pin.root)
    learning_mutex, registry_lock, output_lock = (threading.Lock() for _ in range(3))
    failed = threading.Event()
    release = threading.Event()
    barrier = threading.Barrier(2)
    owners, cache, modules, bindings = {}, {}, {}, {}
    events = []

    def note(kind, payload):
        with output_lock:
            record = {"seq": len(events), "kind": kind, **payload}
            events.append(record)

    def send(value):
        with output_lock:
            channel.send(value)

    def execute(cmd):
        op, identity, form = cmd["id"], cmd["identity"], cmd["form"]
        memory = form in {"hooks", "tool", "background"}
        user = identity
        errors = []
        db = agent = None
        note("command", {"id": op, "command": cmd})
        if failed.is_set():
            send({"id": op, "rejected": "failure latch closed dispatch"})
            return
        try:
            if form in REJECTED_CONTRACTS:
                raise ValueError("unsupported trusted-extension contract: " + form)
            if memory:
                worker_gate.acquire()
            with learning_mutex if memory else nullcontext():
                pin.check()
                note("operation-start", {"id": op, "owner": threading.get_ident(),
                                          "pid": os.getpid(), "gate": worker_gate.held})
                if identity in cache and cache[identity][0] != form:
                    raise ValueError("cached session entrypoint/profile is immutable")
                if identity not in cache:
                    def make(resources=False):
                        engine = create_engine(f"sqlite:///{pin.path}",
                                               connect_args={"timeout": 0.5})
                        adapter = EligibilityDb(db_engine=engine)
                        adapter.arm = lambda entry: (
                            failed.set(), note("failure-latch", {"id": adapter.operation, **entry}))
                        adapter.pin, adapter.worker_gate = pin, worker_gate
                        with engine.connect() as connection:
                            note("adapter-opened", {"id": op, "engine": id(engine),
                                "pid": os.getpid(), "owner": threading.get_ident(),
                                "database_list": [tuple(row) for row in connection.exec_driver_sql(
                                    "PRAGMA database_list")],
                                "journal_mode": connection.exec_driver_sql(
                                    "PRAGMA journal_mode").scalar(),
                                "synchronous": connection.exec_driver_sql(
                                    "PRAGMA synchronous").scalar()})
                        manager = MemoryManager(db=adapter, model=ScriptedModel("fixture fact"))
                        executor = TrackedExecutor()

                        def write_fact(label):
                            adapter.emit("extension-enter", {"label": label})
                            manager.search_user_memories(user_id=user, retrieval_method="last_n")
                            return manager.add_user_memory(UserMemory(memory=label), user_id=user)

                        def hook():
                            write_fact("hook")

                        def remember_fixture(task: str) -> str:
                            """Remember an offline controlled fact."""
                            write_fact("tool " + task)
                            return "remembered"

                        def background_hook():
                            def background():
                                note("background-waiting", {"id": adapter.operation,
                                                            "gate": worker_gate.held})
                                send({"background_waiting": adapter.operation})
                                if not release.wait(4):
                                    raise TimeoutError("controlled background release budget")
                                write_fact("owned-background")
                                note("background-done", {"id": adapter.operation,
                                                         "gate": worker_gate.held})
                            executor.submit(background)

                        options: dict[str, Any] = {}
                        if form == "hooks":
                            options = {"pre_hooks": [hook], "post_hooks": [hook]}
                        elif form == "tool":
                            options = {"tools": [remember_fixture]}
                        elif form == "background":
                            options = {"pre_hooks": [background_hook]}
                        model: Any = RouteModel() if form == "tool" else OverlapModel(barrier)
                        if form == "tool":
                            model.route = "tool"
                        instance: Any = Agent(model=model, db=adapter,
                                         memory_manager=manager if memory else None,
                                         add_memories_to_context=memory,
                                         cache_session=True, add_history_to_context=True,
                                         telemetry=False, **options)
                        instance._background_executor = executor
                        local = None
                        if resources:
                            # A local initialized client surrogate, live SQLite connection,
                            # and lock; no Provider client/network/credentials involved.
                            local = {"pid": os.getpid(), "thread": threading.get_ident(),
                                     "connection": sqlite3.connect(pin.path),
                                     "lock": threading.Lock(), "client": {"ready": True}}
                        instance._fixture_resources = local
                        note("constructed", {"id": op, "object": id(instance),
                                             "pid": os.getpid(), "thread": threading.get_ident(),
                                             "resources": bool(resources)})
                        return instance

                    # Import and singleton claiming share a short lock, not execution.
                    with registry_lock:
                        context: Any = types.ModuleType("eligibility_fixture_context")
                        context.current = types.SimpleNamespace(make=make, note=note, path=str(pin.path))
                        sys.modules[context.__name__] = context
                        if form not in modules:
                            modules[form] = load_module("fixture_" + form.replace("-", "_"),
                                                        fixture_dir / (form + ".py"))
                            modules[form]._owner_identity = identity
                        module = modules[form]
                        # Fresh factory calls must receive the CURRENT session context,
                        # not the closure of the session which first imported the module.
                        module.context = context.current
                        candidate = module.agent if form == "module-agent" else module.factory()
                        if not isinstance(candidate, Agent):
                            raise TypeError("entrypoint did not return synchronous Agent")
                        if id(candidate) in bindings and bindings[id(candidate)] != identity:
                            raise ValueError("mutable singleton alias already owned by "
                                             + bindings[id(candidate)] + "; no cloning/serialization fallback")
                        bindings[id(candidate)] = identity
                        cache[identity] = (form, candidate)
                agent = cache[identity][1]
                db = agent.db
                def emit(kind, payload):
                    note(kind, {"id": op, "gate": worker_gate.held, **payload})
                db.bind(user, op, emit, cmd.get("fault"))
                resources = agent._fixture_resources
                if resources:
                    if resources["thread"] != threading.get_ident():
                        raise ValueError("worker-local resource thread affinity violated")
                    with resources["lock"]:
                        resources["connection"].execute("SELECT 1").fetchone()
                    emit("resource-use", {"pid": resources["pid"], "thread": resources["thread"],
                                          "client_ready": resources["client"]["ready"]})
                response = None
                try:
                    response = agent.run(cmd.get("text", "offline"), session_id=identity,
                                         user_id=user, run_id=op)
                    if response.status != RunStatus.completed:
                        errors.append("Agno status: " + str(response.status))
                except Exception as error:
                    errors.append(repr(error))
                emit("foreground-return", {"owned": len(agent.background_executor.owned)})
                try:
                    agent.background_executor.drain()
                except Exception as error:
                    errors.append("drain: " + repr(error))
                if agent.background_executor.owned:
                    # Keep root gate held; controller will contain this disposable worker.
                    failed.set()
                    raise RuntimeError("unverified background quiescence; gate retained")
                errors.extend(db.errors)
                db.active = False
                emit("drained", {"owned": len(agent.background_executor.owned)})
                emit("terminal", {"success": not errors and not failed.is_set()})
                send({"id": op, "success": not errors and not failed.is_set(), "errors": errors,
                      "agno_status": str(getattr(response, "status", None)),
                      "agent": id(agent), "adapter": id(db), "engine": id(db.db_engine),
                      "model": id(agent.model), "messages": agent.model.seen,
                      "manager": id(agent.memory_manager),
                      "executor": id(agent.background_executor), "owner": threading.get_ident(),
                      "pid": os.getpid(), "identity": identity, "form": form})
        except Exception as error:
            note("rejected", {"id": op, "identity": identity, "form": form, "error": repr(error)})
            send({"id": op, "rejected": repr(error), "identity": identity, "form": form})

    send({"ready": True, "pid": os.getpid(), "root_pin": pin.pinned})
    while True:
        cmd = channel.recv()
        if cmd.get("stop"):
            break
        if cmd.get("release"):
            release.set()
            continue
        if failed.is_set():
            send({"id": cmd.get("id"), "rejected": "failure latch closed admission"})
            continue
        if set(cmd) - {"id", "identity", "form", "text", "fault"} or cmd.get("form") not in FORMS:
            send({"id": cmd.get("id"), "rejected": "only normalized fixture descriptors allowed"})
            continue
        identity = cmd["identity"]
        if identity not in owners:
            owners[identity] = ThreadPoolExecutor(max_workers=1)
        owners[identity].submit(execute, cmd)
    for module in modules.values():
        if hasattr(module, "connection"):
            owners[module._owner_identity].submit(module.connection.close).result(timeout=4)
    for identity, (_, agent) in cache.items():
        if agent._fixture_resources:
            owners[identity].submit(agent._fixture_resources["connection"].close).result(timeout=4)
    for owner in owners.values():
        owner.shutdown(wait=True)
    pending = sum(len(a.background_executor.owned) for _, a in cache.values())
    if not pending:
        worker_gate.release()
    files = sorted(p.name for p in root.iterdir())
    for _, agent in cache.values():
        agent.background_executor.shutdown(wait=True)
        agent.db.close()
    send({"events": events, "pending": pending, "files_before_close": files,
          "gate_released": not worker_gate.held})
    channel.close()


def start(root, fixtures):
    prepare(root)
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=eligibility_worker, args=(str(root), str(fixtures), child))
    process.start()
    child.close()
    ready = reply(parent)
    return parent, process, ready


def stop(handle, results):
    parent, process, ready = handle
    parent.send({"stop": True})
    final = reply(parent)
    process.join(8)
    if process.is_alive():
        process.kill()
        process.join(2)
        raise RuntimeError("prototype worker failed bounded exit")
    parent.close()
    return {"ready": ready, "results": results, **final, "exitcode": process.exitcode}


def command(op, identity, form, **kwargs):
    return {"id": op, "identity": identity, "form": form, **kwargs}


def group_loader(root, fixtures):
    handle = start(root, fixtures)
    parent = handle[0]
    results = []
    commands = [
        command("factory-A", "trial:A", "factory", text="only-A-private-history-marker"),
        command("factory-A-again", "trial:A", "factory"),
        command("factory-B", "trial:B", "factory"),
        command("module-A", "module:A", "module-agent"),
        command("module-A-again", "module:A", "module-agent"),
        command("module-B-reject", "module:B", "module-agent"),
        command("singleton-A", "singleton:A", "singleton-factory"),
        command("singleton-B-reject", "singleton:B", "singleton-factory"),
        command("local", "local:A", "local-resources"),
        command("local-again", "local:A", "local-resources"),
        command("import-thread-A", "import-thread:A", "import-thread"),
        command("import-thread-B-reject", "import-thread:B", "import-thread"),
        command("parent-reject", "parent:A", "parent-state"),
        command("loop-reject", "loop:A", "running-loop"),
    ]
    commands += [command(form, form, form) for form in sorted(REJECTED_CONTRACTS)]
    for cmd in commands:
        parent.send(cmd)
        results.append(reply(parent))
    for identity in ("stateless:invocation-1", "stateless:invocation-2"):
        parent.send(command(identity, identity, "factory", text="overlap"))
    results.extend([reply(parent), reply(parent)])
    return stop(handle, results)


def group_extension(root, fixtures, form, fault=None):
    handle = start(root, fixtures)
    parent = handle[0]
    parent.send(command("extension", "trial:extension", form, fault=fault))
    messages = [reply(parent)]
    blocked = None
    if form == "background":
        if messages[0].get("background_waiting") != "extension":
            raise RuntimeError("background fixture did not reach owned work")
        try:
            with gate(root, timeout=0.06):
                blocked = False
        except TimeoutError:
            blocked = True
        parent.send({"release": True})
        messages.append(reply(parent))
    if fault:
        parent.send(command("late", "late", "factory"))
        messages.append(reply(parent))
    data = stop(handle, messages)
    data["external_gate_denied_while_background"] = blocked
    with sqlite3.connect(root / DB_NAME) as connection:
        data["memory_rows"] = connection.execute(
            "SELECT user_id,memory FROM agno_memories ORDER BY user_id,memory").fetchall()
    return data


def alias_groups(root):
    root.mkdir()
    results = {}
    def attempt(name, action):
        try:
            results[name] = {"accepted": True, "value": action()}
        except Exception as error:
            results[name] = {"accepted": False, "diagnostic": repr(error)}
    selected = root / "selected"
    prepare(selected)
    pin = StoragePin(selected)
    alias = root / "root-symlink"
    alias.symlink_to(selected, target_is_directory=True)
    relative = Path(os.path.relpath(selected))
    attempt("relative-canonical", lambda: StoragePin(relative).pinned == pin.pinned)
    attempt("root-symlink", lambda: StoragePin(alias).pinned == pin.pinned)
    owner = WorkerGate(pin.root)
    owner.acquire()
    try:
        def conflicting_owner():
            with gate(StoragePin(alias).root, timeout=0.06):
                return "UNSAFE acquired"
        attempt("conflicting-owner", conflicting_owner)
    finally:
        owner.release()
    hardlink = root / "db-hardlink"
    os.link(pin.path, hardlink)
    attempt("db-hardlink", pin.check)
    hardlink.unlink()  # Only the alias created immediately above; retain original DB.
    target = root / "saved-db"
    pin.path.rename(target)
    pin.path.symlink_to(target)
    attempt("db-symlink", pin.check)
    pin.path.unlink()  # Only the fixture symlink just created.
    target.rename(pin.path)
    pin.lock_path.rename(root / "saved-lock")
    pin.lock_path.touch()
    attempt("gate-replacement", pin.check)
    pin = StoragePin(selected)
    pin.path.rename(root / "replaced-db")
    sqlite3.connect(pin.path).close()
    attempt("db-replacement", pin.check)
    pin = StoragePin(selected)
    selected.rename(root / "old-root")
    prepare(selected)
    attempt("root-replacement", pin.check)
    other = root / "other-root"
    prepare(other)
    alias_pin = StoragePin(alias)
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)
    attempt("root-symlink-retarget", alias_pin.check)
    return results


def observation_checks(groups):
    checks = {}
    loader = groups["loader"]
    rows = {r["id"]: r for r in loader["results"]}
    def check(name, value):
        checks[name] = bool(value)
    check("worker_not_coordinator", loader["ready"]["pid"] != os.getpid())
    for prefix in ("factory-A", "module-A", "local"):
        first, second = rows[prefix], rows[prefix + ("-again")]
        check(prefix + "-stable", first.get("success") and second.get("success")
              and all(first[k] == second[k] for k in
                      ("agent", "adapter", "owner", "model", "manager", "executor")))
    check("fresh-factory-isolation", rows["factory-A"]["agent"] != rows["factory-B"]["agent"])
    check("independent-adapter-engines", rows["factory-A"]["engine"] != rows["factory-B"]["engine"])
    check("session-history-continuity", "only-A-private-history-marker" in
          json.dumps(rows["factory-A-again"]["messages"]))
    check("session-history-isolation", "only-A-private-history-marker" not in
          json.dumps(rows["factory-B"]["messages"]))
    check("import-thread-owner-eligible", rows["import-thread-A"].get("success"))
    for op in ("module-B-reject", "singleton-B-reject", "parent-reject", "loop-reject",
               "import-thread-B-reject",
               *sorted(REJECTED_CONTRACTS)):
        check(op, "rejected" in rows[op])
    overlap = [rows[s] for s in ("stateless:invocation-1", "stateless:invocation-2")]
    check("independent-overlap", all(r.get("success") for r in overlap)
          and len({r["agent"] for r in overlap}) == len({r["owner"] for r in overlap}) == 2)
    constructed = [e for e in loader["events"] if e["kind"] == "constructed"]
    imported = [e for e in loader["events"] if e["kind"] == "module-import"]
    check("actual-worker-imports-construction", bool(imported) and bool(constructed)
          and all(e["pid"] == loader["ready"]["pid"] for e in imported + constructed))
    check("worker-local-resources-used", len([e for e in loader["events"]
                                               if e["kind"] == "resource-use"]) == 2)
    for name, data in groups.items():
        if name in {"loader", "aliases"}:
            continue
        events = data["events"]
        errors = [e for e in events if e["kind"] == "db-error"]
        result = next(r for r in data["results"] if r.get("id") == "extension")
        fault = "fault" in name
        check(name + ":result", result.get("success") == (not fault))
        check(name + ":gated-access", all(e["gate"] for e in events
                                               if e["kind"] in {"db-attempt", "extension-enter"}))
        check(name + ":drained-before-terminal",
              next(e["seq"] for e in events if e["kind"] == "drained")
              < next(e["seq"] for e in events if e["kind"] == "terminal"))
        check(name + ":no-fallback", [n for n in data["files_before_close"]
                                        if n.endswith(".sqlite3")] == [DB_NAME])
        if fault:
            check(name + ":accounted", bool(errors) and any(e["kind"] == "failure-latch" for e in events)
                  and any(r.get("id") == "late" and "rejected" in r for r in data["results"]))
            check(name + ":no-failed-write-effect", all(row[0] != "trial:extension"
                                                        for row in data["memory_rows"]))
        else:
            changed = [row for row in data["memory_rows"] if row[0] == "trial:extension"]
            seeded = [tuple(row) for row in data["memory_rows"] if row[0] != "trial:extension"]
            check(name + ":scoped-effects", bool(changed)
                  and sorted(seeded) == [("A", '"seed"'), ("foreign", '"seed"'),
                                         ("trial:A", '"seed"')])
        if name.startswith("background"):
            check(name + ":external-gate-denied", data["external_gate_denied_while_background"])
            check(name + ":background-before-terminal",
                  next(e["seq"] for e in events if e["kind"] == "background-done")
                  < next(e["seq"] for e in events if e["kind"] == "terminal"))
    for name, data in groups["aliases"].items():
        expected = name in {"relative-canonical", "root-symlink"}
        check("alias:" + name, data["accepted"] == expected
              and (not expected or data["value"] is True))
    for name, data in groups.items():
        if name != "aliases":
            check(name + ":clean-exit", data["exitcode"] == 0 and data["pending"] == 0
                  and data["gate_released"])
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("report must not exist")
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-eligibility-", dir=args.scratch_parent))
    fixtures = root / "modules"
    fixtures.mkdir()
    for form in sorted(FORMS):
        (fixtures / (form + ".py")).write_text(FIXTURE_SOURCE.replace("__FORM__", repr(form)))
    parent_module = fixtures / "prototype_parent_state.py"
    parent_module.write_text("VALUE = None\n")
    parent_state = load_module("prototype_parent_state", parent_module)
    # Actual parent live resources, deliberately never sent through IPC.
    parent_state.VALUE = {"pid": os.getpid(), "connection": sqlite3.connect(":memory:"),
                          "lock": threading.Lock()}
    groups = {"loader": group_loader(root / "loader", fixtures)}
    for form in ("hooks", "tool", "background"):
        groups[form] = group_extension(root / form, fixtures, form)
        for mode in ("raise", "missing"):
            name = form + "-fault-" + mode
            groups[name] = group_extension(root / name, fixtures, form,
                                           "upsert_user_memory:" + mode)
    groups["aliases"] = alias_groups(root / "aliases")
    parent_state.VALUE["connection"].close()
    checks = observation_checks(groups)
    provenance = {"python": sys.version, "platform": platform.platform(),
                  "sqlite": sqlite3.sqlite_version, "start_method": "spawn",
                  "packages": {n: importlib.metadata.version(n) for n in ("agno", "sqlalchemy")},
                  "coordinator_pid": os.getpid(), "scratch_root": str(root),
                  "probed_entrypoint_forms": sorted(FORMS - REJECTED_CONTRACTS),
                  "declared_rejected_forms": sorted(REJECTED_CONTRACTS)}
    for name, data in groups.items():
        if name == "aliases":
            continue
        with sqlite3.connect(root / name / DB_NAME) as connection:
            data["inspection_pragmas"] = {
                p: connection.execute("PRAGMA " + p).fetchall()
                for p in ("journal_mode", "synchronous", "integrity_check", "compile_options")}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as stream:
        json.dump({"provenance": provenance, "groups": groups, "checks": checks}, stream,
                  indent=2, sort_keys=True)
        stream.write("\n")
    failed_checks = [name for name, ok in checks.items() if not ok]
    print(json.dumps({"groups": len(groups), "checks": len(checks),
                      "failed": failed_checks, "report": str(args.report)}))
    return bool(failed_checks)


if __name__ == "__main__":
    raise SystemExit(main())
