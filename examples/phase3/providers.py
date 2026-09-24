"""Example capability plugins loaded by the Phase 3 acceptance test."""

from __future__ import annotations

from mininet_ai.plugins import ProviderKind, ProviderPlugin
from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    CapabilityOutcome,
    CapabilityProviderError,
)


class TopologyTelemetryProvider:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityOutcome:
        scoped = context.observations.get("topology.neighbors")
        if not isinstance(scoped, dict):
            raise CapabilityProviderError(
                "scoped topology observations are unavailable",
                code="example.telemetry.unavailable",
            )
        observation = scoped.get(proposal.target)
        if not isinstance(observation, dict):
            raise CapabilityProviderError(
                f"target {proposal.target!r} has no topology observation",
                code="example.telemetry.target-missing",
            )
        return CapabilityOutcome(
            output={
                "target": proposal.target,
                "observation": observation,
            }
        )


class ExampleActionProvider:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityOutcome:
        del context
        return CapabilityOutcome(
            changed=True,
            output={
                "applied": proposal.arguments["enabled"],
                "target": proposal.target,
            },
        )


telemetry_plugin = ProviderPlugin(
    kind=ProviderKind.CAPABILITY,
    factory=lambda definition: TopologyTelemetryProvider(),
)
action_plugin = ProviderPlugin(
    kind=ProviderKind.CAPABILITY,
    factory=lambda definition: ExampleActionProvider(),
)
