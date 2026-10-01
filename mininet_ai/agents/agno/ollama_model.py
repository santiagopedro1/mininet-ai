"""Construct Ollama models from the supported declarative configuration."""

from __future__ import annotations

from agno.models.ollama import Ollama

from mininet_ai.sdk import AgentProviderError
from mininet_ai.specification.models import ModelConfiguration, validate_ollama_host


def create_ollama_model(
    configuration: ModelConfiguration, *, prompt_parsed: bool = False
) -> Ollama:
    unknown = sorted(set(configuration.parameters) - {"host"})
    if unknown:
        raise AgentProviderError(
            "unsupported Ollama model parameters: " + ", ".join(unknown),
            code="agent.agno.model-parameters-unsupported",
        )
    host = None
    if "host" in configuration.parameters:
        try:
            host = validate_ollama_host(configuration.parameters["host"])
        except ValueError as error:
            raise AgentProviderError(
                str(error), code="agent.agno.ollama-host-invalid"
            ) from error
    return Ollama(
        id=configuration.name,
        host=host,
        supports_native_structured_outputs=not prompt_parsed,
    )
