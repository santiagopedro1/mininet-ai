from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread

import yaml

from mininet_ai.agents import register_builtin_providers
from mininet_ai.experiment import ExperimentRuntime
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.control import IntentClient, IntentControlError, IntentServer
from mininet_ai.substrates import FakeSubstrateRuntime
from tests.agents.test_runtime import configured_plan


class IntentControlTests(unittest.TestCase):
    def make_owner(self) -> ExperimentRuntime:
        plan = configured_plan({"message": "handled"})
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(plan, substrate, registries)
        owner.start()
        self.addCleanup(owner.stop)
        return owner

    def test_second_client_submits_to_owner_and_owner_drains_work(self) -> None:
        with TemporaryDirectory() as temporary:
            plan = configured_plan({"message": "handled"})
            substrate = FakeSubstrateRuntime()
            registries = ProviderRegistries()
            register_builtin_providers(registries, substrate)
            owner = ExperimentRuntime(plan, substrate, registries)
            run = owner.start()
            try:
                with IntentServer(owner, Path(temporary) / "control"):
                    event = IntentClient(Path(temporary) / "control").submit(
                        run.id,
                        plan.digest,
                        "switch-router@s1",
                        "inspect forwarding",
                    )
                    self.assertEqual(event.run_id, run.id)
                    self.assertEqual(event.payload["intent"], "inspect forwarding")
                report = owner.stop()
                self.assertEqual(report.continuous.completed, 1)
            finally:
                if owner.state.value == "running":
                    owner.stop()

    def test_owner_rejects_mismatched_plan_and_unknown_agents(self) -> None:
        owner = self.make_owner()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "control"
            with IntentServer(owner, directory):
                client = IntentClient(directory)
                with self.assertRaisesRegex(IntentControlError, "does not match"):
                    client.submit(
                        owner.run.id,
                        "sha256:" + "0" * 64,
                        "switch-router@s1",
                        "inspect",
                    )
                with self.assertRaisesRegex(
                    IntentControlError, "unknown or has no manual trigger"
                ):
                    client.submit(
                        owner.run.id, owner.run.plan_digest, "missing", "inspect"
                    )
            self.assertEqual(list(directory.iterdir()), [])
            with self.assertRaises(IntentControlError):
                IntentClient(directory).submit(
                    owner.run.id, owner.run.plan_digest, "switch-router@s1", "inspect"
                )
        self.assertEqual(owner.stop().continuous.completed, 0)

    def test_owner_rejects_agent_without_manual_trigger(self) -> None:
        plan = configured_plan({"message": "handled"})
        plan = plan.model_copy(
            update={
                "agents": tuple(
                    agent.model_copy(update={"triggers": ()}) for agent in plan.agents
                )
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(plan, substrate, registries)
        owner.start()
        self.addCleanup(owner.stop)
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "control"
            with (
                IntentServer(owner, directory),
                self.assertRaisesRegex(IntentControlError, "no manual trigger"),
            ):
                IntentClient(directory).submit(
                    owner.run.id,
                    owner.run.plan_digest,
                    "switch-router@s1",
                    "inspect",
                )
        self.assertEqual(owner.stop().continuous.completed, 0)

    def test_malformed_and_oversized_requests_do_not_break_intake(self) -> None:
        owner = self.make_owner()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "control"
            with IntentServer(owner, directory):
                path = next(directory.iterdir())
                for message in (b"not-json\n", b"x" * 65_537):
                    with (
                        self.subTest(message_size=len(message)),
                        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection,
                    ):
                        connection.settimeout(3)
                        connection.connect(str(path))
                        connection.sendall(message)
                        response = connection.makefile("rb").readline()
                        self.assertIn("error", json.loads(response))
                with self.assertRaises(IntentControlError):
                    IntentClient(directory).submit(
                        owner.run.id,
                        owner.run.plan_digest,
                        "switch-router@s1",
                        "x" * 8193,
                    )
                IntentClient(directory).submit(
                    owner.run.id, owner.run.plan_digest, "switch-router@s1", "inspect"
                )
        self.assertEqual(owner.stop().continuous.completed, 1)

    def test_server_refuses_public_directory_and_existing_endpoint(self) -> None:
        owner = self.make_owner()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "control"
            directory.mkdir(mode=0o755)
            with (
                self.assertRaisesRegex(IntentControlError, "owner-only"),
                IntentServer(owner, directory),
            ):
                pass
            directory.chmod(0o700)
            with IntentServer(owner, directory):
                with (
                    self.assertRaises(IntentControlError),
                    IntentServer(owner, directory),
                ):
                    pass
                event = IntentClient(directory).submit(
                    owner.run.id, owner.run.plan_digest, "switch-router@s1", "inspect"
                )
                self.assertEqual(event.run_id, owner.run.id)

    def test_shutdown_interrupts_partial_request(self) -> None:
        owner = self.make_owner()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "control"
            with (
                IntentServer(owner, directory) as server,
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection,
            ):
                connection.connect(str(next(directory.iterdir())))
                connection.sendall(b'{"intent":')
                # Allow the server to accept the connection before stopping it.
                time.sleep(0.05)
                started = time.monotonic()
                server.close()
                self.assertLess(time.monotonic() - started, 1)
            self.assertEqual(list(directory.iterdir()), [])
        self.assertEqual(owner.stop().continuous.completed, 0)

    def test_trickling_request_has_total_deadline_and_does_not_block_next_intent(
        self,
    ) -> None:
        owner = self.make_owner()
        with TemporaryDirectory() as temporary:
            directory = Path(temporary) / "control"
            with (
                IntentServer(owner, directory),
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection,
            ):
                connection.settimeout(3)
                connection.connect(str(next(directory.iterdir())))
                stopped = Event()

                def trickle() -> None:
                    while not stopped.is_set():
                        try:
                            connection.sendall(b"x")
                        except OSError:
                            return
                        stopped.wait(0.1)

                sender = Thread(target=trickle, daemon=True)
                sender.start()
                try:
                    with connection.makefile("rb") as stream:
                        response = json.loads(stream.readline())
                    self.assertIn("error", response)
                finally:
                    stopped.set()
                    sender.join(timeout=1)
                IntentClient(directory).submit(
                    owner.run.id, owner.run.plan_digest, "switch-router@s1", "inspect"
                )
        self.assertEqual(owner.stop().continuous.completed, 1)

    def test_separate_cli_process_submits_to_foreground_owner(self) -> None:
        command = [sys.executable, "-c", "from mininet_ai.cli import app; app()"]
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            experiment = directory / "experiment.yaml"
            experiment.write_text(
                yaml.safe_dump(configured_plan({"message": "handled"}).snapshot)
            )
            log = directory / "run.log"
            control = directory / "control"
            process = subprocess.Popen(
                [
                    *command,
                    "run",
                    str(experiment),
                    "--format",
                    "json",
                    "--control-dir",
                    str(control),
                    "--log-file",
                    str(log),
                    "--ledger-db",
                    str(directory / "runs.sqlite3"),
                    "--agno-db",
                    str(directory / "agno.sqlite3"),
                    "--shared-state-db",
                    str(directory / "state.sqlite3"),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                deadline = time.monotonic() + 10
                match = None
                while time.monotonic() < deadline and process.poll() is None:
                    if log.exists():
                        match = re.search(r"Run (\S+) is active", log.read_text())
                        if match is not None:
                            break
                    time.sleep(0.02)
                self.assertIsNotNone(
                    match, log.read_text() if log.exists() else "owner did not start"
                )
                assert match is not None
                result = subprocess.run(
                    [
                        *command,
                        "invoke",
                        str(experiment),
                        match[1],
                        "switch-router@s1",
                        "--intent",
                        "inspect from another terminal",
                        "--control-dir",
                        str(control),
                        "--format",
                        "json",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                event = json.loads(result.stdout)
                self.assertEqual(
                    event["payload"]["intent"], "inspect from another terminal"
                )
                process.terminate()
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stderr)
                report = json.loads(stdout)
                self.assertEqual(report["continuous"]["completed"], 1)
                self.assertEqual(report["continuous"]["failed"], 0)
                self.assertIn("Queued terminal intent", log.read_text())
                self.assertEqual(list(control.iterdir()), [])
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
