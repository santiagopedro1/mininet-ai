from __future__ import annotations

import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    AgentInvocationResult,
    AgentProvider,
    AgentResponse,
    AgentRuntimeIssue,
    CapabilityOutcome,
    CapabilityProvider,
    CapabilityProviderError,
    InvocationStatus,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelRole,
    TokenUsage,
)
from mininet_ai.specification.models import AttachmentLayer, ResourceKind
from mininet_ai.substrates import ActionStatus


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
        intent="Repair forwarding safely",
        invokedAt=NOW,
    )


class RecordingAgent:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def invoke(self, invocation: AgentContext) -> AgentResponse:
        return AgentResponse(message=invocation.intent)


class RecordingModel:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content=request.messages[-1].content)


class RecordingCapability:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def execute(
        self,
        invocation: AgentContext,
        proposal: ActionProposal,
    ):
        return {"agent": invocation.agent_id, "target": proposal.target}


class AgentRuntimeContractTests(unittest.TestCase):
    def test_provider_adapters_satisfy_the_runtime_protocols(self) -> None:
        self.assertIsInstance(RecordingAgent(), AgentProvider)
        self.assertIsInstance(RecordingModel(), ModelProvider)
        self.assertIsInstance(RecordingCapability(), CapabilityProvider)

    def test_contract_models_round_trip_with_public_aliases(self) -> None:
        invocation = context()
        proposal = ActionProposal(
            id="proposal-1",
            capability="openflow.flow.install",
            target="s1",
            arguments={"match": "ip", "actions": "normal"},
            reason="Restore connectivity",
            timeoutSeconds=5,
        )
        response = AgentResponse(
            message="A forwarding update is required.",
            proposals=(proposal,),
        )
        result = AgentInvocationResult(
            invocationId=invocation.invocation_id,
            runId=invocation.run_id,
            agentId=invocation.agent_id,
            status=InvocationStatus.SUCCEEDED,
            startedAt=NOW,
            completedAt=NOW,
            response=response,
        )

        restored = AgentInvocationResult.model_validate_json(
            result.model_dump_json(by_alias=True)
        )

        self.assertEqual(restored, result)
        payload = result.model_dump(by_alias=True, mode="json")
        self.assertEqual(payload["invocationId"], "invoke-1")
        self.assertEqual(
            payload["response"]["proposals"][0]["timeoutSeconds"],
            5,
        )
        self.assertEqual(
            CapabilityOutcome(changed=True, output={"installed": True})
            .model_dump(mode="json"),
            {"changed": True, "output": {"installed": True}},
        )

    def test_model_contract_normalizes_structured_output_and_usage(self) -> None:
        request = ModelRequest(
            provider="mock",
            model="deterministic",
            messages=(ModelMessage(role=ModelRole.USER, content="inspect s1"),),
            responseSchema={"type": "object"},
            timeoutSeconds=2,
        )
        response = ModelResponse(
            structuredOutput={"state": "healthy"},
            usage=TokenUsage(inputTokens=2, outputTokens=1, totalTokens=3),
        )

        self.assertEqual(request.response_schema, {"type": "object"})
        self.assertEqual(response.usage.total_tokens, 3)

    def test_invalid_or_ambiguous_outcomes_are_rejected(self) -> None:
        cases = (
            lambda: AgentResponse(),
            lambda: ModelResponse(),
            lambda: TokenUsage(inputTokens=2, outputTokens=2, totalTokens=3),
            lambda: ActionProposal(
                id="proposal-1",
                capability="custom.invalid",
                target="s1",
                arguments={"value": object()},
            ),
            lambda: AgentInvocationResult(
                invocationId="invoke-1",
                runId="run-1",
                agentId="router@s1",
                status=InvocationStatus.FAILED,
                startedAt=NOW,
                completedAt=NOW,
            ),
            lambda: AgentInvocationResult(
                invocationId="invoke-1",
                runId="run-1",
                agentId="router@s1",
                status=InvocationStatus.SUCCEEDED,
                startedAt=NOW,
                completedAt=NOW,
                response=AgentResponse(message="done"),
                issue=AgentRuntimeIssue(code="unexpected", message="not allowed"),
            ),
        )

        for build in cases:
            with self.subTest(build=build):
                with self.assertRaises(ValidationError):
                    build()

    def test_context_rejects_scope_ambiguity(self) -> None:
        payload = context().model_dump(by_alias=True)
        payload["targets"] = ("s1", "s1")

        with self.assertRaisesRegex(ValidationError, "targets must be unique"):
            AgentContext.model_validate(payload)

    def test_provider_errors_require_a_failure_status_and_identity(self) -> None:
        cases = (
            {"message": "", "code": "capability.failed"},
            {"message": "failed", "code": ""},
            {
                "message": "failed",
                "code": "capability.failed",
                "status": ActionStatus.SUCCEEDED,
            },
        )

        for parameters in cases:
            with self.subTest(parameters=parameters):
                with self.assertRaises(ValueError):
                    CapabilityProviderError(**parameters)


if __name__ == "__main__":
    unittest.main()
