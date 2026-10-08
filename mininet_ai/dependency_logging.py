"""Opt-in, run-owned dependency diagnostics; never intercept command streams.

Only reviewed Mininet lifecycle call sites may forward INFO text. Other native
diagnostics and Agno warnings are normalized without inspecting raw messages.
Global logger ownership is exclusive; library callers are unaffected by default.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, get_ident
from typing import Protocol, Self


@dataclass(frozen=True)
class Invocation:
    run_id: str
    agent_id: str
    invocation_id: str


_invocation: ContextVar[Invocation | None] = ContextVar(
    "diagnostic_invocation", default=None
)
_owner = Lock()


@contextmanager
def invocation_diagnostics(
    run_id: str, agent_id: str, invocation_id: str
) -> Iterator[None]:
    token = _invocation.set(Invocation(run_id, agent_id, invocation_id))
    try:
        yield
    finally:
        _invocation.reset(token)


class DiagnosticSink(Protocol):
    def __call__(
        self,
        message: str,
        *,
        level: int = logging.INFO,
        source: str = "Run",
        visible: bool = False,
    ) -> None: ...


# No cmd/sendCmd/waitOutput, tests, interface command failures, or daemon output.
_LIFECYCLE = {
    "net.py": {
        "waitConnected",
        "buildFromTopo",
        "configHosts",
        "build",
        "start",
        "stop",
    },
    "node.py": {"waitListening", "batchShutdown", "checkListening", "defaultIntf"},
}


class _NativeHandler(logging.Handler):
    def __init__(self, sink: DiagnosticSink, run_id: str, root: Path) -> None:
        super().__init__(logging.INFO)
        self.sink, self.run_id, self.root = sink, run_id, root
        self.pending: dict[tuple[int, Invocation | None, int, str], str] = {}

    def emit(self, record: logging.LogRecord) -> None:
        path = Path(record.pathname)
        function = record.funcName
        # Mininet's list-compatible aliases add a non-logging wrapper frame.
        # Recover the synchronous emitting call site, not a guessed active agent.
        if path == self.root / "log.py":
            frame = sys._getframe()
            try:
                while frame is not None:
                    candidate = Path(frame.f_code.co_filename)
                    if candidate.parent == self.root and candidate.name != "log.py":
                        path, function = candidate, frame.f_code.co_name
                        break
                    frame = frame.f_back
            finally:
                del frame
        if (
            path.parent != self.root
            or record.levelno < logging.INFO
            or record.levelno == 25
        ):
            return
        context = _invocation.get()
        if context is not None and context.run_id != self.run_id:
            return
        # Only reviewed INFO call sites, not arbitrary dependency text.
        if record.levelno == logging.INFO and function not in _LIFECYCLE.get(
            path.name, set()
        ):
            return
        # Warnings/errors may themselves contain output: report a safe summary.
        if record.levelno >= logging.WARNING:
            self._flush_thread(get_ident(), context)
            self._send(
                f"Mininet {record.levelname.lower()} diagnostic ({path.stem}.{function})",
                record.levelno,
                context,
            )
            return
        key = (get_ident(), context, record.levelno, function)
        for previous in list(self.pending):
            if previous[:2] == key[:2] and previous != key:
                self._flush(previous)
        text = self.pending.pop(key, "") + record.getMessage()
        lines = text.split("\n")
        for line in lines[:-1]:
            if line.strip():
                self._send(line.strip(), record.levelno, context)
        tail = lines[-1]
        # Bound partial state; never retain arbitrarily long command-like text.
        while len(tail) >= 4096:
            self._send(tail[:4096], record.levelno, context)
            tail = tail[4096:]
        if tail:
            if len(self.pending) >= 256:
                self._flush(next(iter(self.pending)))
            self.pending[key] = tail

    def _send(self, message: str, level: int, context: Invocation | None) -> None:
        if context is not None:
            message += f" agent={context.agent_id} invocation={context.invocation_id}"
        self.sink(message, level=level, source="Mininet")

    def _flush(self, key: tuple[int, Invocation | None, int, str]) -> None:
        message = self.pending.pop(key)
        if message.strip():
            self._send(message.strip(), key[2], key[1])

    def _flush_thread(self, thread: int, context: Invocation | None) -> None:
        for key in list(self.pending):
            if key[:2] == (thread, context):
                self._flush(key)

    def flush(self) -> None:
        self.acquire()
        try:
            for key in list(self.pending):
                self._flush(key)
        finally:
            self.release()


class _AgnoHandler(logging.Handler):
    def __init__(self, sink: DiagnosticSink, run_id: str) -> None:
        super().__init__(logging.WARNING)
        self.sink, self.run_id = sink, run_id

    def emit(self, record: logging.LogRecord) -> None:
        context = _invocation.get()
        if context is not None and context.run_id != self.run_id:
            return
        source = "Run" if context is None else f"Agent:{context.agent_id}"
        message = (
            f"Agno dependency {record.levelname.lower()} diagnostic dependency=agno"
        )
        message += (
            " agent=unknown"
            if context is None
            else f" agent={context.agent_id} invocation={context.invocation_id}"
        )
        # Never format record.msg/args/exception: provider content is excluded.
        self.sink(message, level=record.levelno, source=source)


@dataclass
class _LoggerState:
    logger: logging.Logger
    handlers: list[logging.Handler]
    level: int
    propagate: bool
    disabled: bool

    @classmethod
    def save(cls, logger: logging.Logger) -> Self:
        return cls(
            logger, logger.handlers[:], logger.level, logger.propagate, logger.disabled
        )

    def restore(self) -> None:
        self.logger.handlers = self.handlers
        self.logger.setLevel(self.level)
        self.logger.propagate = self.propagate
        self.logger.disabled = self.disabled


class RunDiagnostics:
    """Capture supported diagnostics for one noninteractive CLI run only."""

    def __init__(
        self, run_id: str, sink: DiagnosticSink, *, mininet: bool = False
    ) -> None:
        self.run_id, self.sink, self.mininet = run_id, sink, mininet
        self.states: list[_LoggerState] = []
        self.handlers: list[logging.Handler] = []
        self.agno_state: tuple[object, ...] | None = None

    def __enter__(self) -> Self:
        if not _owner.acquire(blocking=False):
            raise RuntimeError(
                "dependency diagnostics are already owned by another run"
            )
        try:
            from agno.utils import log as agno_log

            self.agno_state = (
                agno_log.logger,
                agno_log.agent_logger,
                agno_log.team_logger,
                agno_log.workflow_logger,
                agno_log.log_tracebacks,
                agno_log.debug_on,
                agno_log.debug_level,
            )
            for name in ("agno", "agno-team", "agno-workflow"):
                self.states.append(_LoggerState.save(logging.getLogger(name)))
            handler = _AgnoHandler(self.sink, self.run_id)
            self.handlers.append(handler)
            logger = logging.getLogger("mininet-ai.dependency.agno")
            self.states.append(_LoggerState.save(logger))
            logger.handlers = []
            logger.setLevel(logging.WARNING)
            logger.disabled = False
            logger.addHandler(handler)
            logger.propagate = False
            agno_log.configure_agno_logging(
                custom_default_logger=logger,
                custom_agent_logger=logger,
                custom_team_logger=logger,
                custom_workflow_logger=logger,
                enable_log_tracebacks=False,
            )
            if self.mininet:
                import mininet as package  # pyright: ignore[reportMissingImports]
                from mininet.log import lg  # pyright: ignore[reportMissingImports]

                self.states.append(_LoggerState.save(lg))
                native = _NativeHandler(
                    self.sink, self.run_id, Path(package.__file__).parent
                )
                self.handlers.append(native)
                lg.handlers = [native]
                lg.disabled = False
                lg.propagate = False
                lg.setLevel(logging.INFO)
                if not lg.isEnabledFor(logging.INFO):
                    raise RuntimeError(
                        "pre-used Mininet logger has an incompatible INFO cache; use a fresh CLI process"
                    )
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *error: object) -> None:
        try:
            for handler in self.handlers:
                handler.flush()
        finally:
            try:
                for state in reversed(self.states):
                    state.restore()
                if self.agno_state is not None:
                    from agno.utils import log

                    (
                        log.logger,
                        log.agent_logger,
                        log.team_logger,
                        log.workflow_logger,
                        log.log_tracebacks,
                        log.debug_on,
                        log.debug_level,
                    ) = self.agno_state  # type: ignore[assignment]
                for handler in self.handlers:
                    handler.close()
            finally:
                _owner.release()
