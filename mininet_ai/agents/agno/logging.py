"""Agno-owned logging adaptation; never forward raw provider records."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Self

from agno.utils import log

from mininet_ai.dependency_logging import (
    DiagnosticSink,
    LoggerSnapshot,
    current_diagnostic_invocation,
)


@dataclass
class _AgnoLoggingState:
    default_logger: logging.Logger
    agent_logger: logging.Logger
    team_logger: logging.Logger
    workflow_logger: logging.Logger
    tracebacks: bool
    debug_on: bool
    debug_level: Literal[1, 2]

    @classmethod
    def save(cls) -> Self:
        return cls(
            log.logger,
            log.agent_logger,
            log.team_logger,
            log.workflow_logger,
            log.log_tracebacks,
            log.debug_on,
            log.debug_level,
        )

    def restore(self) -> None:
        log.configure_agno_logging(
            custom_default_logger=self.default_logger,
            custom_agent_logger=self.agent_logger,
            custom_team_logger=self.team_logger,
            custom_workflow_logger=self.workflow_logger,
            enable_log_tracebacks=self.tracebacks,
        )
        log.debug_on = self.debug_on
        log.debug_level = self.debug_level


class _DiagnosticHandler(logging.Handler):
    def __init__(self, sink: DiagnosticSink, run_id: str) -> None:
        super().__init__(logging.WARNING)
        self.sink, self.run_id = sink, run_id

    def emit(self, record: logging.LogRecord) -> None:
        context = current_diagnostic_invocation()
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
        # Never format msg/args/exception: provider content is excluded.
        self.sink(message, level=record.levelno, source=source)


@contextmanager
def capture_agno_diagnostics(run_id: str, sink: DiagnosticSink) -> Iterator[None]:
    state = _AgnoLoggingState.save()
    logger = logging.getLogger("mininet-ai.dependency.agno")
    snapshots = [
        LoggerSnapshot.save(logging.getLogger(name))
        for name in ("agno", "agno-team", "agno-workflow")
    ]
    snapshots.append(LoggerSnapshot.save(logger))
    handler = _DiagnosticHandler(sink, run_id)
    try:
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        logger.disabled = False
        logger.propagate = False
        log.configure_agno_logging(
            custom_default_logger=logger,
            custom_agent_logger=logger,
            custom_team_logger=logger,
            custom_workflow_logger=logger,
            enable_log_tracebacks=False,
        )
        yield
    finally:
        try:
            for snapshot in reversed(snapshots):
                snapshot.restore()
            state.restore()
        finally:
            handler.close()
