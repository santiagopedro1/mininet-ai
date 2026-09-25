"""Declarative and in-process Python agent provider adapters."""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Mapping
from typing import cast

from pydantic import JsonValue, ValidationError

from mininet_ai.durations import duration_seconds
from mininet_ai.sdk.catalog import AgentExecutionDefinition
from mininet_ai.sdk.contracts import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    AgentContext,
    AgentProviderError,
    AgentResponse,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelRole,
)


_ENTRYPOINT_PATTERN = re.compile(
    r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$"
)
_DEFAULT_TIMEOUT_SECONDS = 30.0


def _timeout_seconds(value: str | None) -> float:
    if value is None:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = duration_seconds(value)
    except ValueError as error:
        raise AgentProviderError(
            f"invalid declarative agent timeout {value!r}",
            code="agent.configuration.invalid-timeout",
        ) from error
    if timeout <= 0:
        raise AgentProviderError(
            "declarative agent timeout must be positive",
            code="agent.configuration.invalid-timeout",
        )
    return timeout


def _normalize_response(value: object, *, source: str) -> AgentResponse:
    if isinstance(value, AgentResponse):
        return value
    if not isinstance(value, Mapping):
        raise AgentProviderError(
            f"{source} returned a non-object response",
            code="agent.response.invalid",
        )
    try:
        return AgentResponse.model_validate(dict(value))
    except ValidationError as error:
        raise AgentProviderError(
            f"{source} returned an invalid response: {error}",
            code="agent.response.invalid",
        ) from error


def _response_from_model(response: object) -> AgentResponse:
    structured = getattr(response, "structured_output", None)
    if structured is not None:
        return _normalize_response(structured, source="model provider")
    content = getattr(response, "content", None)
    if not isinstance(content, str):
        raise AgentProviderError(
            "model provider returned no agent response",
            code="agent.response.invalid",
        )
    try:
        value = json.loads(content, parse_constant=_reject_json_constant)
    except ValueError as error:
        raise AgentProviderError(
            f"model provider returned invalid agent JSON: {error}",
            code="agent.response.invalid-json",
        ) from error
    return _normalize_response(value, source="model provider")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


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


class DeclarativeAgentProvider:
    """Turn a compiled declarative blueprint into one structured model call."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        definition: AgentExecutionDefinition,
        model_provider: ModelProvider,
    ) -> None:
        blueprint = definition.blueprint
        if blueprint.implementation.type != "declarative":
            raise AgentProviderError(
                f"blueprint {blueprint.metadata.name!r} is not declarative",
                code="agent.configuration.implementation-mismatch",
            )
        if blueprint.model is None:
            raise AgentProviderError(
                f"declarative blueprint {blueprint.metadata.name!r} has no model",
                code="agent.configuration.model-missing",
            )
        self._definition = definition
        self._model_provider = model_provider
        self._timeout_seconds = _timeout_seconds(blueprint.reasoning.timeout)

    def invoke(self, context: AgentContext) -> AgentResponse:
        _validate_context(self._definition, context)
        blueprint = self._definition.blueprint
        model = blueprint.model
        if model is None:  # Guarded during construction; keeps type narrowing local.
            raise AssertionError("declarative model configuration disappeared")
        instructions = blueprint.reasoning.instructions or (
            "Inspect the supplied scoped context and return a safe response."
        )
        system_prompt = (
            instructions
            + "\nReturn only one JSON object matching the supplied response schema. "
            "Action proposals must use only listed capabilities and targets."
        )
        context_payload = json.dumps(
            context.model_dump(mode="json", by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            request = ModelRequest(
                provider=model.provider,
                model=model.name,
                messages=(
                    ModelMessage(role=ModelRole.SYSTEM, content=system_prompt),
                    ModelMessage(role=ModelRole.USER, content=context_payload),
                ),
                parameters=model.parameters,
                responseSchema=AgentResponse.model_json_schema(by_alias=True),
                timeoutSeconds=self._timeout_seconds,
            )
        except ValidationError as error:
            raise AgentProviderError(
                f"declarative model configuration is invalid: {error}",
                code="agent.configuration.model-invalid",
            ) from error
        return _response_from_model(self._model_provider.generate(request))

PythonEntrypoint = Callable[[AgentContext], AgentResponse | Mapping[str, JsonValue]]


class PythonAgentProvider:
    """Invoke one explicitly configured, trusted Python entrypoint in-process."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(self, definition: AgentExecutionDefinition) -> None:
        blueprint = definition.blueprint
        if blueprint.implementation.type != "python":
            raise AgentProviderError(
                f"blueprint {blueprint.metadata.name!r} is not a Python agent",
                code="agent.configuration.implementation-mismatch",
            )
        entrypoint = blueprint.implementation.entrypoint
        if entrypoint is None or _ENTRYPOINT_PATTERN.fullmatch(entrypoint) is None:
            raise AgentProviderError(
                f"Python agent entrypoint {entrypoint!r} is invalid",
                code="agent.python.entrypoint-invalid",
            )
        self._definition = definition
        self._entrypoint = self._load(entrypoint)

    def invoke(self, context: AgentContext) -> AgentResponse:
        _validate_context(self._definition, context)
        try:
            response = self._entrypoint(context)
        except TimeoutError:
            raise
        except Exception as error:
            raise AgentProviderError(
                f"Python agent entrypoint failed: {error}",
                code="agent.python.invocation-failed",
            ) from error
        return _normalize_response(response, source="Python agent entrypoint")

    @staticmethod
    def _load(entrypoint: str) -> PythonEntrypoint:
        module_name, attribute_path = entrypoint.split(":", 1)
        try:
            value = importlib.import_module(module_name)
            for attribute in attribute_path.split("."):
                value = getattr(value, attribute)
        except Exception as error:
            raise AgentProviderError(
                f"could not load Python agent entrypoint {entrypoint!r}: {error}",
                code="agent.python.load-failed",
            ) from error
        if not callable(value):
            raise AgentProviderError(
                f"Python agent entrypoint {entrypoint!r} is not callable",
                code="agent.python.entrypoint-invalid",
            )
        return cast(PythonEntrypoint, value)
