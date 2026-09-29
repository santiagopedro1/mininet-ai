"""Agno factory for reliable Qwen structured responses in Phase 5."""

from __future__ import annotations

from agno.agent import Agent
from agno.models.ollama import Ollama

from mininet_ai.sdk import AgentContext, AgentExecutionDefinition, AgentResponse


_SAFE_OUTPUT_INSTRUCTIONS = (
    "Return a response matching the required schema. Action proposals must use "
    "only capabilities and targets present in the supplied scoped context. "
    "Delegation proposals must use only coordination.allowedDestinations. "
    "Do not execute network changes directly."
)


def create_agent(definition: AgentExecutionDefinition) -> Agent:
    """Create an Ollama agent using prompt-parsed structured output.

    Qwen 2.5 expands opaque OpenFlow argument strings when Ollama's native
    recursive JSON schema is used. Agno's prompt-parsed mode preserves those
    strings while still validating the final response as ``AgentResponse``.
    """

    configuration = definition.blueprint.model
    if configuration is None or configuration.provider != "ollama":
        raise ValueError("the Phase 5 Ollama factory requires an Ollama model")
    instance = definition.instance
    return Agent(
        id=instance.id,
        name=instance.id,
        description=definition.blueprint.metadata.description,
        model=Ollama(
            id=configuration.name,
            supports_native_structured_outputs=False,
        ),
        instructions=[
            definition.blueprint.reasoning.instructions
            or "Inspect the supplied context and propose a safe response.",
            _SAFE_OUTPUT_INSTRUCTIONS,
        ],
        input_schema=AgentContext,
        output_schema=AgentResponse,
        structured_outputs=False,
        telemetry=False,
    )
