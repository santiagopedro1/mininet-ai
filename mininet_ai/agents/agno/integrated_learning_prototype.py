"""THROWAWAY offline mixed-route continuity probe; not production or acceptance."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import platform
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import cast

from agno.agent import Agent
from agno.db.schemas.memory import UserMemory
from agno.db.sqlite import SqliteDb
from agno.memory.manager import MemoryManager
from agno.models.response import ModelResponse
from agno.run import RunStatus

sys.path.append(str(Path(__file__).resolve().parent))
from integrated_storage_prototype import digest
from learning_gate_prototype import (
    OfflineOptimization,
    StrictDb,
    TrackedExecutor,
    command,
    event,
    probe,
)
from storage_prototype import ScriptedModel, database, gate, memories


class MixedModel(ScriptedModel):
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
                "id": "integrated-tool", "type": "function",
                "function": {"name": name, "arguments": '{"task":"offline tool fact"}'},
            }])
        return ModelResponse(content="offline mixed response")


class MixedMemoryModel(ScriptedModel):
    def __init__(self, blocked):
        super().__init__()
        self.hold = False
        self.release = threading.Event()
        self.blocked = blocked

    def invoke(self, *args, **kwargs):
        if self.hold and kwargs["messages"][-1].role != "tool":
            self.blocked()
            if not self.release.wait(4):
                raise TimeoutError("controller did not release offline learning fixture")
        return super().invoke(*args, **kwargs)


def run_route(agent, manager, command, session, user):
    route = command["route"]
    if route in {"reason", "automatic", "agentic", "hooks", "tool"}:
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
        return manager.add_user_memory(UserMemory(memory_id=command["memory_id"],
                                                 memory="direct mixed fact"), user_id=user)
    if route == "replace":
        return manager.replace_user_memory(command["memory_id"], UserMemory(memory="replaced mixed fact"), user_id=user)
    if route == "search":
        return manager.search_user_memories(user_id=user, retrieval_method="last_n")
    if route == "optimize":
        return manager.optimize_memories(user_id=user, strategy=OfflineOptimization())
    if route == "delete":
        return manager.delete_user_memory(command["memory_id"], user_id=user)
    if route == "clear-user":
        return manager.clear_user_memories(user_id=user)
    if route == "read":
        return manager.get_user_memory(command["memory_id"], user_id=user)
    raise ValueError("unsupported offline route: " + route)


def integrated_worker(root, channel, mode="automatic", scope="run"):
    root = Path(root)
    path = root / "PROTOTYPE-wipe-me.sqlite3"
    cached, owners, releases = {}, {}, {}
    send_lock = threading.Lock()
    barrier = threading.Barrier(2)
    memory_capable = mode != "session-only"

    def send(value):
        with send_lock:
            channel.send(value)

    def execute(command):
        identity, op = command["agent"], command["id"]
        session = "trial:" + identity
        user = session if scope == "run" else identity
        with gate(root, timeout=3) if memory_capable else nullcontext():
            def emit(kind, payload):
                event(path, op, kind, payload)

            if memory_capable:
                emit("gate-acquired", {})
            emit("admitted", {"command_hash": digest(command), "user": user})
            if identity not in cached:
                db = cast(StrictDb, database(root, StrictDb))
                current = {}

                def blocked():
                    db.emit("background-entered", {})
                    send({"background_entered": current["id"], "requires_release": True})

                manager = MemoryManager(db=db, model=MixedMemoryModel(blocked))

                def record_hook(stage):
                    db.emit("hook", {"stage": stage})
                    if current["route"] == "hooks":
                        manager.add_user_memory(UserMemory(memory=f"hook-{stage}-{current['id']}"), user_id=user)

                def pre_hook():
                    record_hook("pre")

                def post_hook():
                    record_hook("post")

                def remember_fixture(task: str) -> str:
                    db.emit("custom-tool", {"task": task})
                    return manager.update_memory_task(task, user_id=user)

                agent = Agent(id=identity, model=MixedModel(barrier), db=db,
                              memory_manager=manager if memory_capable else None,
                              update_memory_on_run=mode == "automatic", enable_agentic_memory=mode == "agentic",
                              add_memories_to_context=memory_capable,
                              add_history_to_context=True,
                              add_session_state_to_context=True,
                              num_history_runs=20,
                              tools=[remember_fixture] if memory_capable else None,
                              pre_hooks=[pre_hook] if memory_capable else None,
                              post_hooks=[post_hook] if memory_capable else None,
                              cache_session=True, telemetry=False)
                agent._background_executor = TrackedExecutor()
                cached[identity] = agent, manager, db, current
            agent, manager, db, current = cached[identity]
            current.update(command)
            db.bind(user, op, emit)
            manager.model.fact = "fact-" + op
            manager.model.hold = command.get("hold_learning", False)
            manager.model.release.clear()
            releases[op] = manager.model.release
            try:
                response = run_route(agent, manager, command, session, user)
            finally:
                owned_futures = len(agent.background_executor.owned)
                try:
                    agent.background_executor.drain()
                finally:
                    if agent.background_executor.owned:
                        send({"id": op, "success": False, "unquiesced": True})
                        # Retain the worker-owned gate for controller containment.
                        # This safety branch is not an item-1 validation result.
                        threading.Event().wait()
                emit("drained", {"pending": len(agent.background_executor.owned)})
            result = {"id": op, "hash": digest(command), "success": not db.errors,
                      "response": str(response), "agent_object": id(agent),
                      "manager_object": id(manager), "executor_object": id(agent.background_executor),
                      "adapter_object": id(db), "foreground_model_object": id(agent.model),
                      "memory_model_object": id(manager.model), "owned_futures": owned_futures,
                      "owner": threading.get_ident(), "model_messages": agent.model.seen,
                      "session": session, "user": user, "mode": mode,
                      "settings": {"automatic": agent.update_memory_on_run,
                                   "agentic": agent.enable_agentic_memory,
                                   "session_state_context": agent.add_session_state_to_context,
                                   "history_runs": agent.num_history_runs},
                      "session_state": agent.get_session_state(session_id=session),
                      "memories": memories(db, user) if memory_capable else []}
            db.active = False
            emit("terminal", result)
            send(result)

    send({"ready": True, "mode": mode, "scope": scope})
    try:
        while True:
            command = channel.recv()
            if "release_background" in command:
                releases[command["release_background"]].set()
                continue
            if command["route"] == "stop":
                break
            identity = command["agent"]
            if identity not in owners:
                owners[identity] = ThreadPoolExecutor(max_workers=1)
            owners[identity].submit(execute, command)
    finally:
        for owner in owners.values():
            owner.shutdown(wait=True)
        for agent, manager, db, current in cached.values():
            agent.background_executor.shutdown(wait=True)
            db.close()
        channel.close()


def mixed_commands(mode):
    commands = [[command("first", "reason", text="unique A history")]]
    for route in (mode, "create", "task", "add", "replace", "read", "search", "hooks", "tool"):
        commands.append([command(route, route, memory_id="mixed-direct", hold_learning=route == "automatic")])
    return commands + [[command("delete", "delete", memory_id="mixed-direct")],
                       [command("optimize", "optimize")],
                       [command("clear-user", "clear-user")],
                       [command("outcome", "outcome", state={"action_result": "installed"})],
                       [command("back", "reason", text="continue A")]]


def session_only_commands():
    return [[command("first-A", "reason", text="unique A history")],
            [command("overlap-A", "reason", text="overlap"),
             command("overlap-B", "reason", agent="B", text="overlap")],
            [command("outcome-A", "outcome", state={"action_result": "installed"})],
            [command("back-A", "reason", text="continue A")],
            [command("back-B", "reason", agent="B", text="continue B")]]


def check_groups(groups):
    checks = {}
    for name, group in groups.items():
        replies = {r["id"]: r for r in group["replies"] if "id" in r}
        checks[name + ":success"] = all(r.get("success") is True for r in replies.values())
        checks[name + ":complete"] = set(replies) == {c["id"] for batch in group["commands"] for c in batch}
        checks[name + ":exit"] = group["exitcode"] == 0
        checks[name + ":integrity"] = group["pragmas_inspection_connection"]["integrity_check"] == [("ok",)]
        for identity in {r["session"] for r in replies.values()}:
            subset = [r for r in replies.values() if r["session"] == identity]
            for field in ("agent_object", "manager_object", "executor_object", "adapter_object",
                          "foreground_model_object", "memory_model_object", "owner"):
                checks[f"{name}:{identity}:{field}"] = len({r[field] for r in subset}) == 1
            checks[f"{name}:{identity}:fixed-settings"] = len({json.dumps(r["settings"], sort_keys=True) for r in subset}) == 1
        checks[name + ":durable-completion"] = all(any(
            e["kind"] == "terminal" and e["id"] == r["id"] and e["payload"] == r
            for e in group["events"]
        ) for r in replies.values())
        checks[name + ":drain-before-terminal"] = all(any(
            d["kind"] == "drained" and d["id"] == e["id"] and d["seq"] < e["seq"] and d["payload"]["pending"] == 0
            for d in group["events"]
        ) for e in group["events"] if e["kind"] == "terminal")
        if name == "memory-free":
            checks[name + ":overlap"] = replies["overlap-A"]["success"] and replies["overlap-B"]["success"]
            checks[name + ":distinct-owners"] = replies["overlap-A"]["owner"] != replies["overlap-B"]["owner"]
            checks[name + ":history-isolation"] = "unique A history" not in json.dumps(replies["back-B"]["model_messages"])
            checks[name + ":A-continuity"] = "unique A history" in json.dumps(replies["back-A"]["model_messages"])
            checks[name + ":outcome-in-reasoning"] = all(
                text in json.dumps(replies["back-A"]["model_messages"]) for text in ("action_result", "installed"))
            checks[name + ":no-learning-gate"] = not any(e["kind"] == "gate-acquired" for e in group["events"])
            checks[name + ":no-memory-access"] = not any(
                "memor" in e["payload"].get("method", "") for e in group["events"])
            continue
        checks[name + ":history"] = "unique A history" in json.dumps(replies["back"]["model_messages"])
        checks[name + ":outcome"] = replies["back"]["session_state"] == {"action_result": "installed"}
        checks[name + ":outcome-in-reasoning"] = all(
            text in json.dumps(replies["back"]["model_messages"]) for text in ("action_result", "installed"))
        checks[name + ":direct-add"] = "direct mixed fact" in json.dumps(replies["add"]["memories"])
        checks[name + ":replace-read"] = "replaced mixed fact" in replies["read"]["response"]
        checks[name + ":search"] = "replaced mixed fact" in replies["search"]["response"]
        checks[name + ":manager-create"] = "fact-create" in json.dumps(replies["create"]["memories"])
        checks[name + ":manager-task"] = "fact-task" in json.dumps(replies["task"]["memories"])
        checks[name + ":hooks"] = all(f"hook-{stage}-hooks" in json.dumps(replies["hooks"]["memories"]) for stage in ("pre", "post"))
        checks[name + ":tool"] = any(e["id"] == "tool" and e["kind"] == "custom-tool" for e in group["events"])
        checks[name + ":delete"] = not any(m["memory_id"] == "mixed-direct" for m in replies["delete"]["memories"])
        checks[name + ":optimize"] = [m["memory"] for m in replies["optimize"]["memories"]] == ["optimized offline"]
        checks[name + ":clear"] = not replies["clear-user"]["memories"]
        checks[name + ":foreign-isolation"] = [m["memory"] for m in group["scopes"]["foreign"]] == ["seed"]
        other_scope = "trial:A" if name.endswith("agent-scope") else "A"
        checks[name + ":other-scope-isolation"] = [m["memory"] for m in group["scopes"][other_scope]] == ["seed"]
        if "automatic" in replies:
            checks[name + ":automatic-background"] = replies["automatic"]["owned_futures"] > 0
            checks[name + ":gate-during-background"] = group["background_gate"] == {
                "controller": "denied while background active", "thread": "denied while background active"}
            checks[name + ":automatic-write"] = "fact-automatic" in json.dumps(replies["automatic"]["memories"])
        else:
            checks[name + ":agentic-tool"] = "update_user_memory" in json.dumps(replies["agentic"]["model_messages"])
            checks[name + ":agentic-write"] = "fact-agentic" in json.dumps(replies["agentic"]["memories"])
    return checks


def provenance():
    agno_agent = Path(inspect.getfile(Agent)).parent
    sources = [Path(__file__), Path(__file__).with_name("learning_gate_prototype.py"),
               Path(__file__).with_name("storage_prototype.py"),
               Path(__file__).with_name("integrated_storage_prototype.py"),
               Path(inspect.getfile(MemoryManager)), Path(inspect.getfile(SqliteDb))]
    sources += [agno_agent / name for name in ("agent.py", "_managers.py", "_hooks.py", "_run.py", "_storage.py")]
    with sqlite3.connect(":memory:") as conn:
        source_id = conn.execute("SELECT sqlite_source_id()").fetchone()[0]
        compile_options = conn.execute("PRAGMA compile_options").fetchall()
    return {"python": sys.version, "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
            "sqlite_source_id": source_id, "sqlite_compile_options": compile_options,
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
            "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    args.scratch_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="PROTOTYPE-wipe-me-integrated-", dir=args.scratch_parent))
    report = {"verdict": "PARTIAL; live human review required", "scratch": str(root),
              "provenance": provenance(), "groups": {
        "mixed": probe(root / "mixed", mixed_commands("automatic"), worker_target=integrated_worker),
        "agentic": probe(root / "agentic", mixed_commands("agentic"), worker_target=integrated_worker,
                         worker_options=("agentic",)),
        "automatic-agent-scope": probe(root / "automatic-agent-scope", mixed_commands("automatic"),
                                       worker_target=integrated_worker, worker_options=("automatic", "agent")),
        "agentic-agent-scope": probe(root / "agentic-agent-scope", mixed_commands("agentic"),
                                     worker_target=integrated_worker, worker_options=("agentic", "agent")),
        "memory-free": probe(root / "memory-free", session_only_commands(),
                             worker_target=integrated_worker, worker_options=("session-only",)),
    }}
    report["checks"] = check_groups(report["groups"])
    args.report.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(args.report)
    return int(not all(report["checks"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
