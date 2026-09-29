"""Agno factory for Ollama models that need prompt-parsed responses."""

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


def create_prompt_parsed_agent(definition: AgentExecutionDefinition) -> Agent:
    """Create an Ollama agent without its native recursive JSON schema mode."""

    configuration = definition.blueprint.model
    if configuration is None or configuration.provider != "ollama":
        raise ValueError("the prompt-parsed Ollama factory requires an Ollama model")
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
