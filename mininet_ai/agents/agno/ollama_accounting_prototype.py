"""THROWAWAY GH-98 — offline native Ollama accounting; NEVER MERGE.

Run: .venv/bin/python -P mininet_ai/agents/agno/ollama_accounting_prototype.py
Only scripted HTTPX transports. No daemon, model, credentials or network needed.
"""

# Fixture-loop closures are invoked/drained before the next iteration.
# ruff: noqa: B023

import asyncio
import base64
import contextvars
import hashlib
import importlib.metadata
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __name__ == "__main__" and os.environ.get("GH98_SANITIZED") != "1":
    artifact_root = Path("/tmp/opencode") / "PROTOTYPE-GH98"
    artifact_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="offline-", dir=artifact_root))
    (scratch / "home").mkdir()
    # Do not copy the ambient environment (credentials, proxies, daemon host).
    clean = {"GH98_SANITIZED": "1", "GH98_ROOT": str(scratch),
             "HOME": str(scratch / "home"), "PATH": "/usr/bin:/bin",
             "AGNO_TELEMETRY": "false", "PYTHONNOUSERSITE": "1"}
    raise SystemExit(subprocess.call([sys.executable, "-P", __file__, *sys.argv[1:]], env=clean))

import httpx
from agno.agent import Agent
from agno.models.ollama import Ollama
from ollama import AsyncClient, Client

HERE = Path(__file__).resolve()
ROOT = HERE.parents[3]
ENDPOINT = "http://prototype.invalid:11434"
MODEL = "PROTOTYPE-offline-only"
CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("prototype_operation", default=None)
CALL: contextvars.ContextVar[str | None] = contextvars.ContextVar("prototype_call", default=None)


class Denied(RuntimeError):
    pass


class EvidenceFailure(RuntimeError):
    pass


class Crash(BaseException):
    """Injected abrupt boundary: deliberately bypass normal Exception handlers."""


class Ledger:
    def __deepcopy__(self, memo):
        return self

    def __init__(self, root, fail=None):
        self.path = root / "PROTOTYPE-wipe-me.sqlite3"
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, kind TEXT, data TEXT)")
        self.conn.execute("CREATE TABLE valid_commit(id INTEGER PRIMARY KEY)")
        self.conn.execute("CREATE TABLE rejected_commit(id INTEGER REFERENCES valid_commit(id) "
                          "DEFERRABLE INITIALLY DEFERRED)")
        self.conn.commit()
        self.fail = fail
        self.broken = False
        self.closed = False
        self.failed = False
        self.active = set()
        self.run = "run-" + uuid.uuid4().hex
        self.incarnation = "fake-server-" + uuid.uuid4().hex
        self.trial = None
        self.crash_after = None
        self.historical_context_lost = False
        self.write("run-open", owner_pid=os.getpid())

    @classmethod
    def historical(cls, path):
        self = cls.__new__(cls)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        row = self.conn.execute("SELECT data FROM events ORDER BY seq LIMIT 1").fetchone()
        first = json.loads(row[0]) if row else {}
        self.run, self.incarnation = first.get("run", "unknown"), first.get("server_incarnation", "unknown")
        self.fail = self.crash_after = None
        self.trial = None
        self.active = set()
        self.broken = False  # Historical absence cannot reconstruct an unwritable cause.
        self.closed = self.failed = True  # Required live context is gone; never resume.
        self.historical_context_lost = True
        return self

    def write(self, kind, **data):
        with self.lock:
            envelope = {
                "run": self.run, "endpoint": ENDPOINT,
                "server_incarnation": self.incarnation,
                **(CURRENT.get() or {}), **data,
            }
            try:
                with self.conn:
                    self.conn.execute("INSERT INTO events(kind,data) VALUES (?,?)",
                                      (kind, json.dumps(envelope, sort_keys=True)))
                    if self.fail == kind:
                        # Real SQLite COMMIT rejection, not disk/fsync simulation.
                        self.conn.execute("INSERT INTO rejected_commit VALUES (1)")
            except sqlite3.Error as exc:
                self.broken = self.closed = self.failed = True
                # This failure is only volatile; do not invent a durable cause.
                raise EvidenceFailure(kind) from exc
            if self.crash_after == kind:
                os._exit(70)

    def rows(self):
        with self.lock:
            return [(n, k, json.loads(d)) for n, k, d in self.conn.execute(
                "SELECT seq,kind,data FROM events ORDER BY seq")]

    def freeze(self, cause):
        with self.lock:
            self.closed = self.failed = True
            self.write("frozen-failure", cause=cause)

    def logical_failed(self, logical, exc):
        with self.lock:
            self.closed = self.failed = True
            if not self.broken and not isinstance(exc, Crash):
                self.write("logical-gap", logical_call=logical, cause=type(exc).__name__)

    def reconcile(self):
        with self.lock:
            return self._reconcile()

    def _reconcile(self):
        # Read from the actual retained database, not the volatile live map.
        rows = self.rows()
        attempts = {d["attempt"] for _, k, d in rows if k == "intent"}
        terminals = set()
        integrity = []
        if [n for n, _, _ in rows] != list(range(1, len(rows) + 1)):
            integrity.append("sequence-gap")
        if sum(k == "run-open" for _, k, _ in rows) != 1:
            integrity.append("missing/duplicate-run-header")
        if any(d.get("run") != self.run or d.get("endpoint") != ENDPOINT
               or d.get("server_incarnation") != self.incarnation for _, _, d in rows):
            integrity.append("authoritative-run-identity")
        trials = [(n, d["trial"]) for n, k, d in rows if k == "trial-open"]
        if len({t for _, t in trials}) != len(trials):
            integrity.append("duplicate-trial")
        context_keys = ("run", "trial", "agent", "session", "invocation", "operation",
                        "endpoint", "server_incarnation")

        def index(kind, key):
            found = {}
            for n, k, d in rows:
                if k != kind:
                    continue
                identity = d.get(key)
                if not isinstance(identity, str) or not identity:
                    integrity.append("missing-" + kind)
                    continue
                if identity in found:
                    integrity.append("missing/duplicate-" + kind)
                found[identity] = (n, d)
            return found

        admissions = index("admission", "operation")
        dispatches = index("dispatch", "operation")
        ends = index("operation-end", "operation")
        starts = index("logical-start", "logical_call")
        finishes = index("logical-end", "logical_call")

        def matches(left, right, keys=context_keys):
            return all(key in left and left[key] == right.get(key) for key in keys)

        for op, (n, d) in admissions.items():
            if (not all(key in d for key in context_keys)
                    or not any(t == d.get("trial") and seq < n for seq, t in trials)):
                integrity.append("admission-trial/identity")
            parent = d.get("parent_operation")
            if parent:
                p = admissions.get(parent)
                if (p is None or p[0] >= n or not matches(d, p[1], tuple(
                        key for key in context_keys if key != "operation"))):
                    integrity.append("child-parent")
                if parent in ends and (op not in ends or ends[op][0] >= ends[parent][0]):
                    integrity.append("parent-ended-before-child")
        for op, (n, d) in dispatches.items():
            admitted = admissions.get(op)
            if admitted is None or admitted[0] >= n or not matches(d, admitted[1]):
                integrity.append("dispatch-without-matching-admission")
        for op, (n, d) in ends.items():
            dispatched = dispatches.get(op)
            if dispatched is None or dispatched[0] >= n or not matches(d, dispatched[1]):
                integrity.append("operation-end-without-dispatch")
        for logical, (n, d) in starts.items():
            dispatched = dispatches.get(d.get("operation"))
            if dispatched is None or dispatched[0] >= n or not matches(d, dispatched[1]):
                integrity.append("logical-without-dispatch")
            if logical in finishes:
                end_n, end_d = finishes[logical]
                if end_n <= n or not matches(d, end_d):
                    integrity.append("logical-end-order/identity")
                intents = [(seq, data) for seq, kind, data in rows
                           if kind == "intent" and data.get("logical_call") == logical]
                if not intents or any(not (n < seq < end_n) for seq, _ in intents):
                    integrity.append("logical-end-without-attempt")
            operation = d.get("operation")
            if operation in ends and (logical not in finishes or finishes[logical][0] >= ends[operation][0]):
                integrity.append("operation-ended-before-logical")
        if set(finishes) - set(starts):
            integrity.append("orphan-logical-end")
        identities = ("run", "trial", "agent", "session", "invocation", "operation",
                      "logical_call", "attempt", "endpoint", "server_incarnation")
        for attempt in attempts:
            events = [(n, k, d) for n, k, d in rows if d.get("attempt") == attempt]
            chain = [(n, k, d) for n, k, d in events
                     if k in {"intent", "send-handoff", "response", "terminal"}]
            expected = ["intent", "send-handoff", "response", "terminal"]
            if [k for _, k, _ in chain] != expected[:len(chain)]:
                integrity.append(attempt + ":attempt-order/cardinality")
                continue
            base = chain[0][2]
            parent = starts.get(base.get("logical_call"))
            if (parent is None or parent[0] >= chain[0][0] or not matches(base, parent[1])):
                integrity.append(attempt + ":logical-parent")
                continue
            logical = base["logical_call"]
            if logical in finishes and any(n >= finishes[logical][0] for n, _, _ in chain):
                integrity.append(attempt + ":after-logical-end")
                continue
            if any(not all(key in d and d[key] == base.get(key) for key in identities)
                   for _, _, d in chain):
                integrity.append(attempt + ":identity")
                continue
            if len(chain) != 4:
                continue
            response, terminal = chain[2][2], chain[3][2]
            body = response.get("body", "").encode()
            if (not valid_terminal(response.get("status"), body) or terminal.get("done") is not True
                    or terminal.get("response_sha256") != response.get("sha256")
                    or hashlib.sha256(body).hexdigest() != response.get("sha256")):
                integrity.append(attempt + ":terminal")
                continue
            terminals.add(attempt)
        if any(k in {"send-handoff", "response", "terminal", "attempt-gap"}
               and d.get("attempt") not in attempts for _, k, d in rows):
            integrity.append("orphan-attempt-event")
        frozen = any(k in {"frozen-failure", "logical-gap", "attempt-gap"}
                     or (k == "operation-end" and d.get("failed")) for _, k, d in rows)
        abandoned = {logical for logical in set(starts) - set(finishes)
                     if starts[logical][1].get("operation") not in self.active}
        return {
            "attempts": len(attempts), "terminals": len(terminals),
            "unknown_attempts": sorted(attempts - terminals),
            "logical_gaps": sorted(set(starts) - set(finishes)), "integrity_errors": sorted(set(integrity)),
            "abandoned_logicals": sorted(abandoned),
            "operation_gaps": sorted(set(admissions) - set(ends)),
            "local_active": sorted(self.active), "volatile_evidence_failure": self.broken,
            "durable_failure_observed": frozen, "historical_context_lost": self.historical_context_lost,
            "frozen_failure": frozen or self.failed,
            "normal_ready": not (attempts - terminals or set(starts) - set(finishes) or integrity
                                  or set(admissions) - set(ends) or self.active
                                  or self.broken or frozen or self.failed),
        }


class Boundary:
    def __init__(self, ledger):
        self.ledger = ledger

    def admit(self, route="foreground", trial="trial-1", parent=None):
        with self.ledger.lock:
            if self.ledger.closed or self.ledger.broken:
                raise Denied("admission closed")
            evidence = self.ledger.reconcile()
            if evidence["integrity_errors"] or evidence["abandoned_logicals"] or evidence["durable_failure_observed"]:
                self.ledger.freeze("required evidence integrity failure")
                raise Denied("evidence integrity failure")
            trial = parent["trial"] if parent else trial
            if self.ledger.trial != trial:
                if self.ledger.trial is not None and not self.ledger.reconcile()["normal_ready"]:
                    raise Denied("preceding trial is not quiescent/reconciled")
                if any(k == "trial-open" and d.get("trial") == trial for _, k, d in self.ledger.rows()):
                    raise Denied("cannot reopen a preceding trial")
                self.ledger.write("trial-open", trial=trial)
                self.ledger.trial = trial
            operation = "op-" + uuid.uuid4().hex
            context = {
                "trial": trial, "agent": "compiled-agent-1",
                "session": f"{self.ledger.run}:compiled-agent-1",
                "invocation": "invocation-" + uuid.uuid4().hex,
                "operation": operation, "route": route,
            }
            if parent:
                context.update({key: parent[key] for key in ("trial", "agent", "session", "invocation")})
                context["parent_operation"] = parent["operation"]
            self.ledger.write("admission", **context)
            return context

    @contextmanager
    def dispatch(self, context):
        token = CURRENT.set(context)
        try:
            with self.ledger.lock:
                if self.ledger.closed or self.ledger.broken:
                    raise Denied("dispatch closed")
                evidence = self.ledger.reconcile()
                if (evidence["integrity_errors"] or evidence["abandoned_logicals"]
                        or evidence["durable_failure_observed"]):
                    self.ledger.freeze("dispatch evidence failure")
                    raise Denied("dispatch evidence failure")
                rows = self.ledger.rows()
                matches = [d for _, k, d in rows if k == "admission"
                           and d.get("operation") == context.get("operation")]
                expected = {"run": self.ledger.run, "endpoint": ENDPOINT,
                            "server_incarnation": self.ledger.incarnation, **context}
                if (len(matches) != 1 or matches[0] != expected or any(
                        k == "dispatch" and d.get("operation") == context.get("operation")
                        for _, k, d in rows)):
                    raise Denied("dispatch requires one matching unconsumed durable admission")
                self.ledger.write("dispatch")
                self.ledger.active.add(context["operation"])
            try:
                yield
            except BaseException as exc:
                self.ledger.closed = self.ledger.failed = True
                if not isinstance(exc, (EvidenceFailure, Crash)):
                    self.ledger.write("frozen-failure", cause=type(exc).__name__)
                raise
            finally:
                with self.ledger.lock:
                    self.ledger.active.discard(context["operation"])
            self.ledger.write("operation-end", failed=self.ledger.failed)
            evidence = self.ledger.reconcile()
            if (self.ledger.closed or self.ledger.broken or evidence["integrity_errors"]
                    or evidence["abandoned_logicals"] or evidence["durable_failure_observed"]):
                self.ledger.closed = self.ledger.failed = True
                raise Denied("operation cannot authorize dependent work")
        finally:
            CURRENT.reset(token)


def complete():
    return {"model": MODEL, "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": "offline fixture"}}


def valid_terminal(status, raw):
    try:
        parsed = json.loads(raw)
        return (status == 200 and isinstance(parsed, dict)
                and parsed.get("done") is True and parsed.get("model") == MODEL
                and "error" not in parsed and parsed.get("done_reason") in {"stop", "length"}
                and isinstance(parsed.get("message"), dict)
                and parsed["message"].get("role") == "assistant"
                and isinstance(parsed["message"].get("content"), str))
    except (ValueError, TypeError):
        return False


class Script:
    """Independent fake origin observations are diagnostics, not Provider evidence."""

    def __init__(self, mode="ok", response=None):
        self.mode = mode
        self.response = response
        self.entered = threading.Event()
        self.release = threading.Event()
        self.origin = []
        self.before_origin: threading.Event | None = None

    def __deepcopy__(self, memo):
        return self

    def result(self, request):
        if self.before_origin is not None and not self.before_origin.wait(10):
            raise RuntimeError("prerequisite fake origin was not entered")
        self.entered.set()
        self.origin.append({"path": request.url.path, "mode": self.mode})
        if self.mode == "hold" and not self.release.wait(10):
            raise RuntimeError("fixture release missing")
        if self.mode == "timeout":
            raise httpx.ReadTimeout("scripted", request=request)
        if self.mode == "connect-failure":
            raise httpx.ConnectError("scripted", request=request)
        if self.mode == "lost-ack":
            self.origin[-1]["fake_accepted"] = True
            raise httpx.ReadError("scripted acknowledgement lost", request=request)
        if self.mode == "abrupt-send":
            raise Crash("after fake origin admission")
        if self.mode == "process-exit-send":
            os._exit(71)
        if self.mode == "partial":
            return httpx.Response(200, content=b'{"model":"PROTOTYPE-offline-only","done":')
        if self.mode == "eof":
            return httpx.Response(200, content=b"")
        body = self.response(request) if callable(self.response) else self.response
        return httpx.Response(200, json=body if body is not None else complete())


class AccountedTransport(httpx.BaseTransport, httpx.AsyncBaseTransport):
    def __init__(self, ledger, script):
        self.ledger = ledger
        self.script = script
        self.serial = 0

    def before(self, request):
        with self.ledger.lock:
            context = CURRENT.get()
            logical = CALL.get()
            if (not context or not logical or context["operation"] not in self.ledger.active
                    or self.ledger.closed or self.ledger.broken):
                if not self.ledger.broken:
                    self.ledger.freeze("untracked or closed transport dispatch")
                raise Denied("untracked or closed transport dispatch")
            payload = json.loads(request.content)
            if sum(k == "intent" and d.get("trial") == context["trial"]
                   for _, k, d in self.ledger.rows()) >= 16:
                self.ledger.freeze("fixture request ceiling")
                raise Denied("fixture request ceiling")
            if (str(request.url) != ENDPOINT + "/api/chat" or request.method != "POST"
                    or payload.get("stream") is not False or payload.get("model") != MODEL):
                self.ledger.freeze("ineligible request")
                raise Denied("only explicit native non-streaming fixture model/endpoint")
            self.serial += 1
            attempt = f"{logical}:attempt-{self.serial}"
            self.ledger.write("intent", logical_call=logical, attempt=attempt,
                              method=request.method, path=request.url.path)
            self.ledger.write("send-handoff", logical_call=logical, attempt=attempt)
            return logical, attempt

    def after(self, request, response, logical, attempt):
        raw = response.content
        if self.script.mode == "lost-response-commit":
            raise Crash("complete fake response received before durable commit")
        if self.script.mode == "process-exit-response":
            os._exit(72)
        self.ledger.write("response", logical_call=logical, attempt=attempt,
                          status=response.status_code, sha256=hashlib.sha256(raw).hexdigest(),
                          bytes=len(raw), body=raw.decode("utf-8", errors="replace"))
        if not valid_terminal(response.status_code, raw):
            self.ledger.freeze("missing or malformed terminal")
            raise Denied("no valid terminal witness")
        self.ledger.write("terminal", logical_call=logical, attempt=attempt,
                          done=True, response_sha256=hashlib.sha256(raw).hexdigest(),
                          late=self.ledger.failed or self.ledger.closed)
        return response

    def handle_request(self, request):
        logical, attempt = self.before(request)
        try:
            response = self.script.result(request)
            return self.after(request, response, logical, attempt)
        except BaseException as exc:
            self.ledger.closed = self.ledger.failed = True
            if not isinstance(exc, (EvidenceFailure, Crash)):
                self.ledger.write("attempt-gap", logical_call=logical, attempt=attempt,
                                  cause=type(exc).__name__)
            raise

    async def handle_async_request(self, request):
        logical, attempt = self.before(request)
        try:
            if self.script.mode in {"cancel-send", "cancel-read"}:
                self.script.entered.set()
                self.script.origin.append({"path": request.url.path, "mode": self.script.mode,
                                           "fake_accepted": self.script.mode == "cancel-read"})
                await asyncio.Event().wait()
            response = self.script.result(request)
            return self.after(request, response, logical, attempt)
        except BaseException as exc:
            self.ledger.closed = self.ledger.failed = True
            if not isinstance(exc, (EvidenceFailure, Crash)):
                self.ledger.write("attempt-gap", logical_call=logical, attempt=attempt,
                                  cause=type(exc).__name__)
            raise


@contextmanager
def no_network():
    """Tripwire, not a sandbox; no inherited configuration or credential reads."""
    from unittest.mock import patch

    def denied(*args, **kwargs):
        raise Denied("network is forbidden in the offline prototype")

    with (patch.object(socket.socket, "connect", denied),
          patch.object(socket.socket, "connect_ex", denied),
          patch.object(socket, "getaddrinfo", denied)):
        yield


class AccountedClient(Client):
    def __init__(self, ledger, script):
        self.ledger = ledger
        self.accounted_transport = AccountedTransport(ledger, script)
        super().__init__(host=ENDPOINT, transport=self.accounted_transport, trust_env=False,
                         follow_redirects=False, timeout=2)

    def chat(self, *args, **kwargs):
        if kwargs.get("stream", False):
            self.ledger.freeze("ineligible streaming request")
            raise Denied("streaming is ineligible")
        if not CURRENT.get() or self.ledger.closed or self.ledger.broken:
            raise Denied("no owning operation")
        logical = "logical-" + uuid.uuid4().hex
        token = CALL.set(logical)
        try:
            self.ledger.write("logical-start", logical_call=logical)
            result = super().chat(*args, **kwargs)
            self.ledger.write("logical-end", logical_call=logical)
            return result
        except BaseException as exc:
            self.ledger.logical_failed(logical, exc)
            raise
        finally:
            CALL.reset(token)


class AccountedAsyncClient(AsyncClient):
    def __init__(self, ledger, script):
        self.ledger = ledger
        self.accounted_transport = AccountedTransport(ledger, script)
        super().__init__(host=ENDPOINT, transport=self.accounted_transport, trust_env=False,
                         follow_redirects=False, timeout=2)

    async def chat(self, *args, **kwargs):
        if (kwargs.get("stream", False) or not CURRENT.get()
                or self.ledger.closed or self.ledger.broken):
            raise Denied("unowned or streaming call")
        logical = "logical-" + uuid.uuid4().hex
        token = CALL.set(logical)
        try:
            self.ledger.write("logical-start", logical_call=logical)
            result = await super().chat(*args, **kwargs)
            self.ledger.write("logical-end", logical_call=logical)
            return result
        except BaseException as exc:
            self.ledger.logical_failed(logical, exc)
            raise
        finally:
            CALL.reset(token)


@dataclass
class AccountedModel(Ollama):
    """Only override client acquisition; inherit native invoke/parse/tool loops.

    Agno's real Model.__deepcopy__ clears cached clients. Shared scratch ledger
    and script explicitly survive copying, and lazy acquisition stays accounted.
    """

    ledger: Any = field(default=None, repr=False)
    script: Any = field(default=None, repr=False)

    def get_client(self):
        if self.client is None:
            self.client = AccountedClient(self.ledger, self.script)
        return self.client

    def get_async_client(self):
        if self.async_client is None:
            self.async_client = AccountedAsyncClient(self.ledger, self.script)
        return self.async_client


def model_for(ledger, script):
    return AccountedModel(id=MODEL, host=ENDPOINT, ledger=ledger, script=script,
                          api_key=None, retries=0, retry_with_guidance=False)


def agent_for(ledger, script):
    return Agent(model=model_for(ledger, script), retries=0, telemetry=False,
                 session_id=f"{ledger.run}:compiled-agent-1", markdown=False)


def tool_response(name, arguments):
    response = complete()
    response["message"] = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}
    return response


def memory_response(request):
    payload = json.loads(request.content)
    if any(m["role"] == "tool" for m in payload["messages"]):
        return complete()
    names = {t["function"]["name"] for t in payload.get("tools", [])}
    if "add_memory" in names:
        return tool_response("add_memory", {"memory": "PROTOTYPE remembers blue"})
    if "update_user_memory" in names:
        return tool_response("update_user_memory", {"task": "Remember blue"})
    return complete()


def probe_tool(agent: Agent) -> str:
    """Trusted offline fixture tool; never grants Mininet action authority."""
    assert isinstance(agent.model, AccountedModel)
    agent.model.get_client().chat(model=MODEL, messages=[{"role": "user", "content": "tool"}])
    agent.model.ledger.write("local-tool-effect", value="preserved")
    return "fixture tool finished"


def probe_hook(agent: Agent, **kwargs):
    assert isinstance(agent.model, AccountedModel)
    agent.model.get_client().chat(model=MODEL, messages=[{"role": "user", "content": "hook"}])
    agent.model.ledger.write("local-hook-effect", value="preserved")


def eligible(agent, ledger):
    """Bounded trusted fixture policy, NOT arbitrary-Python enforcement."""
    from agno.learn.config import UserMemoryConfig
    from agno.learn.machine import LearningMachine
    from agno.learn.stores.user_memory import UserMemoryStore
    from agno.memory.manager import MemoryManager

    def model_ok(model):
        return (type(model) is AccountedModel and model.ledger is ledger
                and model.host == ENDPOINT and model.id == MODEL and model.retries == 0
                and model.retry_with_guidance is False and not model.client_params
                and not model.request_params and not model.api_key
                and (model.client is None or (type(model.client) is AccountedClient
                                             and model.client.ledger is ledger))
                and (model.async_client is None or (type(model.async_client) is AccountedAsyncClient
                                                   and model.async_client.ledger is ledger)))

    unsupported = ("reasoning", "reasoning_model", "parser_model", "output_model",
                   "followups", "followup_model", "compression_manager", "compress_tool_results",
                   "session_summary_manager", "enable_session_summaries", "knowledge",
                   "dependencies", "tool_hooks", "guardrails", "evals", "stream", "stream_events",
                   "fallback_models", "fallback_config", "reasoning_agent")
    reasons = [name for name in unsupported if getattr(agent, name, None)]
    if type(agent) is not Agent or not model_ok(agent.model) or agent.retries != 0:
        reasons.append("unaccounted foreground model/retries")
    if getattr(agent, "pre_hooks", None) not in (None, [], [probe_hook]):
        reasons.append("untracked pre-hook")
    if getattr(agent, "post_hooks", None):
        reasons.append("untracked post-hook")
    if agent.tools not in (None, [], [probe_tool]):
        reasons.append("untracked tools/external requests")
    if agent.memory_manager and (type(agent.memory_manager) is not MemoryManager
                                 or not model_ok(agent.memory_manager.model)):
        reasons.append("alternate/fallback memory model")
    if agent.learning:
        machine = agent.learning
        if (type(machine) is not LearningMachine or not model_ok(machine.model)
                or type(machine.user_memory) is not UserMemoryConfig
                or not model_ok(machine.user_memory.model)
                or machine.custom_stores or machine.knowledge
                or any(getattr(machine, name) for name in (
                    "user_profile", "session_context", "entity_memory", "learned_knowledge", "decision_log"))):
            reasons.append("uncovered LearningMachine store/model graph")
        else:
            stores = machine.stores
            if (set(stores) != {"user_memory"}
                    or type(stores["user_memory"]) is not UserMemoryStore
                    or not model_ok(stores["user_memory"].model)):
                reasons.append("unaccounted initialized learning store")
    if getattr(agent, "_learning", None) is not None and agent._learning is not agent.learning:
        reasons.append("hidden cached learning machine")
    if any(callable(getattr(agent, name, None)) for name in ("instructions", "system_message", "knowledge_retriever")):
        reasons.append("unregistered callable prompt/retrieval")
    if reasons:
        ledger.freeze("ineligible: " + ", ".join(reasons))
        raise Denied(", ".join(reasons))


def run_agent(boundary, context, agent, asynchronous=False):
    async def owned_arun():
        from unittest.mock import patch

        import agno.agent._managers as managers
        tasks = []

        def owned_create(coro):
            parent = CURRENT.get()
            try:
                child = boundary.admit(route=coro.cr_code.co_name, parent=parent)
            except BaseException:
                coro.close()
                raise

            async def work():
                with boundary.dispatch(child):
                    return await coro
            task = asyncio.create_task(work())
            tasks.append(task)
            return task

        # Exact locked built-in task-creation seam, not a general task sandbox.
        with patch.object(managers, "create_task", owned_create):
            try:
                result = await agent.arun("Remember blue", user_id="fixture-user")
            finally:
                results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
                if any(isinstance(r, BaseException) for r in results):
                    boundary.ledger.freeze("owned background task failure")
            return result

    def call():
        eligible(agent, boundary.ledger)
        with ScopedExecutor(boundary) as executor:
            agent._background_executor = executor
            try:
                result = (asyncio.run(owned_arun())
                          if asynchronous else agent.run("Remember blue", user_id="fixture-user"))
            finally:
                executor.drain()
            return result
    return execute(boundary, context, call)


def execute(boundary, context, call):
    with boundary.dispatch(context):
        result = call()
        status = getattr(result, "status", None)
        if status is not None and str(getattr(status, "value", status)).lower() != "completed":
            boundary.ledger.freeze("Agno error status")
        return result


class ScopedExecutor(ThreadPoolExecutor):
    """Trusted prototype executor: explicit child operations and full draining."""

    def __init__(self, boundary):
        super().__init__(max_workers=4)
        self.boundary = boundary
        self.futures = []

    def submit(self, fn, /, *args, **kwargs):
        parent = CURRENT.get()
        if not parent or fn.__module__ != "agno.agent._managers":
            self.boundary.ledger.freeze("unregistered executor submission")
            raise Denied("only owned built-in Agno memory/learning jobs")
        child = self.boundary.admit(route=fn.__name__, parent=parent)
        future = super().submit(execute, self.boundary, child, lambda: fn(*args, **kwargs))
        self.futures.append(future)
        return future

    def drain(self):
        # Each fixture call is bounded by request count and scripted releases.
        # This is not a production deadline or forced thread containment protocol.
        errors = []
        for future in self.futures:
            error = raises(lambda: future.result(timeout=10))
            if error:
                errors.append(error)
        if errors:
            raise Denied("background failure: " + ",".join(errors))


class Observations:
    def __init__(self, root):
        self.root = root
        self.groups = {}
        self.current: dict[str, Any] = {}

    def group(self, name):
        self.current = {"checks": {}, "observations": {}}
        self.groups[name] = self.current
        root = self.root / name
        root.mkdir()
        return root

    def check(self, name, value):
        self.current["checks"][name] = bool(value)
        if not value:
            raise AssertionError(name)

    def capture(self, ledger, scripts=()):
        self.current["observations"] = {
            "reconciliation": ledger.reconcile(),
            "events": ledger.rows(), "fake_origin": [s.origin for s in scripts],
            "files": sorted(p.name for p in ledger.path.parent.iterdir()),
        }


def raises(call, expected=BaseException):
    try:
        call()
    except expected as exc:
        return type(exc).__name__
    return None


def refuse_late(obs, boundary, context, call):
    obs.check("late admission refused", raises(lambda: boundary.admit(trial="trial-late"), Denied))

    def late():
        with boundary.dispatch(context):
            call()

    obs.check("already-admitted late dispatch refused", raises(late, Denied))


def package_provenance():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    result = {}
    for name in ("agno", "ollama", "httpx", "pydantic"):
        dist = importlib.metadata.distribution(name)
        files = {}
        record_verified = True
        for entry in dist.files or []:
            if str(entry).startswith(name + "/") and str(entry).endswith(".py"):
                path = Path(str(dist.locate_file(entry)))
                # Only this workspace's pinned installed sources are inspected.
                path.resolve().relative_to(ROOT)
                digest = hashlib.sha256(path.read_bytes()).digest()
                files[str(entry)] = digest.hex()
                record_verified = record_verified and bool(entry.hash and entry.hash.mode == "sha256"
                    and entry.hash.value == base64.urlsafe_b64encode(digest).decode().rstrip("="))
        pinned = next(p for p in lock["package"] if p["name"] == name)
        assert pinned["version"] == dist.version and files and record_verified
        result[name] = {"version": dist.version, "source_sha256": files,
                        "installed_python_sources_match_RECORD": record_verified,
                        "locked_wheels": pinned["wheels"]}
    return result


def basic_cases(obs):
    for asynchronous in (False, True):
        root = obs.group("normal-async" if asynchronous else "normal-sync")
        ledger, script = Ledger(root), Script()
        boundary = Boundary(ledger)
        result = run_agent(boundary, boundary.admit(), agent_for(ledger, script), asynchronous)
        obs.check("real Agno content", result.content == "offline fixture")
        obs.check("one SDK attempt", len(script.origin) == 1)
        obs.check("all terminal and locally quiescent", ledger.reconcile()["normal_ready"])
        obs.capture(ledger, [script])

    responses = {
        "nonterminal": {**complete(), "done": False},
        "coerced-done": {**complete(), "done": "true"},
        "wrong-model": {**complete(), "model": "other"},
        "error-envelope": {**complete(), "error": "failed"},
        "missing-reason": {k: v for k, v in complete().items() if k != "done_reason"},
        "missing-message": {k: v for k, v in complete().items() if k != "message"},
    }
    for mode in [*responses, "partial", "eof", "timeout", "connect-failure", "lost-ack",
                 "abrupt-send", "lost-response-commit"]:
        root = obs.group(mode)
        ledger = Ledger(root)
        script = Script(mode, responses.get(mode))
        boundary = Boundary(ledger)
        context, queued = boundary.admit(), boundary.admit(route="queued")
        agent = agent_for(ledger, script)
        error = raises(lambda: run_agent(boundary, context, agent))
        obs.check("operation fails even if Agno swallowed error", error)
        obs.check("one send; no automatic replay", len(script.origin) == 1)
        state = ledger.reconcile()
        obs.check("unknown retained", len(state["unknown_attempts"]) == 1)
        obs.check("no normal readiness", not state["normal_ready"])
        refuse_late(obs, boundary, queued, lambda: agent.run("late"))
        obs.check("late requests did not reach origin", len(script.origin) == 1)
        obs.capture(ledger, [script])


def evidence_failures(obs):
    for kind in ("admission", "dispatch", "logical-start", "intent", "send-handoff",
                 "response", "terminal", "logical-end", "operation-end", "frozen-failure", "attempt-gap",
                 "logical-gap"):
        root = obs.group("commit-rejected-" + kind)
        ledger = Ledger(root)
        boundary = Boundary(ledger)
        queued = boundary.admit(route="queued")
        context = boundary.admit()
        baseline = sum(k == kind for _, k, _ in ledger.rows())
        ledger.fail = kind
        script = Script("timeout" if kind in {"frozen-failure", "attempt-gap"} else "ok")
        if kind == "logical-gap":
            script.response = {**complete(), "message": {"role": "assistant", "content": "", "tool_calls": [{}]}}
        if kind == "admission":
            error = raises(lambda: boundary.admit())
        else:
            error = raises(lambda: run_agent(boundary, context, agent_for(ledger, script)))
        obs.check("required commit failure seen", error and ledger.broken)
        obs.check("failed event not durably invented", sum(k == kind for _, k, _ in ledger.rows()) == baseline)
        obs.check("evidence latch closes readiness", not ledger.reconcile()["normal_ready"])
        count = len(script.origin)
        refuse_late(obs, boundary, queued, lambda: None)
        obs.check("no send after failure", count == len(script.origin))
        if kind in {"admission", "dispatch", "logical-start", "intent", "send-handoff"}:
            obs.check("pre-send failure prevents send", count == 0)
        obs.capture(ledger, [script])

    for kind in ("run-open", "trial-open"):
        root = obs.group("commit-rejected-" + kind)
        if kind == "run-open":
            error = raises(lambda: Ledger(root, fail=kind), EvidenceFailure)
            ledger = Ledger.historical(root / "PROTOTYPE-wipe-me.sqlite3")
        else:
            ledger = Ledger(root, fail=kind)
            error = raises(lambda: Boundary(ledger).admit(), EvidenceFailure)
        obs.check("bootstrap commit rejection observed", error)
        obs.check("failed bootstrap event absent", not any(k == kind for _, k, _ in ledger.rows()))
        obs.check("no bootstrap readiness", not ledger.reconcile()["normal_ready"])
        obs.check("no continuation after evidence loss", raises(lambda: Boundary(ledger).admit(), Denied))
        obs.capture(ledger)


def memory_cases(obs):
    from agno.db.schemas.memory import UserMemory
    from agno.db.sqlite import SqliteDb
    from agno.memory.manager import MemoryManager

    method: Any
    call: Any

    for route in ("automatic", "agentic", "extract", "update", "optimize"):
        for asynchronous in (False, True):
            root = obs.group(route + ("-async" if asynchronous else "-sync"))
            ledger, script = Ledger(root), Script(response=memory_response)
            boundary = Boundary(ledger)
            db = SqliteDb(db_file=str(ledger.path))
            manager = MemoryManager(model=model_for(ledger, script), db=db)
            context = boundary.admit(route=route)
            if route in {"automatic", "agentic"}:
                agent = agent_for(ledger, script)
                agent.db = db
                agent.memory_manager = manager
                agent.update_memory_on_run = route == "automatic"
                agent.enable_agentic_memory = route == "agentic"
                run_agent(boundary, context, agent, asynchronous)
            else:
                if route == "optimize":
                    manager.add_user_memory(UserMemory(memory="blue"), user_id="fixture-user")
                    method = manager.aoptimize_memories if asynchronous else manager.optimize_memories
                    call = lambda: method(user_id="fixture-user")
                elif route == "update":
                    method = manager.aupdate_memory_task if asynchronous else manager.update_memory_task
                    call = lambda: method(task="Remember blue", user_id="fixture-user")
                else:
                    method = manager.acreate_user_memories if asynchronous else manager.create_user_memories
                    call = lambda: method(message="Remember blue", user_id="fixture-user")
                execute(boundary, context, lambda: asyncio.run(call()) if asynchronous else call())
            obs.check("real memory path emitted transport", bool(script.origin))
            obs.check("all attempts terminal", ledger.reconcile()["normal_ready"])
            memories = manager.get_user_memories(user_id="fixture-user")
            obs.check("real scratch memory effect", bool(memories))
            assert memories
            if route != "optimize":
                obs.check("tool continuation independently accounted", len(script.origin) >= 2)
            if route == "automatic" and not asynchronous:
                obs.check("background child operation tracked", any(
                    d.get("parent_operation") == context["operation"] for _, k, d in ledger.rows()
                    if k == "admission"))
            obs.current["memories"] = [m.memory for m in memories]
            obs.capture(ledger, [script])

    from agno.learn.config import LearningMode, UserMemoryConfig
    from agno.learn.machine import LearningMachine
    for mode in ("automatic", "agentic"):
        for asynchronous in (False, True):
            root = obs.group("learning-machine-" + mode + ("-async" if asynchronous else "-sync"))
            ledger, script = Ledger(root), Script(response=memory_response)
            boundary = Boundary(ledger)
            db = SqliteDb(db_file=str(ledger.path))
            machine = LearningMachine(db=db, model=model_for(ledger, script),
                user_memory=UserMemoryConfig(db=db, model=model_for(ledger, script),
                    mode=LearningMode.ALWAYS if mode == "automatic" else LearningMode.AGENTIC,
                    enable_agent_tools=mode == "agentic", max_updates_per_run=2))
            agent = agent_for(ledger, script)
            agent.db, agent.learning = db, machine
            run_agent(boundary, boundary.admit(route="learning-" + mode), agent, asynchronous)
            obs.check("real learning store made copied-model calls", len(script.origin) >= 3)
            obs.check("all foreground and learning calls terminal", ledger.reconcile()["normal_ready"])
            store: Any = machine.stores["user_memory"]
            learned = store.get(user_id="fixture-user")
            obs.check("learning memory effect committed", bool(learned and learned.memories))
            obs.check("owned learning work tracked", any(k == "admission" and d.get("parent_operation")
                                                       for _, k, d in ledger.rows()))
            obs.capture(ledger, [script])


def cancellation_cases(obs):
    for mode in ("cancel-send", "cancel-read"):
        root = obs.group(mode)
        ledger, script = Ledger(root), Script(mode)
        boundary = Boundary(ledger)
        context, queued = boundary.admit(), boundary.admit(route="queued")
        agent = agent_for(ledger, script)

        async def exercise():
            async def operation():
                with boundary.dispatch(context):
                    await agent.arun("fixture cancellation")
            task = asyncio.create_task(operation())
            async def entered():
                while not script.entered.is_set():
                    await asyncio.sleep(0)
            await asyncio.wait_for(entered(), 5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            obs.check("local async task finished", task.done())

        asyncio.run(exercise())
        obs.check("cancel is not a terminal", len(ledger.reconcile()["unknown_attempts"]) == 1)
        obs.check("local exit does not grant readiness", not ledger.reconcile()["normal_ready"])
        refuse_late(obs, boundary, queued, lambda: agent.run("late"))
        obs.check("no replay after cancellation", len(script.origin) == 1)
        obs.capture(ledger, [script])


def concurrent_cases(obs):
    # Existing transport can finish late; it cannot clear an older unknown.
    root = obs.group("late-success-cannot-clear-earlier-unknown")
    ledger = Ledger(root)
    held, failed = Script("hold"), Script("lost-ack")
    boundary = Boundary(ledger)
    background = boundary.admit(route="tracked-background")
    foreground, queued = boundary.admit(), boundary.admit(route="queued")
    result = []
    thread = threading.Thread(target=lambda: result.append(raises(
        lambda: run_agent(boundary, background, agent_for(ledger, held)))))
    thread.start()
    obs.check("background send entered before foreground fails", held.entered.wait(5))
    obs.check("next independent trial refused while prior work is outstanding", raises(
        lambda: boundary.admit(trial="trial-2"), Denied))
    obs.check("foreground fails", raises(lambda: run_agent(
        boundary, foreground, agent_for(ledger, failed))))
    obs.check("local work still active", bool(ledger.active))
    obs.check("both attempts unresolved before release", len(ledger.reconcile()["unknown_attempts"]) == 2)
    refuse_late(obs, boundary, queued, lambda: None)
    held.release.set()
    thread.join(10)
    obs.check("late local work drained", not thread.is_alive() and not ledger.active)
    state = ledger.reconcile()
    obs.check("later success recorded separately", state["terminals"] == 1)
    obs.check("earlier ambiguity retained", len(state["unknown_attempts"]) == 1)
    obs.check("frozen trial stays failed", state["frozen_failure"] and not state["normal_ready"])
    obs.capture(ledger, [held, failed])

    # A terminal foreground is insufficient while real Agno automatic memory
    # is blocked in its copied model, running in the owned executor.
    from agno.db.sqlite import SqliteDb
    from agno.memory.manager import MemoryManager
    root = obs.group("foreground-terminal-with-outstanding-automatic-memory")
    ledger = Ledger(root)
    memory, normal = Script("hold", response=memory_response), Script()
    boundary = Boundary(ledger)
    context = boundary.admit()
    agent = agent_for(ledger, normal)
    agent.db = SqliteDb(db_file=str(ledger.path))
    agent.memory_manager = MemoryManager(model=model_for(ledger, memory), db=agent.db)
    agent.update_memory_on_run = True
    outcomes = []
    thread = threading.Thread(target=lambda: outcomes.append(raises(lambda: run_agent(boundary, context, agent))))
    thread.start()
    obs.check("real automatic learning entered", memory.entered.wait(5))
    # Wait for a recorded foreground terminal, not elapsed-time readiness.
    import time
    until = time.monotonic() + 5
    while time.monotonic() < until:
        if any(k == "terminal" and d["operation"] == context["operation"] for _, k, d in ledger.rows()):
            break
        time.sleep(0.001)
    obs.check("foreground terminal recorded", any(k == "terminal" and d["operation"] == context["operation"]
                                                  for _, k, d in ledger.rows()))
    obs.check("foreground cannot clear memory request", not ledger.reconcile()["normal_ready"])
    ledger.freeze("fixture trial deadline")
    queued = {**context, "operation": "not-dispatched-fixture"}
    refuse_late(obs, boundary, queued, lambda: None)
    memory.release.set()
    thread.join(10)
    obs.check("owned automatic worker drained", not thread.is_alive() and not ledger.active)
    obs.check("late completion cannot revise frozen failure", not ledger.reconcile()["normal_ready"])
    obs.capture(ledger, [normal, memory])

    root = obs.group("real-automatic-memory-concurrent-with-foreground-failure")
    ledger = Ledger(root)
    memory, foreground_script = Script("hold", response=memory_response), Script("lost-ack")
    foreground_script.before_origin = memory.entered
    boundary = Boundary(ledger)
    context, queued = boundary.admit(), boundary.admit(route="queued")
    db = SqliteDb(db_file=str(ledger.path))
    manager = MemoryManager(model=model_for(ledger, memory), db=db)
    agent = agent_for(ledger, foreground_script)
    agent.db, agent.memory_manager, agent.update_memory_on_run = db, manager, True
    outcomes = []
    thread = threading.Thread(target=lambda: outcomes.append(raises(lambda: run_agent(boundary, context, agent))))
    thread.start()
    obs.check("actual memory send running", memory.entered.wait(5))
    until = time.monotonic() + 5
    while time.monotonic() < until and not ledger.failed:
        time.sleep(0.001)
    obs.check("foreground failure latched during learning", ledger.failed and thread.is_alive())
    refuse_late(obs, boundary, queued, lambda: None)
    memory.release.set()
    thread.join(10)
    obs.check("all local worker activity drained", not thread.is_alive() and not ledger.active)
    state = ledger.reconcile()
    obs.check("foreground unknown remains despite learning terminal", len(state["unknown_attempts"]) == 1
              and state["terminals"] == 1)
    obs.check("late committed learning preserved", bool(manager.get_user_memories(user_id="fixture-user")))
    obs.check("no readiness or replay", not state["normal_ready"]
              and len(memory.origin) == len(foreground_script.origin) == 1)
    obs.capture(ledger, [foreground_script, memory])


def retry_and_extensions(obs):
    root = obs.group("attempted-retry-refused-after-ambiguity")
    ledger, script = Ledger(root), Script("lost-ack")
    boundary = Boundary(ledger)
    context = boundary.admit()
    client = AccountedClient(ledger, script)
    seen = []

    def retrying_call():
        seen.append(raises(lambda: client.chat(model=MODEL, messages=[])))
        script.mode = "ok"
        seen.append(raises(lambda: client.chat(model=MODEL, messages=[])))

    raises(lambda: execute(boundary, context, retrying_call))
    obs.check("both errors observed", all(seen) and len(seen) == 2)
    obs.check("retry denied before a second send", len(script.origin) == 1)
    obs.check("unknown persists", len(ledger.reconcile()["unknown_attempts"]) == 1)
    obs.capture(ledger, [script])

    root = obs.group("trusted-hook-and-tool-calls")
    ledger = Ledger(root)

    def respond(request):
        payload = json.loads(request.content)
        names = {t["function"]["name"] for t in payload.get("tools", [])}
        if "probe_tool" in names and not any(m["role"] == "tool" for m in payload["messages"]):
            return tool_response("probe_tool", {})
        return complete()

    script = Script(response=respond)
    boundary = Boundary(ledger)
    agent = agent_for(ledger, script)
    agent.tools, agent.pre_hooks = [probe_tool], [probe_hook]
    run_agent(boundary, boundary.admit(), agent)
    obs.check("hook tool foreground continuation each accounted", len(script.origin) == 4)
    obs.check("local effects preserved", {k for _, k, _ in ledger.rows()} >= {"local-tool-effect", "local-hook-effect"})
    obs.check("normal readiness requires all four terminals", ledger.reconcile()["normal_ready"])
    obs.capture(ledger, [script])

    def alternate_memory(agent, ledger):
        from agno.memory.manager import MemoryManager
        agent.memory_manager = MemoryManager()  # Must reject before get_model fallback.

    def unsupported_learning(agent, ledger, feature):
        from agno.learn.config import UserMemoryConfig
        from agno.learn.machine import LearningMachine
        config = UserMemoryConfig(model=model_for(ledger, Script()))
        machine = LearningMachine(model=model_for(ledger, Script()), user_memory=config)
        if feature == "alternate-model":
            config.model = Ollama(id="other", api_key=None)
        else:
            setattr(machine, feature, True)
        agent.learning = machine

    mutations = {
        "agent-retries": lambda a, l: setattr(a, "retries", 1),
        "model-retries": lambda a, l: setattr(a.model, "retries", 1),
        "guidance-retry": lambda a, l: setattr(a.model, "retry_with_guidance", True),
        "alternate-endpoint": lambda a, l: setattr(a.model, "host", "http://other.invalid"),
        "alternate-model": lambda a, l: setattr(a.model, "id", "other"),
        "raw-client-params": lambda a, l: setattr(a.model, "client_params", {"follow_redirects": True}),
        "streaming": lambda a, l: setattr(a, "stream", True),
        "reasoning-probe": lambda a, l: setattr(a, "reasoning", True),
        "output-model": lambda a, l: setattr(a, "output_model", model_for(l, Script())),
        "parser-model": lambda a, l: setattr(a, "parser_model", model_for(l, Script())),
        "followups": lambda a, l: setattr(a, "followups", True),
        "learning-machine": lambda a, l: setattr(a, "learning", True),
        "untracked-hook": lambda a, l: setattr(a, "pre_hooks", [lambda: None]),
        "untracked-external-tool": lambda a, l: setattr(a, "tools", [lambda: None]),
        "hidden-memory-fallback": alternate_memory,
        "unresolved-provider-string": lambda a, l: setattr(a, "model", "ollama:other"),
        "unaccounted-client": lambda a, l: setattr(a.model, "client", Client(
            host=ENDPOINT, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=complete())),
            trust_env=False)),
        "compatible-endpoint-model": lambda a, l: setattr(a, "model", Ollama(id=MODEL, api_key=None)),
        "hidden-fallback-model": lambda a, l: setattr(a, "fallback_models", [model_for(l, Script())]),
        "callable-instructions": lambda a, l: setattr(a, "instructions", lambda: "unsupported"),
    }
    for feature in ("user_profile", "session_context", "entity_memory", "learned_knowledge", "alternate-model"):
        mutations["learning-" + feature] = lambda a, l, f=feature: unsupported_learning(a, l, f)
    for name, mutate in mutations.items():
        root = obs.group("reject-" + name)
        ledger, script = Ledger(root), Script()
        boundary = Boundary(ledger)
        agent = agent_for(ledger, script)
        mutate(agent, ledger)
        obs.check("explicitly rejected at integrated dispatch", raises(
            lambda: run_agent(boundary, boundary.admit(), agent), Denied))
        obs.check("rejected without transport", not script.origin)
        obs.check("not silently accepted", not ledger.reconcile()["normal_ready"])
        obs.capture(ledger, [script])

    for name in ("generate-endpoint", "raw-http-call", "unowned-call", "streaming-sdk"):
        root = obs.group("transport-reject-" + name)
        ledger, script = Ledger(root), Script()
        boundary = Boundary(ledger)
        client = AccountedClient(ledger, script)
        context = boundary.admit()
        calls = {
            "generate-endpoint": lambda: client.generate(model=MODEL, prompt="unsupported"),
            "raw-http-call": lambda: client._client.post("/api/chat", json={"model": MODEL, "stream": False}),
            "unowned-call": lambda: client.chat(model=MODEL, messages=[]),
            "streaming-sdk": lambda: client.chat(model=MODEL, messages=[], stream=True),
        }
        error = (raises(calls[name]) if name == "unowned-call"
                 else raises(lambda: execute(boundary, context, calls[name])))
        obs.check("unsupported SDK route refused", error)
        obs.check("no fake origin admission", not script.origin)
        obs.check("unsupported route not silently normal", not ledger.reconcile()["normal_ready"])
        obs.capture(ledger, [script])


def crash_cases(obs):
    for mode in ("logical-start", "intent", "send-handoff", "process-exit-send",
                 "process-exit-response", "response", "terminal", "logical-end", "operation-end"):
        root = obs.group("process-death-" + mode)
        result = subprocess.run([sys.executable, "-P", str(HERE), "--crash-worker", str(root), mode],
                                timeout=15, check=False)
        obs.check("actual process exited at injected boundary", result.returncode in {70, 71, 72})
        ledger = Ledger.historical(root / "PROTOTYPE-wipe-me.sqlite3")
        state = ledger.reconcile()
        obs.check("historical evidence never resumes run", not state["normal_ready"])
        obs.check("local process exit cannot clear admission", raises(lambda: Boundary(ledger).admit(), Denied))
        if mode not in {"logical-end", "operation-end"}:
            obs.check("missing logical completion remains explicit", bool(state["logical_gaps"]))
        obs.check("context loss is not invented durable failure", state["historical_context_lost"]
                  and not state["durable_failure_observed"])
        if mode in {"terminal", "logical-end", "operation-end"}:
            obs.check("historical terminal retained without replay", state["terminals"] == 1)
        elif mode != "logical-start":
            obs.check("attempt ambiguity retained", len(state["unknown_attempts"]) == 1)
        obs.current["exit_code"] = result.returncode
        obs.capture(ledger)


def crash_worker(root, mode):
    ledger = Ledger(Path(root))
    ledger.crash_after = mode
    script = Script(mode if mode.startswith("process-exit") else "ok")
    boundary = Boundary(ledger)
    with no_network():
        run_agent(boundary, boundary.admit(), agent_for(ledger, script))
    raise AssertionError("fixture did not exit")


def integrity_and_trial_cases(obs):
    root = obs.group("healthy-trial-rollover")
    ledger, script = Ledger(root), Script()
    boundary = Boundary(ledger)
    for trial in ("trial-1", "trial-2"):
        run_agent(boundary, boundary.admit(trial=trial), agent_for(ledger, script))
    obs.check("healthy trials admitted without replay", len(script.origin) == 2)
    obs.check("all correlated trial evidence retained", {d["trial"] for _, k, d in ledger.rows()
                                                          if k == "terminal"} == {"trial-1", "trial-2"})
    obs.check("normal rollover still locally quiescent", ledger.reconcile()["normal_ready"])
    obs.check("prior trial cannot be reopened", raises(lambda: boundary.admit(trial="trial-1"), Denied))
    obs.capture(ledger, [script])

    for mutation in ("session", "invocation", "endpoint", "server_incarnation", "logical_call",
                     "response_hash", "body", "duplicate-terminal", "orphan-terminal", "sequence-gap"):
        root = obs.group("reconcile-reject-" + mutation)
        ledger, script = Ledger(root), Script()
        boundary = Boundary(ledger)
        run_agent(boundary, boundary.admit(), agent_for(ledger, script))
        queued = boundary.admit(route="already-admitted-before-corruption")
        target = "response" if mutation in {"response_hash", "body"} else "terminal"
        seq, data = next((n, d) for n, k, d in ledger.rows() if k == target)
        with ledger.conn:
            if mutation == "duplicate-terminal":
                ledger.conn.execute("INSERT INTO events(kind,data) VALUES ('terminal',?)", (json.dumps(data),))
            elif mutation == "orphan-terminal":
                data["attempt"] = "orphan-attempt"
                ledger.conn.execute("INSERT INTO events(kind,data) VALUES ('terminal',?)", (json.dumps(data),))
            elif mutation == "sequence-gap":
                ledger.conn.execute("DELETE FROM events WHERE kind='send-handoff'")
            else:
                if mutation == "response_hash":
                    data["sha256"] = "wrong"
                elif mutation == "body":
                    data["body"] = json.dumps({**complete(), "done": False})
                else:
                    data[mutation] = "wrong"
                ledger.conn.execute("UPDATE events SET data=? WHERE seq=?", (json.dumps(data), seq))
        state = ledger.reconcile()
        obs.check("corrupt or contradictory evidence cannot complete", not state["normal_ready"])
        obs.check("gap or integrity failure exposed", state["unknown_attempts"] or state["integrity_errors"])
        obs.check("next independent trial denied", raises(lambda: boundary.admit(trial="trial-2"), Denied))
        obs.check("already-admitted dispatch rechecks integrity", raises(lambda: run_agent(
            boundary, queued, agent_for(ledger, script)), Denied))
        obs.check("reconciliation did not replay", len(script.origin) == 1)
        obs.capture(ledger, [script])


def review_regressions(obs):
    root = obs.group("one-shot-dispatch-ownership")
    ledger, script = Ledger(root), Script("hold")
    boundary = Boundary(ledger)
    context = boundary.admit()
    agent = agent_for(ledger, script)
    results = []
    thread = threading.Thread(target=lambda: results.append(raises(lambda: run_agent(boundary, context, agent))))
    thread.start()
    obs.check("original dispatch active", script.entered.wait(5))
    obs.check("simultaneous duplicate refused", raises(lambda: run_agent(boundary, context, agent), Denied))
    obs.check("duplicate did not remove live ownership", context["operation"] in ledger.active)
    obs.check("no false readiness with duplicate owner", not ledger.reconcile()["normal_ready"])
    script.release.set()
    thread.join(10)
    obs.check("original completes normally", not thread.is_alive() and results == [None])
    obs.check("completed operation cannot replay", raises(lambda: run_agent(boundary, context, agent), Denied))
    obs.check("only one origin send", len(script.origin) == 1)
    obs.check("one actual dispatch and completion", sum(k == "dispatch" for _, k, _ in ledger.rows()) == 1
              and sum(k == "operation-end" for _, k, _ in ledger.rows()) == 1)
    other = boundary.admit()
    obs.check("identity-mismatched context rejected", raises(lambda: run_agent(
        boundary, {**other, "invocation": "wrong"}, agent), Denied))
    obs.check("unadmitted context rejected", raises(lambda: run_agent(
        boundary, {**other, "operation": "never-admitted"}, agent), Denied))
    script.mode = "ok"
    run_agent(boundary, other, agent)
    obs.check("valid unused admission retained", ledger.reconcile()["normal_ready"])
    obs.capture(ledger, [script])

    malformed = {**complete(), "message": {"role": "assistant", "content": "", "tool_calls": [{}]}}
    for route in ("approved-hook", "learning-store", "async-sdk"):
        root = obs.group("swallowed-post-transport-validation-" + route)
        ledger, script = Ledger(root), Script(response=malformed)
        boundary = Boundary(ledger)
        context, queued = boundary.admit(), boundary.admit(route="queued")
        if route == "approved-hook":
            agent = agent_for(ledger, script)
            agent.pre_hooks = [probe_hook]
            error = raises(lambda: run_agent(boundary, context, agent))
        elif route == "learning-store":
            from agno.db.sqlite import SqliteDb
            from agno.learn.config import UserMemoryConfig
            from agno.learn.machine import LearningMachine
            learning = Script(response=malformed)
            normal = Script()
            normal.before_origin = learning.entered
            db = SqliteDb(db_file=str(ledger.path))
            agent = agent_for(ledger, normal)
            agent.db = db
            agent.learning = LearningMachine(db=db, model=model_for(ledger, learning),
                user_memory=UserMemoryConfig(db=db, model=model_for(ledger, learning)))
            error = raises(lambda: run_agent(boundary, context, agent))
            script = learning
        else:
            client = AccountedAsyncClient(ledger, script)
            async def call():
                with boundary.dispatch(context):
                    try:
                        await client.chat(model=MODEL, messages=[])
                    except Exception as exc:
                        obs.current["deliberately_swallowed_sdk_error"] = type(exc).__name__
                    await client.chat(model=MODEL, messages=[])
            error = raises(lambda: asyncio.run(call()))
        obs.check("swallowed parsing failure cannot succeed", error and ledger.closed)
        obs.check("request terminal retained separately", ledger.reconcile()["terminals"] >= 1)
        obs.check("logical gap retained", len(ledger.reconcile()["logical_gaps"]) == 1)
        obs.check("no normal readiness", not ledger.reconcile()["normal_ready"])
        refuse_late(obs, boundary, queued, lambda: None)
        obs.check("no second failed-route send", len(script.origin) == 1)
        obs.capture(ledger, [script])

    for omitted in ("dispatch", "admission", "logical-start", "entire-attempt-chain",
                    "duplicate-logical-end", "duplicate-operation-end", "orphan-operation-end",
                    "orphan-logical-end", "early-operation-end", "authoritative-run"):
        root = obs.group("graph-reject-" + omitted)
        ledger, script = Ledger(root), Script()
        boundary = Boundary(ledger)
        run_agent(boundary, boundary.admit(), agent_for(ledger, script))
        original = ledger.rows()
        if omitted == "entire-attempt-chain":
            changed = [(k, d) for _, k, d in original if k not in {"intent", "send-handoff", "response", "terminal"}]
        elif omitted.startswith(("duplicate-", "orphan-")):
            kind = omitted.split("-", 1)[1]
            changed = [(k, d) for _, k, d in original]
            data = next(d.copy() for _, k, d in original if k == kind)
            if omitted.startswith("orphan-"):
                data["logical_call" if kind == "logical-end" else "operation"] = "orphan"
            changed.append((kind, data))
        elif omitted == "early-operation-end":
            changed = [(k, d) for _, k, d in original if k != "operation-end"]
            end = next((k, d) for _, k, d in original if k == "operation-end")
            changed.insert(4, end)
        elif omitted == "authoritative-run":
            changed = [(k, {**d, "run": "wrong"} if k != "run-open" else d) for _, k, d in original]
        else:
            changed = [(k, d) for _, k, d in original if k != omitted]
        with ledger.conn:
            ledger.conn.execute("DELETE FROM events")
            ledger.conn.executemany("INSERT INTO events(seq,kind,data) VALUES (?,?,?)", [
                (n, k, json.dumps(d)) for n, (k, d) in enumerate(changed, 1)])
        state = ledger.reconcile()
        obs.check("no sequence gap to mask lifecycle omission", "sequence-gap" not in state["integrity_errors"])
        obs.check("incomplete graph rejected", bool(state["integrity_errors"]) and not state["normal_ready"])
        obs.check("same-trial admission closed", raises(lambda: boundary.admit(), Denied))
        obs.check("no replay while reconciling", len(script.origin) == 1)
        obs.capture(ledger, [script])

    root = obs.group("cumulative-budget-survives-model-copies")
    ledger, script = Ledger(root), Script()
    boundary = Boundary(ledger)
    from copy import deepcopy

    from agno.models.message import Message
    source = model_for(ledger, script)
    context = boundary.admit()
    def calls():
        for _ in range(17):
            deepcopy(source).response(messages=[Message(role="user", content="fixture")])
    obs.check("shared trial ceiling enforced", raises(lambda: execute(boundary, context, calls)))
    obs.check("copies cannot reset budget", len(script.origin) == 16)
    obs.check("budget failure blocks dependent work", not ledger.reconcile()["normal_ready"])
    obs.capture(ledger, [script])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--crash-worker":
        crash_worker(sys.argv[2], sys.argv[3])
        return
    root = Path(os.environ["GH98_ROOT"])
    obs = Observations(root)
    with no_network():
        basic_cases(obs)
        evidence_failures(obs)
        memory_cases(obs)
        cancellation_cases(obs)
        concurrent_cases(obs)
        retry_and_extensions(obs)
        crash_cases(obs)
        integrity_and_trial_cases(obs)
        review_regressions(obs)
    report = {"warning": "THROWAWAY OFFLINE ONLY; NEVER MERGE; not Provider containment evidence",
              "python": sys.version, "source_sha256": hashlib.sha256(HERE.read_bytes()).hexdigest(),
              "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
              "packages": package_provenance(), "scratch_root": str(root), "groups": obs.groups}
    destination = root / "observations.json"
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"groups": len(obs.groups), "checks": sum(len(g["checks"]) for g in obs.groups.values()),
                      "observations": str(destination)}, indent=2))


if __name__ == "__main__":
    main()
