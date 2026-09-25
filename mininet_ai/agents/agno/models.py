"""Agno models that support deterministic offline experiments."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from agno.metrics import MessageMetrics
from agno.models.base import Model
from agno.models.response import ModelResponse
from pydantic import JsonValue, ValidationError

from mininet_ai.agents.agno.contracts import TokenMetrics
from mininet_ai.sdk import AgentProviderError, AgentResponse


class DeterministicAgnoModel(Model):
    """Return one configured response through Agno without external I/O."""

    def __init__(
        self,
        response: object,
        *,
        usage: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(
            id="deterministic",
            name="DeterministicAgnoModel",
            provider="MininetAI",
        )
        try:
            normalized = AgentResponse.model_validate(response)
        except ValidationError as error:
            raise AgentProviderError(
                f"deterministic Agno response is invalid: {error}",
                code="agent.agno.deterministic-response-invalid",
            ) from error
        self._content = normalized.model_dump_json(by_alias=True)
        self._usage = self._metrics(usage or {})

    @staticmethod
    def _metrics(usage: Mapping[str, JsonValue]) -> MessageMetrics:
        try:
            normalized = TokenMetrics.model_validate(dict(usage))
        except ValidationError as error:
            raise AgentProviderError(
                f"deterministic Agno usage is invalid: {error}",
                code="agent.agno.deterministic-usage-invalid",
            ) from error
        return MessageMetrics(
            input_tokens=normalized.input_tokens,
            output_tokens=normalized.output_tokens,
            total_tokens=normalized.total_tokens,
            audio_input_tokens=normalized.audio_input_tokens,
            audio_output_tokens=normalized.audio_output_tokens,
            audio_total_tokens=normalized.audio_total_tokens,
            cache_read_tokens=normalized.cache_read_tokens,
            cache_write_tokens=normalized.cache_write_tokens,
            reasoning_tokens=normalized.reasoning_tokens,
            cost=normalized.cost,
        )

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        del args, kwargs
        return ModelResponse(content=self._content, response_usage=self._usage)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Iterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(
        self,
        response: Any,
        **kwargs: Any,
    ) -> ModelResponse:
        del kwargs
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response
