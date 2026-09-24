"""Public SDK contracts for user-defined agents and capabilities."""

from mininet_ai.errors import AgentRuntimeError
from mininet_ai.sdk.catalog import AgentExecutionDefinition, ExecutionCatalog
from mininet_ai.sdk.contracts import (
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

__all__ = [
    "AGENT_RUNTIME_CONTRACT_VERSION",
    "ActionProposal",
    "AgentContext",
    "AgentExecutionDefinition",
    "AgentInvocationResult",
    "AgentProvider",
    "AgentResponse",
    "AgentRuntimeIssue",
    "AgentRuntimeError",
    "CapabilityOutcome",
    "CapabilityProvider",
    "CapabilityProviderError",
    "ExecutionCatalog",
    "InvocationStatus",
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "TokenUsage",
]
