from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import patch

from mininet_ai.capabilities import (
    ProcessCapabilityProvider,
    ServiceCapabilityProvider,
    SubstrateActionProvider,
    SubstrateObservationProvider,
)
from mininet_ai.compiler import compile_experiment
from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    CapabilityProviderError,
    ExecutionCatalog,
)
from mininet_ai.substrates import (
    ActionResult,
    ActionStatus,
    ObservationResult,
    SubstrateRuntime,
)
from mininet_ai.transports import HttpTransportError
from tests.compiler.helpers import EXAMPLE

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class RecordingRuntime:
    def __init__(self) -> None:
        self.action_requests = []
        self.observation_queries = []

    def execute(self, run_id, request):
        self.action_requests.append((run_id, request))
        return ActionResult(
            run_id=run_id,
            request_id=request.id,
            status=ActionStatus.SUCCEEDED,
            completed_at=NOW,
            changed=True,
            output={"installed": True},
        )

    def observe(self, run_id, query):
        self.observation_queries.append((run_id, query))
        return ObservationResult(
            run_id=run_id,
            query=query,
            observed_at=NOW,
            values={query.targets[0]: {"state": "up"}},
        )


class RecordingTransport:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response or {
            "changed": False,
            "output": {"observed": True},
        }
        self.error = error
        self.calls = []

    def post_json(self, url, payload, **options):
        self.calls.append((url, payload, options))
        if self.error is not None:
            raise self.error
        return self.response


class CapabilityAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        plan = compile_experiment(EXAMPLE)
        definition = ExecutionCatalog(plan).resolve("switch-router@s1")
        cls.capability = definition.capabilities[0]
        attachment = definition.instance.attachment
        cls.context = AgentContext(
            invocationId="invoke-1",
            runId="run-1",
            agentId=definition.instance.id,
            deployment=definition.instance.deployment,
            layer=attachment.layer,
            customLayer=attachment.custom_layer,
            targetKind=attachment.target_kind,
            targets=attachment.targets,
            capabilities=definition.instance.capabilities,
            intent="inspect and repair",
            invokedAt=NOW,
        )

    @staticmethod
    def proposal(**updates) -> ActionProposal:
        values = {
            "id": "proposal-1",
            "capability": "openflow.flow.install",
            "target": "s1",
            "arguments": {"match": "ip", "actions": "normal"},
            "timeoutSeconds": 2,
        }
        values.update(updates)
        return ActionProposal.model_validate(values)

    def test_substrate_action_and_observation_adapters_preserve_contracts(
        self,
    ) -> None:
        runtime = RecordingRuntime()
        substrate = cast(SubstrateRuntime, runtime)
        action_provider = SubstrateActionProvider(self.capability, substrate)
        observation_provider = SubstrateObservationProvider(
            self.capability,
            substrate,
        )
        proposal = self.proposal()

        action = action_provider.execute(self.context, proposal)
        observation = observation_provider.execute(self.context, proposal)

        self.assertTrue(action.changed)
        request = runtime.action_requests[0][1]
        self.assertEqual(request.name, self.capability.metadata.name)
        self.assertEqual(request.timeout_seconds, 2)
        query = runtime.observation_queries[0][1]
        self.assertEqual(query.name, self.capability.metadata.name)
        self.assertEqual(query.targets, ("s1",))
        observed = cast(dict[str, Any], observation.output["s1"])
        self.assertEqual(observed["state"], "up")

    def test_process_adapter_uses_versioned_json_protocol(self) -> None:
        script = (
            "import json,os,sys; request=json.load(sys.stdin); "
            "json.dump({'changed': True, 'output': {"
            "'target': request['proposal']['target'], "
            "'version': request['contractVersion'], "
            "'inheritedSecret': 'MININET_AI_TEST_SECRET' in os.environ}}, "
            "sys.stdout)"
        )
        provider = ProcessCapabilityProvider((sys.executable, "-c", script))

        with patch.dict(os.environ, {"MININET_AI_TEST_SECRET": "secret"}):
            result = provider.execute(self.context, self.proposal())

        self.assertTrue(result.changed)
        self.assertEqual(result.output["target"], "s1")
        self.assertEqual(
            result.output["version"],
            AGENT_RUNTIME_CONTRACT_VERSION,
        )
        self.assertFalse(result.output["inheritedSecret"])

    def test_process_adapter_reports_exit_and_output_failures(self) -> None:
        cases = (
            (
                (
                    sys.executable,
                    "-c",
                    "import sys; sys.stderr.write('bad'); sys.exit(3)",
                ),
                1024,
                "capability.process.failed",
            ),
            (
                (sys.executable, "-c", "print('not-json')"),
                1024,
                "capability.process.invalid-response",
            ),
            (
                (sys.executable, "-c", "print('x' * 10000)"),
                100,
                "capability.process.output-too-large",
            ),
        )

        for command, limit, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                provider = ProcessCapabilityProvider(
                    command,
                    max_output_bytes=limit,
                )
                with self.assertRaises(CapabilityProviderError) as context:
                    provider.execute(self.context, self.proposal())
                self.assertEqual(context.exception.code, expected_code)

    def test_process_timeout_stops_the_complete_process_group(self) -> None:
        with TemporaryDirectory() as temporary:
            pid_path = Path(temporary) / "child.pid"
            script = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(30)']); "
                "open(sys.argv[1], 'w').write(str(child.pid)); time.sleep(30)"
            )
            provider = ProcessCapabilityProvider(
                (sys.executable, "-c", script, str(pid_path))
            )

            with self.assertRaises(TimeoutError):
                provider.execute(
                    self.context,
                    self.proposal(timeoutSeconds=0.1),
                )

            child_pid = int(pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2
            while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(Path(f"/proc/{child_pid}").exists())

    def test_service_adapter_uses_bounded_versioned_json_protocol(self) -> None:
        transport = RecordingTransport()
        provider = ServiceCapabilityProvider(
            "https://capabilities.example.test/invoke",
            transport=transport,
            headers={"authorization": "Bearer test"},
            max_response_bytes=2048,
        )

        result = provider.execute(self.context, self.proposal(timeoutSeconds=4))

        self.assertEqual(result.output, {"observed": True})
        url, payload, options = transport.calls[0]
        self.assertEqual(url, "https://capabilities.example.test/invoke")
        self.assertEqual(
            payload["contractVersion"],
            AGENT_RUNTIME_CONTRACT_VERSION,
        )
        self.assertEqual(options["timeout_seconds"], 4)
        self.assertEqual(options["max_response_bytes"], 2048)
        self.assertEqual(options["headers"]["authorization"], "Bearer test")

    def test_service_transport_and_response_failures_are_typed(self) -> None:
        cases = (
            (
                RecordingTransport(
                    error=HttpTransportError("offline", code="request-failed")
                ),
                "capability.service.request-failed",
            ),
            (
                RecordingTransport(response={"unexpected": True}),
                "capability.service.invalid-response",
            ),
        )

        for transport, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                provider = ServiceCapabilityProvider(
                    "http://127.0.0.1:9000/invoke",
                    transport=transport,
                )
                with self.assertRaises(CapabilityProviderError) as context:
                    provider.execute(self.context, self.proposal())
                self.assertEqual(context.exception.code, expected_code)

    def test_service_endpoint_rejects_embedded_credentials(self) -> None:
        with self.assertRaises(ValueError):
            ServiceCapabilityProvider("https://user:secret@example.test/invoke")


if __name__ == "__main__":
    unittest.main()
