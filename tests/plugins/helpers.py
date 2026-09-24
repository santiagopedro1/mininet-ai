from __future__ import annotations

from dataclasses import dataclass

from mininet_ai.plugins import ProviderKind, ProviderPlugin
from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    AgentResponse,
    ModelRequest,
    ModelResponse,
)


class RecordingAgent:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def invoke(self, context: AgentContext) -> AgentResponse:
        return AgentResponse(message=context.intent)


class RecordingCapability:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def execute(self, context: AgentContext, proposal: ActionProposal):
        return {"agent": context.agent_id, "target": proposal.target}


class RecordingModel:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(content=request.messages[-1].content)


def plugin(kind: ProviderKind) -> ProviderPlugin:
    providers = {
        ProviderKind.AGENT: RecordingAgent,
        ProviderKind.CAPABILITY: RecordingCapability,
        ProviderKind.MODEL: RecordingModel,
    }
    return ProviderPlugin(
        kind=kind,
        factory=lambda configuration: providers[kind](),
    )


@dataclass
class FakeEntryPoint:
    name: str
    group: str
    value: object
    error: Exception | None = None
    loaded: bool = False

    def load(self) -> object:
        self.loaded = True
        if self.error is not None:
            raise self.error
        return self.value
