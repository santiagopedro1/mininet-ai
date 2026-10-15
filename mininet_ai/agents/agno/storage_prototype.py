"""THROWAWAY: offline Agno storage experiments, not production code.

Run with the isolated interpreter documented in docs/prototypes/agno-storage.md.
Every invocation creates a new PROTOTYPE-wipe-me-* directory; no existing store
is opened. Model responses are scripted; Agno execution and SQLite are real.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import hashlib
import importlib.metadata
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import select
import signal
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

    def upsert_run(self, *args, **kwargs):
        try:
            return super().upsert_run(*args, **kwargs)
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


def supervised_worker(root, fact, mode, channel, entered, release):
    """Worker owns the gate so losing its supervisor cannot unlock it early.

    This experiment permits threads, not descendant processes or remote calls.
    The supervisor must still observe process exit, not trust a completion IPC.
    """
    root = Path(root)
    agent = None
    db = None
    if mode == "ignore-term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        with gate(root, timeout=2):
            try:
                channel.send({"phase": "gate_acquired"})
                db = database(root, ObservedDb)
                if mode == "hard-crash":
                    os._exit(23)
                blocked = mode in ("drain", "stuck", "ignore-term", "stall")
                learning_model = BlockedLearningModel(entered, release) if blocked else ScriptedModel(fact)
                model = FailingForegroundModel(entered) if blocked and mode != "stall" else ScriptedModel()
                agentic = mode.startswith("agentic")
                if agentic:
                    model = AgenticModel()
                agent = Agent(id="agent", model=model, db=db, retries=0, telemetry=False,
                              memory_manager=MemoryManager(db=db, model=learning_model),
                              update_memory_on_run=not agentic, enable_agentic_memory=agentic)
                db.inject_memory_error = mode in ("memory-failure", "agentic-memory-failure")
                db.inject_session_error = mode == "session-failure"
                response = agent.run(fact, session_id=f"{fact}:agent", user_id="agent")
                channel.send({"phase": "foreground_return", "agno_status": str(response.status)})
                # A foreground return is explicitly NOT the completion boundary.
                agent.background_executor.shutdown(wait=True)
                channel.send({"phase": "drained", "agno_status": str(response.status),
                              "db_errors": list(db.errors),
                              "affected_run_status": "success" if str(response.status) == "RunStatus.completed" and not db.errors else "failed"})
            finally:
                # Even exceptions must drain while STILL holding the gate. This
                # may block; terminate/kill and join are the supervisor boundary.
                if agent is not None:
                    agent.background_executor.shutdown(wait=True)
                if db is not None:
                    db.close()
    except Exception as error:
        channel.send({"phase": "worker_error", "error": repr(error)})
    finally:
        channel.close()


def supervise(root, fact, mode="healthy"):
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    entered, release = ctx.Event(), ctx.Event()
    worker = ctx.Process(target=supervised_worker,
                         args=(str(root), fact, mode, sender, entered, release))
    result = {"mode": mode, "events": [], "forced_stop": False,
              "budgets": {"operation_seconds": 4, "error_drain_seconds": 0.4,
                          "terminate_seconds": 0.4, "kill_seconds": 0.4}}
    start = time.monotonic()
    worker.start()
    sender.close()
    deadline = start + 4
    probed = False
    try:
        while worker.is_alive() and time.monotonic() < deadline:
            if entered.is_set() and not probed:
                probed = True
                try:
                    with gate(root, timeout=0.06):
                        result["gate_held_while_learning_blocked"] = False
                except TimeoutError:
                    result["gate_held_while_learning_blocked"] = True
                if mode == "stall":
                    # Deterministic stalled operation; shorten its remaining
                    # budget only after background learning has actually begun.
                    deadline = min(deadline, time.monotonic() + 0.4)
            if receiver.poll(0.01):
                try:
                    event = receiver.recv()
                except EOFError:
                    break
                event["at_seconds"] = time.monotonic() - start
                result["events"].append(event)
                if event["phase"] == "foreground_return" and event["agno_status"] != "RunStatus.completed":
                    deadline = min(deadline, time.monotonic() + 0.4)
                    if mode == "drain":
                        release.set()
        worker.join(timeout=max(0, deadline - time.monotonic()))
        if worker.is_alive():
            result["forced_stop"] = True
            worker.terminate()
            worker.join(timeout=0.4)
        if worker.is_alive():
            result["kill_required"] = True
            worker.kill()
            worker.join(timeout=0.4)
        result["local_contained"] = not worker.is_alive() and worker.exitcode is not None
        result["exitcode"] = worker.exitcode
        result["seconds_to_containment_check"] = time.monotonic() - start
        # Receive buffered final records only after join. EOF is not completion.
        while receiver.poll():
            try:
                result["events"].append(receiver.recv())
            except EOFError:
                break
        final = next((event for event in reversed(result["events"]) if event["phase"] == "drained"), None)
        result["affected_run_status"] = (
            "success" if result["local_contained"] and worker.exitcode == 0
            and not result["forced_stop"] and final is not None
            and not any(event["phase"] == "worker_error" for event in result["events"])
            and final["affected_run_status"] == "success" else "failed")
        result["admit_next_local_operation"] = result["local_contained"]
        if not result["local_contained"]:
            # Leave no prototype worker abandoned, but never claim a bounded
            # containment success if the bounded kill/join above did not prove it.
            raise RuntimeError("prototype local containment unproven; no next operation authorized")
        return result
    finally:
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=1)
        receiver.close()


def supervised_operations(root):
    root.mkdir()
    result = {}
    for mode in ("healthy", "agentic-healthy", "memory-failure", "agentic-memory-failure",
                 "session-failure", "drain", "stuck", "ignore-term", "stall", "hard-crash",
                 "sqlite-contention"):
        selected = root / mode
        db = database(selected)
        db.upsert_user_memory(UserMemory(memory_id="shared", user_id="agent", memory="seed"))
        # Initialize the session/run schemas before holding the adversarial
        # writer transaction, so this probes writes, not concurrent schema DDL.
        bootstrap = Agent(id="agent", model=ScriptedModel(), db=db, telemetry=False)
        bootstrap.run("bootstrap", session_id="bootstrap:agent", user_id="agent")
        bootstrap.background_executor.shutdown(wait=True)
        db.close()
        blocker = None
        try:
            if mode == "sqlite-contention":
                blocker = sqlite3.connect(selected / "PROTOTYPE-wipe-me.sqlite3")
                blocker.execute("BEGIN IMMEDIATE")
            observed = supervise(selected, f"{mode}-fact", mode)
        finally:
            if blocker is not None:
                blocker.rollback()
                blocker.close()
        # Admission happens only after the supervisor has confirmed local exit.
        # The former blocked thread is in that dead process, even if release is
        # never signalled; no finite sleep is offered as proof of quiescence.
        with gate(selected):
            db = database(selected)
            observed["memories_after_containment"] = memories(db)
            learn(db, "next-operation-fact")
            observed["memories_after_next_operation"] = memories(db)
            with db.db_engine.connect() as connection:
                observed["integrity_check"] = connection.exec_driver_sql("PRAGMA integrity_check").scalar()
            db.close()
        observed["files_after_reopen_close"] = sorted(p.name for p in selected.iterdir())
        result[mode] = observed
    # Two simultaneous independent supervisors; each uses a spawned Agent
    # process. Seed one file once, then automatic/agentic full operations update
    # the same learned-memory row under their worker-owned gate.
    for mode in ("healthy", "agentic-healthy"):
        selected = root / f"concurrent-{mode}"
        db = database(selected)
        db.upsert_user_memory(UserMemory(memory_id="shared", user_id="agent", memory="seed"))
        db.close()
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(supervise, selected, fact, mode) for fact in ("fact-A", "fact-B")]
            operations = [future.result(timeout=15) for future in futures]
        db = database(selected)
        final = memories(db)
        db.close()
        content = " ".join(m["memory"] for m in final)
        result[f"concurrent-{mode}"] = {
            "operations": operations, "final": final,
            "both_facts_retained": all(fact in content for fact in ("fact-A", "fact-B"))}
    return result


def cached_session_worker(root, channel):
    """Long-lived per-run process; one stable Agent per explicit session."""
    os.chdir(root)
    agents = {}
    try:
        while True:
            command = channel.recv()
            if command["op"] == "stop":
                break
            session = command["session"]
            if session not in agents:
                agents[session] = Agent(id=session.split(":")[-1], model=ScriptedModel(),
                                        cache_session=True, add_history_to_context=True,
                                        telemetry=False)
            agent = agents[session]
            if command["op"] == "state":
                agent.update_session_state(command["state"], session_id=session)
            else:
                agent.run(command["text"], session_id=session, user_id=session)
            channel.send({"session": session, "state": agent.get_session_state(session_id=session),
                          "model_messages": agent.model.seen, "live_sessions": sorted(agents)})
    finally:
        for agent in agents.values():
            agent.background_executor.shutdown(wait=True)
        channel.close()


def long_lived_zero_db(root):
    root.mkdir()
    ctx = mp.get_context("spawn")
    result = {}
    for lifecycle in ("original", "restarted"):
        controller, endpoint = ctx.Pipe()
        worker = ctx.Process(target=cached_session_worker, args=(str(root), endpoint))
        worker.start()
        endpoint.close()
        def request(command):
            controller.send(command)
            if not controller.poll(4):
                raise TimeoutError("prototype cached-session reply budget exceeded")
            return controller.recv()
        try:
            if lifecycle == "original":
                result["first"] = request({"op": "run", "session": "trial:agent-A", "text": "unique agent A history"})
                result["state_update"] = request({"op": "state", "session": "trial:agent-A", "state": {"action_result": "installed"}})
                result["second"] = request({"op": "run", "session": "trial:agent-A", "text": "continue A"})
                result["other_agent"] = request({"op": "run", "session": "trial:agent-B", "text": "agent B isolated"})
                result["back_to_A"] = request({"op": "run", "session": "trial:agent-A", "text": "continue A again"})
                result["fresh_trial"] = request({"op": "run", "session": "next-trial:agent-A", "text": "fresh trial A"})
            else:
                result["after_restart"] = request({"op": "run", "session": "trial:agent-A", "text": "after worker restart"})
            controller.send({"op": "stop"})
            worker.join(timeout=2)
            result[f"{lifecycle}_exitcode"] = worker.exitcode
            if worker.is_alive():
                raise TimeoutError("cached worker did not exit in prototype budget")
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
            controller.close()
    result["files_created"] = sorted(p.name for p in root.iterdir())
    result["second_retained_history"] = "unique agent A history" in json.dumps(result["second"]["model_messages"])
    result["back_to_A_retained_state"] = result["back_to_A"]["state"].get("action_result") == "installed"
    result["other_agent_isolated"] = "action_result" not in result["other_agent"]["state"] and "unique agent A history" not in json.dumps(result["other_agent"]["model_messages"])
    result["fresh_trial_isolated"] = "action_result" not in result["fresh_trial"]["state"] and "unique agent A history" not in json.dumps(result["fresh_trial"]["model_messages"])
    result["restart_loses_cache"] = "action_result" not in result["after_restart"]["state"] and "unique agent A history" not in json.dumps(result["after_restart"]["model_messages"])
    return result


def linux_prctl(option, argument):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(ctypes.c_int(option), argument, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prototype prctl failed")


class IndefinitelyBlockedLearningModel(BlockedLearningModel):
    def invoke(self, *args, **kwargs):
        self.entered.set()
        self.release.wait()  # Only this experiment's controller can kill it.
        return super().invoke(*args, **kwargs)


def orphan_worker(root, expected_parent, armed, channel):
    if armed:
        linux_prctl(1, int(signal.SIGKILL))  # PR_SET_PDEATHSIG (Linux only).
        # Close the parent-exited-before-prctl race; no DB is opened first.
        if os.getppid() != expected_parent:
            os._exit(72)
    db = None
    agent = None
    with gate(Path(root), timeout=2):
        try:
            db = database(Path(root))
            entered, release = threading.Event(), threading.Event()
            agent = Agent(id="agent", model=FailingForegroundModel(entered), db=db,
                          memory_manager=MemoryManager(db=db, model=IndefinitelyBlockedLearningModel(entered, release)),
                          update_memory_on_run=True, retries=0, telemetry=False)
            response = agent.run("orphan learning", session_id="orphan:agent", user_id="agent")
            channel.send({"phase": "blocked_learning", "armed_parent_death_signal": armed,
                          "pid": os.getpid(), "parent_pid": os.getppid(),
                          "agno_status": str(response.status)})
        finally:
            # Deliberately cannot complete. Controller cleanup and the candidate
            # kernel parent-death signal must stop this process, not release it.
            if agent is not None:
                agent.background_executor.shutdown(wait=True)
            if db is not None:
                db.close()


def doomed_supervisor(root, armed, controller):
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    worker = ctx.Process(target=orphan_worker, args=(root, os.getpid(), armed, sender))
    worker.start()
    sender.close()
    if not receiver.poll(6):
        worker.kill()
        worker.join(timeout=1)
        raise TimeoutError("prototype orphan worker did not become ready")
    controller.send(receiver.recv())
    command = controller.recv()
    if command == "exit":
        os._exit(0)  # Test normal supervisor exit as well as external SIGKILL.


def supervisor_death(root):
    """Linux-only controlled orphan experiment; reap only our known child PID."""
    if sys.platform != "linux" or not hasattr(os, "pidfd_open"):
        raise RuntimeError("supervisor-death experiment requires Linux pidfds/prctl")
    root.mkdir()
    previous = ctypes.c_int()
    linux_prctl(37, ctypes.byref(previous))  # PR_GET_CHILD_SUBREAPER.
    linux_prctl(36, 1)  # Adopt/reap our orphaned scratch workers, not PID 1.
    result = {}
    try:
        for mode, armed, normal_exit in (("baseline-no-watchdog", False, False),
                                         ("parent-killed", True, False),
                                         ("parent-exits", True, True)):
            selected = root / mode
            db = database(selected)
            db.upsert_user_memory(UserMemory(memory_id="shared", user_id="agent", memory="seed"))
            db.close()
            ctx = mp.get_context("spawn")
            controller, endpoint = ctx.Pipe()
            supervisor = ctx.Process(target=doomed_supervisor, args=(str(selected), armed, endpoint))
            supervisor.start()
            endpoint.close()
            pidfd = None
            pid = None
            record = {}
            try:
                if not controller.poll(8):
                    raise TimeoutError("prototype supervisor readiness budget exceeded")
                record["ready"] = controller.recv()
                pid = record["ready"]["pid"]
                pidfd = os.pidfd_open(pid)
                try:
                    with gate(selected, timeout=0.06):
                        record["gate_held_before_supervisor_death"] = False
                except TimeoutError:
                    record["gate_held_before_supervisor_death"] = True
                start = time.monotonic()
                if normal_exit:
                    controller.send("exit")
                else:
                    supervisor.kill()
                supervisor.join(timeout=1)
                record["supervisor_exitcode"] = supervisor.exitcode
                record["worker_exited_without_controller_kill"] = bool(select.select([pidfd], [], [], 0.5)[0])
                record["seconds_after_supervisor_stop"] = time.monotonic() - start
                if not record["worker_exited_without_controller_kill"]:
                    try:
                        with gate(selected, timeout=0.06):
                            record["orphan_still_holds_gate"] = False
                    except TimeoutError:
                        record["orphan_still_holds_gate"] = True
            finally:
                if supervisor.is_alive():
                    supervisor.kill()
                    supervisor.join(timeout=1)
                if pidfd is not None:
                    if not select.select([pidfd], [], [], 0)[0]:
                        signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                    if not select.select([pidfd], [], [], 1)[0]:
                        raise RuntimeError("orphan local containment unproven; deny next operation")
                    # The controlled supervisor is gone; this interpreter is the
                    # subreaper. This wait targets only the PID learned above.
                    waited_pid, status = os.waitpid(pid, 0)
                    record["reaped_pid"] = waited_pid
                    record["worker_exitcode"] = os.waitstatus_to_exitcode(status)
                    os.close(pidfd)
                controller.close()
            with gate(selected):
                db = database(selected)
                record["memories_after_worker_exit"] = memories(db)
                learn(db, "after-supervisor-death")
                record["next_operation_memories"] = memories(db)
                db.close()
            result[mode] = record
    finally:
        linux_prctl(36, previous.value)
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
                 ("late_background", late_background), ("gate_contention", gate_contention),
                 ("supervised_operations", supervised_operations),
                 ("long_lived_zero_db", long_lived_zero_db),
                 ("supervisor_death", supervisor_death)]
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
