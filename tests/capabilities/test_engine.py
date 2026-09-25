from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any, cast

from mininet_ai.capabilities import CapabilityEngine
from mininet_ai.compiler import compile_experiment
from mininet_ai.plugins import ProviderKind, ProviderPlugin, ProviderRegistries
from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    CapabilityOutcome,
    CapabilityProviderError,
    ExecutionCatalog,
)
from mininet_ai.specification.models import AttachmentLayer
from mininet_ai.substrates import ActionResult, ActionStatus, RuntimeIssue
from tests.compiler.helpers import EXAMPLE

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class RecordingProvider:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(self, result: object | None = None) -> None:
        self.result = result or CapabilityOutcome(
            changed=True,
            output={"installed": True},
        )
        self.calls: list[tuple[AgentContext, ActionProposal]] = []

    def execute(self, context: AgentContext, proposal: ActionProposal):
        self.calls.append((context, proposal))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class CapabilityEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = compile_experiment(EXAMPLE)
        self.catalog = ExecutionCatalog(self.plan)
        self.definition = self.catalog.resolve("switch-router@s1")
        self.provider = RecordingProvider()
        self.registries = ProviderRegistries()
        self.registries.capabilities.register(
            "fake.openflow",
            cast(
                Any,
                ProviderPlugin(
                    kind=ProviderKind.CAPABILITY,
                    factory=lambda capability: self.provider,
                ),
            ),
        )
        self.engine = CapabilityEngine(
            self.catalog,
            self.registries.capabilities,
            clock=lambda: NOW,
        )

    def context(self, **updates: Any) -> AgentContext:
        attachment = self.definition.instance.attachment
        values = {
            "invocationId": "invoke-1",
            "runId": "run-1",
            "agentId": self.definition.instance.id,
            "deployment": self.definition.instance.deployment,
            "layer": attachment.layer,
            "customLayer": attachment.custom_layer,
            "targetKind": attachment.target_kind,
            "targets": attachment.targets,
            "capabilities": self.definition.instance.capabilities,
            "intent": "repair forwarding",
            "invokedAt": NOW,
        }
        values.update(updates)
        return AgentContext.model_validate(values)

    @staticmethod
    def proposal(**updates: Any) -> ActionProposal:
        values = {
            "id": "proposal-1",
            "capability": "openflow.flow.install",
            "target": "s1",
            "arguments": {"match": "ip", "actions": "normal"},
        }
        values.update(updates)
        return ActionProposal.model_validate(values)

    def assert_issue_code(self, result: ActionResult, code: str) -> None:
        self.assertIsNotNone(result.issue)
        assert result.issue is not None
        self.assertEqual(result.issue.code, code)

    def test_authorizes_validates_and_executes_an_assigned_capability(self) -> None:
        context = self.context()
        proposal = self.proposal()

        result = self.engine.execute(context, proposal)

        self.assertEqual(result.status, ActionStatus.SUCCEEDED)
        self.assertTrue(result.changed)
        self.assertEqual(result.output, {"installed": True})
        self.assertEqual(result.completed_at, NOW)
        self.assertEqual(self.provider.calls, [(context, proposal)])

    def test_compiled_action_timeout_caps_agent_proposal(self) -> None:
        agents = tuple(
            agent.model_copy(
                update={
                    "execution": agent.execution.model_copy(
                        update={"action_timeout": "2s"}
                    )
                }
            )
            if agent.id == "switch-router@s1"
            else agent
            for agent in self.plan.agents
        )
        engine = CapabilityEngine(
            ExecutionCatalog(self.plan.model_copy(update={"agents": agents})),
            self.registries.capabilities,
            clock=lambda: NOW,
        )

        result = engine.execute(
            self.context(),
            self.proposal(timeoutSeconds=20),
        )

        self.assertEqual(result.status, ActionStatus.SUCCEEDED)
        self.assertEqual(self.provider.calls[0][1].timeout_seconds, 2)

    def test_rejects_unassigned_out_of_scope_and_invalid_input(self) -> None:
        cases = (
            (
                self.proposal(capability="host.process.start"),
                "capability.not-assigned",
            ),
            (
                self.proposal(target="s2"),
                "capability.target.out-of-scope",
            ),
            (
                self.proposal(arguments={"match": "ip"}),
                "capability.input.invalid",
            ),
        )

        for proposal, code in cases:
            with self.subTest(code=code):
                result = self.engine.execute(self.context(), proposal)
                self.assertEqual(result.status, ActionStatus.REJECTED)
                self.assert_issue_code(result, code)

        self.assertEqual(self.provider.calls, [])

    def test_rejects_context_that_does_not_match_compiled_scope(self) -> None:
        cases = (
            self.context(deployment="forged-deployment"),
            self.context(layer=AttachmentLayer.CUSTOM, customLayer="data"),
        )

        for context in cases:
            with self.subTest(layer=context.layer):
                result = self.engine.execute(context, self.proposal())

                self.assertEqual(result.status, ActionStatus.REJECTED)
                self.assert_issue_code(result, "capability.context.invalid")
        self.assertEqual(self.provider.calls, [])

    def test_unknown_agent_identity_is_rejected(self) -> None:
        result = self.engine.execute(
            self.context(agentId="missing"),
            self.proposal(),
        )

        self.assertEqual(result.status, ActionStatus.REJECTED)
        self.assert_issue_code(result, "agent.instance.unknown")
        self.assertEqual(self.provider.calls, [])

    def test_capability_target_kind_and_layer_are_enforced(self) -> None:
        cases = (
            ("targets", ["host"], "capability.target-kind.denied"),
            ("layers", ["control"], "capability.layer.denied"),
        )

        for field, value, expected_code in cases:
            with self.subTest(field=field):
                snapshot = dict(self.plan.snapshot)
                capabilities = [
                    dict(item) for item in snapshot["capabilityDefinitions"]
                ]
                capabilities[0][field] = value
                snapshot["capabilityDefinitions"] = capabilities
                catalog = ExecutionCatalog(
                    self.plan.model_copy(update={"snapshot": snapshot})
                )
                engine = CapabilityEngine(
                    catalog,
                    self.registries.capabilities,
                    clock=lambda: NOW,
                )

                result = engine.execute(self.context(), self.proposal())

                self.assertEqual(result.status, ActionStatus.REJECTED)
                self.assert_issue_code(result, expected_code)

        self.assertEqual(self.provider.calls, [])

    def test_missing_compiled_effect_is_rejected(self) -> None:
        agents = tuple(
            agent.model_copy(update={"privileges": ()})
            if agent.id == "switch-router@s1"
            else agent
            for agent in self.plan.agents
        )
        catalog = ExecutionCatalog(self.plan.model_copy(update={"agents": agents}))
        engine = CapabilityEngine(
            catalog,
            self.registries.capabilities,
            clock=lambda: NOW,
        )

        result = engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.REJECTED)
        self.assert_issue_code(result, "capability.effects.denied")

    def test_provider_failures_and_timeouts_are_normalized(self) -> None:
        cases = (
            (RuntimeError("provider crashed"), "capability.execution.failed"),
            (TimeoutError("deadline exceeded"), "capability.timeout"),
            (
                CapabilityProviderError(
                    "remote denied the request",
                    code="capability.remote.denied",
                    status=ActionStatus.REJECTED,
                ),
                "capability.remote.denied",
            ),
        )

        for failure, code in cases:
            with self.subTest(code=code):
                self.provider.result = failure
                result = self.engine.execute(self.context(), self.proposal())
                expected_status = (
                    ActionStatus.REJECTED
                    if isinstance(failure, CapabilityProviderError)
                    else ActionStatus.FAILED
                )
                self.assertEqual(result.status, expected_status)
                self.assert_issue_code(result, code)

    def test_successful_substrate_result_output_must_be_json(self) -> None:
        self.provider.result = ActionResult(
            run_id="run-1",
            request_id="proposal-1",
            status=ActionStatus.SUCCEEDED,
            completed_at=NOW,
            output={"invalid": object()},
        )

        result = self.engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assert_issue_code(result, "capability.output.invalid")

    def test_substrate_action_results_are_preserved(self) -> None:
        substrate_result = ActionResult(
            run_id="run-1",
            request_id="proposal-1",
            status=ActionStatus.REJECTED,
            completed_at=NOW,
            issue=RuntimeIssue(
                code="runtime.action.denied",
                message="substrate rejected the action",
                target="s1",
            ),
        )
        self.provider.result = substrate_result

        result = self.engine.execute(self.context(), self.proposal())

        self.assertIs(result, substrate_result)

    def test_provider_result_identity_must_match_the_proposal(self) -> None:
        self.provider.result = ActionResult(
            run_id="another-run",
            request_id="another-request",
            status=ActionStatus.SUCCEEDED,
            completed_at=NOW,
        )

        result = self.engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assert_issue_code(result, "capability.result.invalid-identity")

    def test_unknown_provider_is_a_typed_failure(self) -> None:
        engine = CapabilityEngine(
            self.catalog,
            ProviderRegistries().capabilities,
            clock=lambda: NOW,
        )

        result = engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assert_issue_code(result, "plugin.provider.unknown")

    def test_missing_provider_is_a_typed_failure(self) -> None:
        snapshot = dict(self.plan.snapshot)
        capabilities = [dict(item) for item in snapshot["capabilityDefinitions"]]
        capabilities[0]["provider"] = None
        snapshot["capabilityDefinitions"] = capabilities
        catalog = ExecutionCatalog(
            self.plan.model_copy(update={"snapshot": snapshot})
        )
        engine = CapabilityEngine(
            catalog,
            self.registries.capabilities,
            clock=lambda: NOW,
        )

        result = engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assert_issue_code(result, "capability.provider.missing")

    def test_provider_output_must_be_json_and_match_declared_schema(self) -> None:
        cases = (
            (object(), "capability.output.invalid"),
            ({"notJson": object()}, "capability.output.invalid"),
        )

        for output, code in cases:
            with self.subTest(output=output):
                self.provider.result = output
                result = self.engine.execute(self.context(), self.proposal())
                self.assertEqual(result.status, ActionStatus.FAILED)
                self.assert_issue_code(result, code)

    def test_declared_output_schema_is_enforced(self) -> None:
        snapshot = dict(self.plan.snapshot)
        capabilities = [dict(item) for item in snapshot["capabilityDefinitions"]]
        capabilities[0]["output-schema"] = {
            "type": "object",
            "required": ["installed"],
            "properties": {"installed": {"type": "boolean"}},
        }
        snapshot["capabilityDefinitions"] = capabilities
        catalog = ExecutionCatalog(self.plan.model_copy(update={"snapshot": snapshot}))
        engine = CapabilityEngine(
            catalog,
            self.registries.capabilities,
            clock=lambda: NOW,
        )
        self.provider.result = {"other": True}

        result = engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assert_issue_code(result, "capability.output.invalid")

    def test_invalid_declared_schema_is_a_typed_failure(self) -> None:
        snapshot = dict(self.plan.snapshot)
        capabilities = [dict(item) for item in snapshot["capabilityDefinitions"]]
        capabilities[0]["input-schema"] = {"type": "not-a-json-type"}
        snapshot["capabilityDefinitions"] = capabilities
        catalog = ExecutionCatalog(self.plan.model_copy(update={"snapshot": snapshot}))
        engine = CapabilityEngine(
            catalog,
            self.registries.capabilities,
            clock=lambda: NOW,
        )

        result = engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.FAILED)
        self.assert_issue_code(result, "capability.schema.invalid")
        self.assertEqual(self.provider.calls, [])

    def test_supported_json_schema_formats_are_enforced(self) -> None:
        snapshot = dict(self.plan.snapshot)
        capabilities = [dict(item) for item in snapshot["capabilityDefinitions"]]
        capabilities[0]["input-schema"] = {
            "type": "object",
            "required": ["match", "actions"],
            "properties": {"match": {"type": "string", "format": "ipv4"}},
        }
        snapshot["capabilityDefinitions"] = capabilities
        catalog = ExecutionCatalog(self.plan.model_copy(update={"snapshot": snapshot}))
        engine = CapabilityEngine(
            catalog,
            self.registries.capabilities,
            clock=lambda: NOW,
        )

        result = engine.execute(self.context(), self.proposal())

        self.assertEqual(result.status, ActionStatus.REJECTED)
        self.assert_issue_code(result, "capability.input.invalid")
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
