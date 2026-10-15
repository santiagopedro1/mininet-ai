"""THROWAWAY: offline Agno storage experiments, not production code.

Run with the isolated interpreter documented in docs/prototypes/agno-storage.md.
Every invocation creates a new PROTOTYPE-wipe-me-* directory; no existing store
is opened. Model responses are scripted; Agno execution and SQLite are real.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from typing import Any

from agno.agent import Agent
from agno.db.schemas.memory import UserMemory
from agno.db.sqlite import SqliteDb
from agno.memory.manager import MemoryManager
from agno.models.base import Model
from agno.models.response import ModelResponse
from sqlalchemy import create_engine


class ScriptedModel(Model):
    """Script a real Agno tool call from the snapshot supplied to its model."""

    def __init__(self, fact="", barrier=None):
        super().__init__(id="PROTOTYPE-offline", provider="scripted")
        self.fact = fact
        self.barrier = barrier
        self.seen = []

    def __deepcopy__(self, memo):
        # Each worker owns its own model. Preserve its synchronization primitive
        # across MemoryManager's model copy; never use this for a real Provider.
        return self

    def invoke(self, *args, **kwargs):
        messages = kwargs["messages"]
        self.seen = [m.to_dict() for m in messages]
        if not self.fact or messages[-1].role == "tool":
            return ModelResponse(content="offline response")
        prompt = "\n".join(str(m.content) for m in messages if m.role == "system")
        matches = re.findall(r"ID: ([^\n]+)\nMemory: ([^\n]+)", prompt)
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        name = "update_memory" if matches else "add_memory"
        arguments = {"memory": (matches[0][1] + " | " if matches else "") + self.fact}
        if matches:
            arguments["memory_id"] = matches[0][0]
        return ModelResponse(tool_calls=[{
            "id": "offline-call", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }])

    async def ainvoke(self, *args, **kwargs):
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args, **kwargs):
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args, **kwargs):
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response, **kwargs):
        return response

    def _parse_provider_response_delta(self, response):
        return response


class AgenticModel(ScriptedModel):
    def invoke(self, *args, **kwargs):
        if kwargs["messages"][-1].role == "tool":
            return ModelResponse(content="offline agentic response")
        return ModelResponse(tool_calls=[{
            "id": "agentic-call", "type": "function",
            "function": {"name": "update_user_memory",
                         "arguments": json.dumps({"task": "remember offline fact"})},
        }])


class BlockedLearningModel(ScriptedModel):
    def __init__(self, entered, release):
        super().__init__("late-background-fact")
        self.entered = entered
        self.release = release

    def invoke(self, *args, **kwargs):
        if kwargs["messages"][-1].role != "tool":
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise TimeoutError("prototype background release safety limit")
        return super().invoke(*args, **kwargs)


class FailingForegroundModel(ScriptedModel):
    def __init__(self, entered):
        super().__init__()
        self.entered = entered

    def invoke(self, *args, **kwargs):
        if not self.entered.wait(timeout=3):
            raise TimeoutError("prototype background did not start")
        raise RuntimeError("PROTOTYPE foreground failure while learning is active")


def database(root, cls=SqliteDb):
    root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{root / 'PROTOTYPE-wipe-me.sqlite3'}",
                           connect_args={"timeout": 0.2})
    return cls(db_engine=engine)


@contextmanager
def gate(root, timeout=0.4):
    """Candidate root-wide full-operation gate, NOT a SQLite transaction."""
    with (root / "PROTOTYPE-wipe-me.learning.lock").open("a") as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("prototype learning gate budget exceeded")
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def memories(db, user="agent"):
    return [m.to_dict() for m in db.get_user_memories(user_id=user)]


def learn(db, fact, user="agent", barrier=None, automatic=False):
    model = ScriptedModel(fact, barrier)
    manager = MemoryManager(db=db, model=model)
    if automatic:
        response = manager.create_user_memories(message=fact, user_id=user)
    else:
        response = manager.update_memory_task(fact, user_id=user)
    return {"response": response, "model_messages": model.seen,
            "memories": memories(db, user)}


def race_worker(root, fact, guarded, barrier, queue):
    db = database(Path(root))  # Spawned process owns its engine/connections.
    try:
        if guarded:
            with gate(Path(root), timeout=10):
                result = learn(db, fact)
        else:
            result = learn(db, fact, barrier=barrier)
        queue.put({"fact": fact, "result": result})
    except Exception as error:
        queue.put({"fact": fact, "error": repr(error)})
    finally:
        db.close()


def race(root, guarded):
    db = database(root)
    db.upsert_user_memory(UserMemory(memory_id="shared", user_id="agent", memory="seed"))
    db.close()
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    barrier = ctx.Barrier(2)
    workers = [ctx.Process(target=race_worker,
                          args=(str(root), fact, guarded, barrier, queue))
               for fact in ("fact-A", "fact-B")]
    for worker in workers:
        worker.start()
    results = []
    try:
        for _ in workers:
            results.append(queue.get(timeout=20))
    finally:
        for worker in workers:
            worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
        queue.close()
    db = database(root)
    final = memories(db)
    db.close()
    content = " ".join(m["memory"] for m in final)
    return {"workers": results, "exitcodes": [w.exitcode for w in workers],
            "final": final, "both_facts_retained": all(f in content for f in ("fact-A", "fact-B"))}


def continuity(root):
    root.mkdir()
    previous = Path.cwd()
    os.chdir(root)
    try:
        model = ScriptedModel()
        agent = Agent(id="agent", model=model, cache_session=True,
                      add_history_to_context=True, telemetry=False)
        agent.run("first unique message", session_id="run-A:agent", user_id="run-A:agent")
        agent.update_session_state({"action_result": "installed"}, session_id="run-A:agent")
        agent.run("second", session_id="run-A:agent", user_id="run-A:agent")
        history_seen = "first unique message" in json.dumps(model.seen)
        state = agent.get_session_state(session_id="run-A:agent")
        agent.run("fresh trial", session_id="run-B:agent", user_id="run-B:agent")
        fresh = agent.get_session_state(session_id="run-B:agent")
        files = [p.name for p in root.iterdir()]
        return {"history_seen": history_seen, "retained_state": state,
                "fresh_session_state": fresh, "files_created": files,
                "boundary": "One cached session on one live Agent; no restart persistence claimed."}
    finally:
        os.chdir(previous)


def persistent(root):
    db = database(root)
    first = Agent(id="agent", model=ScriptedModel(), db=db,
                  add_history_to_context=True, telemetry=False)
    first.run("durable unique message", session_id="run-A:agent", user_id="agent")
    first.update_session_state({"action_result": "installed"}, session_id="run-A:agent")
    db.close()
    db = database(root)
    model = ScriptedModel()
    restored = Agent(id="agent", model=model, db=db, add_history_to_context=True, telemetry=False)
    restored.run("after reconstruction", session_id="run-A:agent", user_id="agent")
    result = {"history_restored": "durable unique message" in json.dumps(model.seen),
              "state_restored": restored.get_session_state(session_id="run-A:agent")}
    learn(db, "run-A-only", user="run-A:agent", automatic=True)
    result["run_B_memories_before_learning"] = memories(db, "run-B:agent")
    learn(db, "shared-agent-fact", user="agent", automatic=True)
    db.close()
    db = database(root)
    result["shared_memory_after_reopen"] = memories(db, "agent")
    result["run_A_memory"] = memories(db, "run-A:agent")
    result["distinct_agent_memory"] = memories(db, "other-agent")
    result["live_files"] = sorted(p.name for p in root.iterdir())
    with db.db_engine.connect() as connection:
        result["pragmas"] = {name: connection.exec_driver_sql(f"PRAGMA {name}").fetchall()
                             for name in ("journal_mode", "synchronous", "busy_timeout", "foreign_keys")}
        result["tables"] = [r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")]
    db.close()
    result["after_close_files"] = sorted(p.name for p in root.iterdir())
    return result


class ObservedDb(SqliteDb):
    """Prototype-only error journal catches DB errors swallowed by Agno."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.errors = []
        self.inject_session_error = False
        self.inject_memory_error = False

    def upsert_session(self, *args, **kwargs):
        try:
            if self.inject_session_error:
                raise OSError("PROTOTYPE injected session write failure")
            result = super().upsert_session(*args, **kwargs)
            if result is None:
                raise RuntimeError("session upsert returned no row")
            return result
        except Exception as error:
            self.errors.append(repr(error))
            raise

    def upsert_user_memory(self, *args, **kwargs):
        try:
            if self.inject_memory_error:
                raise OSError("PROTOTYPE injected memory write failure")
            result = super().upsert_user_memory(*args, **kwargs)
            if result is None:
                raise RuntimeError("memory upsert returned no row")
            return result
        except Exception as error:
            self.errors.append(repr(error))
            raise


def failures(root):
    db = database(root, ObservedDb)
    agent = Agent(id="agent", model=ScriptedModel(), db=db, telemetry=False)
    agent.run("initialize", session_id="run:agent", user_id="agent")
    learn(db, "seed")
    db.inject_session_error = True
    returned = agent.run("write must fail", session_id="run:agent", user_id="agent")
    result = {"session_failure": {"agent_returned_content": returned.content,
              "db_errors": list(db.errors), "prototype_affected_run_status": "failed" if db.errors else "success"}}
    db.inject_session_error = False
    db.errors.clear()
    db.inject_memory_error = True
    result["memory_failure"] = learn(db, "must-not-be-saved")
    result["memory_failure"]["db_errors"] = list(db.errors)
    result["memory_failure"]["prototype_affected_run_status"] = "failed" if db.errors else "success"
    db.inject_memory_error = False
    db.errors.clear()
    connection = sqlite3.connect(root / "PROTOTYPE-wipe-me.sqlite3")
    connection.execute("BEGIN IMMEDIATE")
    start = time.monotonic()
    try:
        result["real_writer_contention"] = learn(db, "blocked-write")
        result["real_writer_contention"]["seconds"] = time.monotonic() - start
        result["real_writer_contention"]["db_errors"] = list(db.errors)
        result["real_writer_contention"]["prototype_affected_run_status"] = "failed" if db.errors else "success"
    finally:
        connection.rollback()
        connection.close()
        db.close()
    return result


def configured_learning(root):
    db = database(root, ObservedDb)
    result = {}
    for mode in ("automatic", "agentic"):
        for broken in (False, True):
            user = f"{mode}-{'broken' if broken else 'healthy'}"
            db.errors.clear()
            db.inject_memory_error = broken
            manager = MemoryManager(db=db, model=ScriptedModel("configured-fact"))
            agent = Agent(id="agent", model=AgenticModel() if mode == "agentic" else ScriptedModel(),
                          db=db, memory_manager=manager, update_memory_on_run=mode == "automatic",
                          enable_agentic_memory=mode == "agentic", telemetry=False)
            response = agent.run("remember offline fact", session_id=f"{user}:agent", user_id=user)
            agent.background_executor.shutdown(wait=True)
            result[user] = {"agent_content": response.content, "status": str(response.status),
                            "memories": memories(db, user), "db_errors": list(db.errors),
                            "prototype_affected_run_status": "failed" if db.errors else "success"}
    db.close()
    return result


def late_background(root):
    db = database(root)
    # Create tables before deliberately overlapping read and failure paths.
    db.get_user_memories(user_id="agent")
    entered, release = threading.Event(), threading.Event()
    agent = Agent(id="agent", model=FailingForegroundModel(entered), db=db,
                  memory_manager=MemoryManager(db=db, model=BlockedLearningModel(entered, release)),
                  update_memory_on_run=True, retries=0, telemetry=False)
    start = time.monotonic()
    result = {}
    try:
        with gate(root):
            try:
                response = agent.run("start learning", session_id="run:agent", user_id="agent")
                result["returned"] = str(response.status)
            except Exception as error:
                result["foreground_error"] = repr(error)
            result["foreground_return_seconds"] = time.monotonic() - start
            result["background_started"] = entered.is_set()
            result["memories_at_return"] = memories(db)
        # Deliberately show why release-at-return is unsafe. Only scratch data.
        with gate(root):
            result["next_operation_can_acquire_gate"] = True
            release.set()
            agent.background_executor.shutdown(wait=True)
            result["memories_after_old_worker_drained"] = memories(db)
    finally:
        release.set()
        agent.background_executor.shutdown(wait=True)
        db.close()
    return result


def gate_waiter(root, queue):
    start = time.monotonic()
    try:
        with gate(Path(root)):
            queue.put({"unexpectedly_acquired": True})
    except TimeoutError as error:
        queue.put({"error": str(error), "seconds": time.monotonic() - start})


def gate_contention(root):
    root.mkdir()
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    with gate(root):
        worker = ctx.Process(target=gate_waiter, args=(str(root), queue))
        worker.start()
        try:
            result = queue.get(timeout=10)
        finally:
            worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
    queue.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, help="New file for shareable observations; never overwrite")
    args = parser.parse_args()
    if args.report is not None and args.report.exists():
        parser.error("--report already exists; choose a new path")
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-", dir=args.scratch_parent)).resolve()
    result: dict[str, Any] = {
        "question": "Can offline Agno retain scoped state safely using zero or one database per root?",
        "scratch_root": str(root), "python": sys.version, "sqlite": sqlite3.sqlite_version,
        "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": ["No remote Provider or network requests", "No production code changed",
                   "Separate scenario directories represent separate selected storage roots",
                   "ObservedDb is a prototype journal, not a complete strict-persistence adapter",
                   "No crash-atomic learning, cancellation, or remote-containment guarantee"],
    }
    scenarios = [("zero_database", continuity), ("persistent", persistent),
                 ("unguarded_race", lambda p: race(p, False)),
                 ("guarded_race", lambda p: race(p, True)),
                 ("failures", failures), ("configured_learning", configured_learning),
                 ("late_background", late_background), ("gate_contention", gate_contention)]
    for name, experiment in scenarios:
        print(f"Running {name}", flush=True)
        try:
            result[name] = experiment(root / name)
        except Exception as error:
            import traceback
            result[name] = {"experiment_error": repr(error), "traceback": traceback.format_exc()}
        (root / "observations.json").write_text(json.dumps(result, indent=2, default=list) + "\n")
    print(f"Observations: {root / 'observations.json'}")
    if args.report is not None:
        with args.report.open("x") as output:
            output.write(json.dumps(result, indent=2, default=list) + "\n")
    if any(isinstance(v, dict) and "experiment_error" in v for v in result.values()):
        raise SystemExit("An experiment could not run; inspect observations (not a validation verdict).")


if __name__ == "__main__":
    main()
