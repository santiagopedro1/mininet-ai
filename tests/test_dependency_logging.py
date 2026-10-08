"""Dependency logging boundary: real logger APIs, without a live topology."""

import io
import logging
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from mininet_ai.dependency_logging import RunDiagnostics, invocation_diagnostics


def test_owned_capture_filters_native_echoes_and_restores_logger(tmp_path):
    from mininet import log, net

    received = []
    foreign_output = io.StringIO()
    foreign = logging.StreamHandler(foreign_output)
    original_handlers, original_level = log.lg.handlers[:], log.lg.level
    log.lg.handlers = [foreign]
    try:
        with RunDiagnostics(
            "owned",
            lambda message, **fields: received.append((message, fields)),
            mininet=True,
        ):

            def fragment(text):
                log.lg.handle(
                    logging.LogRecord(
                        "mininet",
                        logging.INFO,
                        net.__file__,
                        1,
                        text,
                        (),
                        None,
                        "stop",
                    )
                )

            fragment("Stopping ")
            fragment("switches\n")
            log.lg.handle(
                logging.LogRecord(
                    "mininet",
                    logging.INFO,
                    str(Path(net.__file__).with_name("node.py")),
                    1,
                    "private command output",
                    (),
                    None,
                    "waitOutput",
                )
            )
            log.lg.handle(
                logging.LogRecord(
                    "mininet",
                    logging.DEBUG,
                    net.__file__,
                    1,
                    "private debug",
                    (),
                    None,
                    "stop",
                )
            )
            log.lg.handle(
                logging.LogRecord(
                    "mininet", 25, net.__file__, 1, "interactive", (), None, "stop"
                )
            )
            assert foreign_output.getvalue() == ""
        assert [(message, fields["source"]) for message, fields in received] == [
            ("Stopping switches", "Mininet")
        ]
        assert log.lg.handlers == [foreign]
        assert log.lg.level == original_level
    finally:
        log.lg.handlers = original_handlers
        log.lg.setLevel(original_level)


def test_agno_warning_is_normalized_with_emission_context_and_restored():
    from agno.utils import log

    previous = log.logger, log.agent_logger, log.team_logger, log.workflow_logger
    received = []
    with RunDiagnostics(
        "owned", lambda message, **fields: received.append((message, fields))
    ):
        with invocation_diagnostics("owned", "agent-one", "inv-one"):
            log.log_warning("private provider response", exc_info=True)
        log.log_warning("other private response")
    assert (
        log.logger,
        log.agent_logger,
        log.team_logger,
        log.workflow_logger,
    ) == previous
    assert len(received) == 2
    assert received[0][1]["source"] == "Agent:agent-one"
    assert "invocation=inv-one" in received[0][0]
    assert received[1][1]["source"] == "Run"
    assert "agent=unknown" in received[1][0]
    assert all("private" not in message for message, _ in received)


def test_capture_rejects_competing_run_and_restores_after_exception():
    with (
        pytest.raises(RuntimeError, match="operation failed"),
        RunDiagnostics("one", lambda *args, **kwargs: None),
    ):
        with (
            pytest.raises(RuntimeError, match="already owned"),
            RunDiagnostics("two", lambda *args, **kwargs: None),
        ):
            pass
        raise RuntimeError("operation failed")
    with RunDiagnostics("three", lambda *args, **kwargs: None):
        pass


def test_real_mininet_aliases_capture_lifecycle_without_changing_command_results():
    from mininet.net import Mininet
    from mininet.util import quietRun

    network = Mininet.__new__(Mininet)
    (
        network.controllers,
        network.switches,
        network.hosts,
        network.links,
        network.terms,
    ) = [], [], [], [], []
    received = []
    with RunDiagnostics(
        "owned", lambda message, **fields: received.append(message), mininet=True
    ):
        network.stop()
        assert (
            quietRun(["printf", "PRIVATE command result"]) == "PRIVATE command result"
        )
    assert "*** Stopping 0 switches" in received
    assert "*** Done" in received
    assert all("PRIVATE" not in message for message in received)


def test_native_fragments_do_not_merge_invocations_or_threads():
    from mininet import log, net

    received = []
    barrier = threading.Barrier(2)

    def worker(identity):
        with invocation_diagnostics("owned", identity, f"inv-{identity}"):
            log.lg.handle(
                logging.LogRecord(
                    "mininet",
                    logging.INFO,
                    net.__file__,
                    1,
                    f"{identity} ",
                    (),
                    None,
                    "stop",
                )
            )
            barrier.wait(timeout=2)
            log.lg.handle(
                logging.LogRecord(
                    "mininet",
                    logging.INFO,
                    net.__file__,
                    1,
                    "finished",
                    (),
                    None,
                    "stop",
                )
            )

    with RunDiagnostics(
        "owned", lambda message, **fields: received.append(message), mininet=True
    ):
        threads = [
            threading.Thread(target=worker, args=(identity,))
            for identity in ("one", "two")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()
    assert sorted(received) == [
        "one finished agent=one invocation=inv-one",
        "two finished agent=two invocation=inv-two",
    ]


def test_preused_detached_logger_cache_is_rejected_without_leaking_ownership():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import logging
from mininet.log import lg
from mininet_ai.dependency_logging import RunDiagnostics
assert not lg.isEnabledFor(logging.INFO)
handlers, level = lg.handlers[:], lg.level
try:
    with RunDiagnostics('owned', lambda *args, **kwargs: None, mininet=True):
        raise AssertionError('incompatible cache accepted')
except RuntimeError as error:
    assert 'incompatible INFO cache' in str(error)
assert lg.handlers == handlers and lg.level == level
with RunDiagnostics('next', lambda *args, **kwargs: None):
    pass
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr


def test_native_capture_restores_effective_info_enablement():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import logging
from mininet.log import lg
from mininet_ai.dependency_logging import RunDiagnostics
assert lg.level == 25
with RunDiagnostics('owned', lambda *args, **kwargs: None, mininet=True):
    assert lg.isEnabledFor(logging.INFO)
assert lg.level == 25
assert not lg.isEnabledFor(logging.INFO), 'capture left INFO enabled'
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
