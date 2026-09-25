"""Construct and invoke Agno agents without granting network authority."""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping
from importlib.metadata import version
from typing import Protocol, cast

from agno.agent import Agent
from agno.db.base import BaseDb
from agno.metrics import ModelMetrics, RunMetrics
from agno.models.base import Model
from agno.run.agent import RunOutput
from pydantic import JsonValue, TypeAdapter, ValidationError

from mininet_ai.agents.agno.contracts import (
    AgentRunMetrics,
    AgnoExecutionResult,
    AgnoMemorySettings,
    ModelRunMetrics,
)
from mininet_ai.agents.agno.models import DeterministicAgnoModel
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
    "Shared-state updates must use only sharedState.allowedScopes. "
    "Do not execute network changes directly."
)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_AGNO_VERSION = version("agno")


class ModelResolver(Protocol):
    """Resolve one declarative blueprint model into an Agno model."""

    def __call__(self, configuration: ModelConfiguration) -> Model | str: ...


AgnoFactoryEntrypoint = Callable[[AgentExecutionDefinition], Agent]


def _default_model_resolver(configuration: ModelConfiguration) -> Model | str:
    if configuration.provider == "mock":
        allowed = {"response", "usage"}
        unknown = sorted(set(configuration.parameters) - allowed)
        if unknown:
            raise AgentProviderError(
                "unsupported deterministic Agno model parameters: "
                + ", ".join(unknown),
                code="agent.agno.model-parameters-unsupported",
            )
        if "response" not in configuration.parameters:
            raise AgentProviderError(
                "the deterministic Agno model requires parameters.response",
                code="agent.agno.deterministic-response-missing",
            )
        usage = configuration.parameters.get("usage", {})
        if not isinstance(usage, Mapping):
            raise AgentProviderError(
                "deterministic Agno parameters.usage must be an object",
                code="agent.agno.deterministic-usage-invalid",
            )
        return DeterministicAgnoModel(
            configuration.parameters["response"],
            usage=usage,
        )
    if configuration.parameters:
        raise AgentProviderError(
            "declarative Agno models do not accept legacy model parameters; "
            "use an Agno Python factory for provider-specific configuration",
            code="agent.agno.model-parameters-unsupported",
        )
    return f"{configuration.provider}:{configuration.name}"


def _json_object(value: object, *, field: str) -> dict[str, JsonValue]:
    if value is None:
        return {}
    try:
        return _JSON_OBJECT.validate_python(value)
    except ValidationError as error:
        raise AgentProviderError(
            f"Agno returned non-JSON {field}: {error}",
            code="agent.agno.metrics-invalid",
        ) from error


def _model_metrics(role: str, metrics: ModelMetrics) -> ModelRunMetrics:
    return ModelRunMetrics(
        role=role,
        model=metrics.id,
        provider=metrics.provider,
        inputTokens=metrics.input_tokens,
        outputTokens=metrics.output_tokens,
        totalTokens=metrics.total_tokens,
        audioInputTokens=metrics.audio_input_tokens,
        audioOutputTokens=metrics.audio_output_tokens,
        audioTotalTokens=metrics.audio_total_tokens,
        cacheReadTokens=metrics.cache_read_tokens,
        cacheWriteTokens=metrics.cache_write_tokens,
        reasoningTokens=metrics.reasoning_tokens,
        cost=metrics.cost,
        providerMetrics=_json_object(
            metrics.provider_metrics,
            field="model provider metrics",
        ),
    )


def _run_metrics(metrics: RunMetrics | None) -> AgentRunMetrics:
    if metrics is None:
        return AgentRunMetrics()
    models = tuple(
        _model_metrics(role, model_metrics)
        for role in sorted(metrics.details or {})
        for model_metrics in (metrics.details or {})[role]
    )
    return AgentRunMetrics(
        inputTokens=metrics.input_tokens,
        outputTokens=metrics.output_tokens,
        totalTokens=metrics.total_tokens,
        audioInputTokens=metrics.audio_input_tokens,
        audioOutputTokens=metrics.audio_output_tokens,
        audioTotalTokens=metrics.audio_total_tokens,
        cacheReadTokens=metrics.cache_read_tokens,
        cacheWriteTokens=metrics.cache_write_tokens,
        reasoningTokens=metrics.reasoning_tokens,
        cost=metrics.cost,
        durationSeconds=metrics.duration,
        timeToFirstTokenSeconds=metrics.time_to_first_token,
        models=models,
        additional=_json_object(
            metrics.additional_metrics,
            field="additional metrics",
        ),
    )


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

    def __init__(
        self,
        *,
        model_resolver: ModelResolver | None = None,
        db: BaseDb | None = None,
    ) -> None:
        self._model_resolver = model_resolver or _default_model_resolver
        self._db = db

    def create(self, definition: AgentExecutionDefinition) -> Agent:
        implementation = definition.blueprint.implementation
        if implementation.type == "declarative":
            agent = self._declarative(definition)
        else:
            agent = self._python(definition)
        return self._configure(agent, definition)

    def _configure(
        self,
        agent: Agent,
        definition: AgentExecutionDefinition,
    ) -> Agent:
        memory = definition.blueprint.memory
        uses_persistent_memory = any(
            item is not None
            for item in (memory.local, memory.conversation, memory.learned)
        )
        if uses_persistent_memory and self._db is None:
            raise AgentProviderError(
                "Agno memory requires a configured session database",
                code="agent.agno.database-required",
            )
        if self._db is not None:
            if agent.db is not None and agent.db is not self._db:
                raise AgentProviderError(
                    "Python Agno agent configured a different session database",
                    code="agent.agno.database-conflict",
                )
            agent.db = self._db
        agent.session_id = None
        agent.user_id = None
        local = memory.local
        conversation = memory.conversation
        learned = memory.learned
        agent.session_state = (agent.session_state or {}) if local else None
        agent.add_session_state_to_context = local is not None
        agent.enable_agentic_state = local is not None
        agent.add_history_to_context = conversation is not None
        agent.num_history_messages = (
            conversation.max_messages if conversation is not None else None
        )
        agent.enable_session_summaries = bool(
            conversation is not None and conversation.summaries
        )
        agent.add_session_summary_to_context = bool(
            conversation is not None and conversation.summaries
        )
        agent.update_memory_on_run = bool(
            learned is not None and learned.mode == "automatic"
        )
        agent.enable_agentic_memory = bool(
            learned is not None and learned.mode == "agentic"
        )
        agent.telemetry = False
        return agent

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
        return self.run(context).response

    def run(self, context: AgentContext) -> AgnoExecutionResult:
        """Execute Agno and return only normalized, serializable values."""

        _validate_context(self._definition, context)
        session_id = f"{context.run_id}:{context.agent_id}"
        learned = self._definition.blueprint.memory.learned
        user_id = None
        if learned is not None:
            user_id = (
                context.agent_id if learned.scope == "agent" else session_id
            )
        try:
            output = self._agent.run(
                input=context,
                run_id=context.invocation_id,
                session_id=session_id,
                user_id=user_id,
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
        agno_run_id = output.run_id
        if not isinstance(agno_run_id, str) or agno_run_id != context.invocation_id:
            raise AgentProviderError(
                "Agno returned a different run identifier",
                code="agent.agno.run-identity-mismatch",
            )
        if output.session_id != session_id or output.user_id != user_id:
            raise AgentProviderError(
                "Agno returned different session identity",
                code="agent.agno.session-identity-mismatch",
            )
        memory = self._definition.blueprint.memory
        if (
            memory.local is not None
            and output.session_state is not None
            and len(output.session_state) > memory.local.max_entries
        ):
            raise AgentProviderError(
                "Agno session state exceeds the declared local memory limit",
                code="agent.agno.local-memory-limit",
            )
        conversation = memory.conversation
        return AgnoExecutionResult(
            agnoRunId=agno_run_id,
            runtimeVersion=_AGNO_VERSION,
            sessionId=session_id,
            userId=user_id,
            model=output.model,
            modelProvider=output.model_provider,
            response=_normalize_output(output),
            metrics=_run_metrics(output.metrics),
            memory=AgnoMemorySettings(
                localState=memory.local is not None,
                localMaxEntries=(
                    memory.local.max_entries if memory.local is not None else None
                ),
                conversationHistory=conversation is not None,
                historyMessages=(
                    conversation.max_messages if conversation is not None else None
                ),
                sessionSummaries=(
                    conversation.summaries if conversation is not None else False
                ),
                learnedMemory=learned is not None,
                learnedScope=learned.scope if learned is not None else None,
                learnedMode=learned.mode if learned is not None else None,
            ),
        )
