from __future__ import annotations

import json
import os
import stat
import unittest
from datetime import UTC, datetime
from tempfile import TemporaryDirectory

from mininet_ai.audit import (
    AUDIT_CONTRACT_VERSION,
    AuditError,
    AuditEventType,
    AuditRecorder,
    AuditedAgentProvider,
    AuditedCapabilityExecutor,
    AuditedModelProvider,
    JsonLinesAuditSink,
    MemoryAuditSink,
)
from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    AgentProviderError,
    AgentResponse,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    TokenUsage,
)
from mininet_ai.specification.models import AttachmentLayer, ResourceKind
from mininet_ai.substrates import ActionResult, ActionStatus, RuntimeIssue


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def context() -> AgentContext:
    return AgentContext(
        invocationId="invoke-1",
        runId="run-1",
        agentId="router@s1",
        deployment="router",
        layer=AttachmentLayer.DATA,
        targetKind=ResourceKind.SWITCH,
        targets=("s1",),
        capabilities=("openflow.flow.install",),
        observations={"topology.neighbors": {"s1": ["s2"]}},
        intent="repair forwarding",
        invokedAt=NOW,
    )


def proposal() -> ActionProposal:
    return ActionProposal(
        id="proposal-1",
        capability="openflow.flow.install",
        target="s1",
        arguments={"match": "ip", "actions": "normal"},
    )


class EchoAgent:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def invoke(self, invocation):
        return AgentResponse(message=invocation.intent)


class FailingAgent:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def invoke(self, invocation):
        raise AgentProviderError("script failed", code="agent.script.failed")


class EchoModel:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def generate(self, request):
        return ModelResponse(
            content=request.messages[-1].content,
            usage=TokenUsage(inputTokens=3, outputTokens=2, totalTokens=5),
            providerRequestId="model-request-1",
        )


class RejectingExecutor:
    def execute(self, invocation, action):
        return ActionResult(
            run_id=invocation.run_id,
            request_id=action.id,
            status=ActionStatus.REJECTED,
            completed_at=NOW,
            issue=RuntimeIssue(code="denied", message="not authorized"),
        )


class AuditTests(unittest.TestCase):
    def test_recorder_builds_correlated_versioned_events(self) -> None:
        sink = MemoryAuditSink()
        recorder = AuditRecorder(sink, clock=lambda: NOW)

        event = recorder.record(
            AuditEventType.AGENT_STARTED,
            context(),
            {"attempt": 1},
        )

        self.assertEqual(event.contract_version, AUDIT_CONTRACT_VERSION)
        self.assertEqual(event.recorded_at, NOW)
        self.assertEqual(event.run_id, "run-1")
        self.assertEqual(event.invocation_id, "invoke-1")
        self.assertEqual(event.agent_id, "router@s1")
        self.assertEqual(sink.events, (event,))
        with self.assertRaises(Exception):
            event.type = AuditEventType.AGENT_FAILED

    def test_json_lines_sink_appends_private_machine_readable_events(self) -> None:
        with TemporaryDirectory() as temporary:
            path = os.path.join(temporary, "audit.jsonl")
            sink = JsonLinesAuditSink(path, sync=True)
            recorder = AuditRecorder(sink, clock=lambda: NOW)

            recorder.record(AuditEventType.AGENT_STARTED, context())
            recorder.record(
                AuditEventType.AGENT_COMPLETED,
                context(),
                {"response": {"message": "done"}},
            )

            with open(path, encoding="utf-8") as stream:
                records = [json.loads(line) for line in stream]
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["contractVersion"], AUDIT_CONTRACT_VERSION)
            self.assertEqual(records[0]["recordedAt"], "2026-01-01T00:00:00Z")
            self.assertEqual(records[1]["data"]["response"]["message"], "done")
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_json_lines_sink_rejects_large_and_insecure_logs(self) -> None:
        with TemporaryDirectory() as temporary:
            large_path = os.path.join(temporary, "large.jsonl")
            recorder = AuditRecorder(
                JsonLinesAuditSink(large_path, max_event_bytes=32),
                clock=lambda: NOW,
            )
            with self.assertRaises(AuditError) as large:
                recorder.record(AuditEventType.AGENT_STARTED, context())
            self.assertEqual(large.exception.code, "audit.event.too-large")

            insecure_path = os.path.join(temporary, "insecure.jsonl")
            descriptor = os.open(insecure_path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(descriptor)
            os.chmod(insecure_path, 0o644)
            insecure = AuditRecorder(
                JsonLinesAuditSink(insecure_path),
                clock=lambda: NOW,
            )
            with self.assertRaises(AuditError) as unsafe:
                insecure.record(AuditEventType.AGENT_STARTED, context())
            self.assertEqual(unsafe.exception.code, "audit.path.unsafe")

    def test_agent_wrapper_records_success_and_typed_failure(self) -> None:
        sink = MemoryAuditSink()
        recorder = AuditRecorder(sink, clock=lambda: NOW)
        invocation = context()

        response = AuditedAgentProvider(EchoAgent(), recorder).invoke(invocation)

        self.assertEqual(response.message, invocation.intent)
        self.assertEqual(
            tuple(event.type for event in sink.events),
            (
                AuditEventType.AGENT_STARTED,
                AuditEventType.AGENT_COMPLETED,
            ),
        )
        self.assertEqual(
            sink.events[1].data["response"]["message"],
            invocation.intent,
        )

        failing_sink = MemoryAuditSink()
        failing = AuditedAgentProvider(
            FailingAgent(),
            AuditRecorder(failing_sink, clock=lambda: NOW),
        )
        with self.assertRaises(AgentProviderError):
            failing.invoke(invocation)
        self.assertEqual(failing_sink.events[-1].type, AuditEventType.AGENT_FAILED)
        self.assertEqual(
            failing_sink.events[-1].data["code"],
            "agent.script.failed",
        )

    def test_model_wrapper_records_prompts_responses_and_token_usage(self) -> None:
        sink = MemoryAuditSink()
        recorder = AuditRecorder(sink, clock=lambda: NOW)
        provider = AuditedModelProvider(EchoModel(), recorder, context())
        request = ModelRequest(
            provider="mock",
            model="deterministic",
            messages=(ModelMessage(role=ModelRole.USER, content="inspect s1"),),
        )

        response = provider.generate(request)

        self.assertEqual(response.usage.total_tokens, 5)
        self.assertEqual(
            tuple(event.type for event in sink.events),
            (
                AuditEventType.MODEL_STARTED,
                AuditEventType.MODEL_COMPLETED,
            ),
        )
        self.assertEqual(
            sink.events[0].data["request"]["messages"][0]["content"],
            "inspect s1",
        )
        usage = sink.events[1].data["response"]["usage"]
        self.assertEqual(usage["totalTokens"], 5)

    def test_capability_wrapper_records_proposals_and_typed_results(self) -> None:
        sink = MemoryAuditSink()
        recorder = AuditRecorder(sink, clock=lambda: NOW)
        executor = AuditedCapabilityExecutor(RejectingExecutor(), recorder)

        result = executor.execute(context(), proposal())

        self.assertEqual(result.status, ActionStatus.REJECTED)
        self.assertEqual(
            tuple(event.type for event in sink.events),
            (
                AuditEventType.CAPABILITY_STARTED,
                AuditEventType.CAPABILITY_COMPLETED,
            ),
        )
        self.assertEqual(
            sink.events[0].data["proposal"]["capability"],
            "openflow.flow.install",
        )
        self.assertEqual(
            sink.events[1].data["result"]["status"],
            "rejected",
        )


if __name__ == "__main__":
    unittest.main()
