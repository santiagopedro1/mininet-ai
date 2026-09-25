"""Construct and invoke Agno agents without granting network authority."""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping
from typing import Protocol, cast

from agno.agent import Agent
from agno.models.base import Model
from agno.run.agent import RunOutput
from pydantic import ValidationError

from mininet_ai.sdk.catalog import AgentExecutionDefinition
from mininet_ai.sdk.contracts import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    AgentContext,
    AgentProviderError,
    AgentResponse,
)
from mininet_ai.specification.models import ModelConfiguration


_ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$"
)
_SAFE_OUTPUT_INSTRUCTIONS = (
    "Return a response matching the required schema. Action proposals must use "
    "only capabilities and targets present in the supplied scoped context. "
    "Do not execute network changes directly."
)


class ModelResolver(Protocol):
    """Resolve one declarative blueprint model into an Agno model."""

    def __call__(self, configuration: ModelConfiguration) -> Model | str: ...


AgnoFactoryEntrypoint = Callable[[AgentExecutionDefinition], Agent]


def _default_model_resolver(configuration: ModelConfiguration) -> str:
    if configuration.parameters:
        raise AgentProviderError(
            "declarative Agno models do not accept legacy model parameters; "
            "use an Agno Python factory for provider-specific configuration",
            code="agent.agno.model-parameters-unsupported",
        )
    return f"{configuration.provider}:{configuration.name}"


def _validate_context(
    definition: AgentExecutionDefinition,
    context: AgentContext,
) -> None:
    instance = definition.instance
    attachment = instance.attachment
    if (
        context.agent_id != instance.id
        or context.deployment != instance.deployment
        or context.layer != attachment.layer
        or context.custom_layer != attachment.custom_layer
        or context.target_kind != attachment.target_kind
        or set(context.targets) != set(attachment.targets)
        or set(context.capabilities) != set(instance.capabilities)
        or not set(context.observations).issubset(instance.observes)
        or context.priority != instance.priority
    ):
        raise AgentProviderError(
            f"context does not match compiled agent {instance.id!r}",
            code="agent.context.invalid",
        )


def _normalize_output(output: RunOutput) -> AgentResponse:
    content = output.content
    if isinstance(content, AgentResponse):
        return content
    if isinstance(content, Mapping):
        try:
            return AgentResponse.model_validate(dict(content))
        except ValidationError as error:
            raise AgentProviderError(
                f"Agno returned an invalid agent response: {error}",
                code="agent.agno.response-invalid",
            ) from error
    raise AgentProviderError(
        f"Agno returned {type(content).__name__}, not AgentResponse",
        code="agent.agno.response-invalid",
    )


class AgnoAgentFactory:
    """Build an Agno agent from one compiled Mininet agent definition."""

    def __init__(self, *, model_resolver: ModelResolver | None = None) -> None:
        self._model_resolver = model_resolver or _default_model_resolver

    def create(self, definition: AgentExecutionDefinition) -> Agent:
        implementation = definition.blueprint.implementation
        if implementation.type == "declarative":
            return self._declarative(definition)
        return self._python(definition)

    def _declarative(self, definition: AgentExecutionDefinition) -> Agent:
        blueprint = definition.blueprint
        model_configuration = blueprint.model
        if model_configuration is None:
            raise AgentProviderError(
                f"declarative blueprint {blueprint.metadata.name!r} has no model",
                code="agent.configuration.model-missing",
            )
        try:
            model = self._model_resolver(model_configuration)
            return Agent(
                id=definition.instance.id,
                name=definition.instance.id,
                description=blueprint.metadata.description,
                model=model,
                instructions=[
                    blueprint.reasoning.instructions
                    or "Inspect the supplied context and propose a safe response.",
                    _SAFE_OUTPUT_INSTRUCTIONS,
                ],
                input_schema=AgentContext,
                output_schema=AgentResponse,
                telemetry=False,
            )
        except AgentProviderError:
            raise
        except Exception as error:
            raise AgentProviderError(
                f"could not construct Agno agent {definition.instance.id!r}: "
                f"{error}",
                code="agent.agno.construction-failed",
            ) from error

    def _python(self, definition: AgentExecutionDefinition) -> Agent:
        entrypoint = definition.blueprint.implementation.entrypoint
        if entrypoint is None or _ENTRYPOINT_PATTERN.fullmatch(entrypoint) is None:
            raise AgentProviderError(
                f"Agno agent entrypoint {entrypoint!r} is invalid",
                code="agent.agno.entrypoint-invalid",
            )
        module_name, attribute_path = entrypoint.split(":", 1)
        try:
            value = importlib.import_module(module_name)
            for attribute in attribute_path.split("."):
                value = getattr(value, attribute)
        except Exception as error:
            raise AgentProviderError(
                f"could not load Agno agent entrypoint {entrypoint!r}: {error}",
                code="agent.agno.load-failed",
            ) from error

        if isinstance(value, Agent):
            return value
        if not callable(value):
            raise AgentProviderError(
                f"Agno agent entrypoint {entrypoint!r} is not an Agent or factory",
                code="agent.agno.entrypoint-invalid",
            )
        try:
            agent = cast(AgnoFactoryEntrypoint, value)(definition)
        except Exception as error:
            raise AgentProviderError(
                f"Agno agent factory {entrypoint!r} failed: {error}",
                code="agent.agno.factory-failed",
            ) from error
        if not isinstance(agent, Agent):
            raise AgentProviderError(
                f"Agno agent factory {entrypoint!r} returned "
                f"{type(agent).__name__}, not Agent",
                code="agent.agno.factory-invalid",
            )
        return agent


class AgnoAgentProvider:
    """Invoke one Agno agent behind the transitional provider interface."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        definition: AgentExecutionDefinition,
        *,
        factory: AgnoAgentFactory | None = None,
    ) -> None:
        self._definition = definition
        self._agent = (factory or AgnoAgentFactory()).create(definition)

    def invoke(self, context: AgentContext) -> AgentResponse:
        _validate_context(self._definition, context)
        try:
            output = self._agent.run(
                input=context,
                run_id=context.invocation_id,
                output_schema=AgentResponse,
            )
        except TimeoutError:
            raise
        except Exception as error:
            raise AgentProviderError(
                f"Agno agent invocation failed: {error}",
                code="agent.agno.invocation-failed",
            ) from error
        if not isinstance(output, RunOutput):
            raise AgentProviderError(
                "Agno returned a streaming iterator for a bounded invocation",
                code="agent.agno.streaming-unsupported",
            )
        return _normalize_output(output)
