"""Construct Ollama models from the supported declarative configuration."""

from __future__ import annotations

from agno.models.ollama import Ollama

from mininet_ai.sdk import AgentProviderError
from mininet_ai.specification.models import ModelConfiguration, validate_ollama_host


def create_ollama_model(
    configuration: ModelConfiguration, *, prompt_parsed: bool = False
) -> Ollama:
    unknown = sorted(set(configuration.parameters) - {"host", "think"})
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
    request_params = None
    if "think" in configuration.parameters:
        think = configuration.parameters["think"]
        if not isinstance(think, bool):
            raise AgentProviderError(
                "Ollama model parameter 'think' must be a boolean",
                code="agent.agno.ollama-think-invalid",
            )
        request_params = {"think": think}
    return Ollama(
        id=configuration.name,
        host=host,
        request_params=request_params,
        supports_native_structured_outputs=not prompt_parsed,
    )
