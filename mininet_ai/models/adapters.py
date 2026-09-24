"""HTTP adapters that normalize model backends into the SDK contract."""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue

from mininet_ai.sdk import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)
from mininet_ai.transports import (
    HttpTransportError,
    HttpxJsonTransport,
    JsonHttpTransport,
)


_DEFAULT_RESPONSE_LIMIT = 4 * 1024 * 1024


def _endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(
            "model endpoint must be an HTTP(S) URL without credentials or a fragment"
        )
    return value


def _object(value: JsonValue, description: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ModelProviderError(
            f"model backend returned {description} with an invalid shape",
            code="model.response.invalid",
        )
    return value


def _list(value: JsonValue | None, description: str) -> list[JsonValue]:
    if not isinstance(value, list) or not value:
        raise ModelProviderError(
            f"model backend returned {description} with an invalid shape",
            code="model.response.invalid",
        )
    return value


def _optional_string(value: JsonValue | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelProviderError(
            f"model backend returned a non-string {field}",
            code="model.response.invalid",
        )
    return value


def _integer(value: JsonValue | None, field: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ModelProviderError(
            f"model backend returned an invalid {field}",
            code="model.response.invalid",
        )
    return value


def _messages(request: ModelRequest) -> list[JsonValue]:
    messages: list[JsonValue] = []
    for message in request.messages:
        value: dict[str, JsonValue] = {
            "role": message.role.value,
            "content": message.content,
        }
        if message.name is not None:
            value["name"] = message.name
        messages.append(value)
    return messages


def _structured_output(content: str, request: ModelRequest) -> JsonValue:
    schema = request.response_schema
    if schema is None:
        raise ModelProviderError(
            "structured output requires a response schema",
            code="model.request.invalid-schema",
        )
    try:
        value = json.loads(content, parse_constant=_reject_json_constant)
    except ValueError as error:
        raise ModelProviderError(
            f"model backend did not return valid structured JSON: {error}",
            code="model.response.invalid-json",
        ) from error
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except SchemaError as error:
        raise ModelProviderError(
            f"model request contains an invalid response schema: {error.message}",
            code="model.request.invalid-schema",
        ) from error
    except ValidationError as error:
        raise ModelProviderError(
            f"structured model response violates its schema: {error.message}",
            code="model.response.schema-mismatch",
        ) from error
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


class DeterministicModelProvider:
    """Return configured structured output without network access."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def generate(self, request: ModelRequest) -> ModelResponse:
        if "response" not in request.parameters:
            raise ModelProviderError(
                "deterministic model requires a 'response' parameter",
                code="model.deterministic.response-missing",
            )
        return ModelResponse(structuredOutput=request.parameters["response"])


class _HttpModelProvider:
    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        endpoint: str,
        *,
        transport: JsonHttpTransport | None,
        headers: Mapping[str, str] | None,
        max_response_bytes: int,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._endpoint = _endpoint(endpoint)
        self._transport = transport or HttpxJsonTransport()
        self._headers = {"content-type": "application/json", **dict(headers or {})}
        self._max_response_bytes = max_response_bytes

    def _post(
        self,
        request: ModelRequest,
        payload: Mapping[str, JsonValue],
        *,
        backend: str,
    ) -> dict[str, JsonValue]:
        try:
            response = self._transport.post_json(
                self._endpoint,
                payload,
                headers=self._headers,
                timeout_seconds=request.timeout_seconds,
                max_response_bytes=self._max_response_bytes,
            )
        except HttpTransportError as error:
            raise ModelProviderError(
                str(error),
                code=f"model.{backend}.{error.code}",
            ) from error
        return _object(response, backend + " response")


class OpenAICompatibleModelProvider(_HttpModelProvider):
    """Normalize an OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self,
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        *,
        api_key: str | None = None,
        transport: JsonHttpTransport | None = None,
        headers: Mapping[str, str] | None = None,
        max_response_bytes: int = _DEFAULT_RESPONSE_LIMIT,
    ) -> None:
        request_headers = dict(headers or {})
        if api_key is not None:
            if not api_key:
                raise ValueError("api_key must not be empty")
            request_headers["authorization"] = f"Bearer {api_key}"
        super().__init__(
            endpoint,
            transport=transport,
            headers=request_headers,
            max_response_bytes=max_response_bytes,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, JsonValue] = {
            **request.parameters,
            "model": request.model,
            "messages": _messages(request),
            "stream": False,
        }
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "mininet_ai_response",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        response = self._post(request, payload, backend="openai")
        choices = _list(response.get("choices"), "OpenAI choices")
        choice = _object(choices[0], "OpenAI choice")
        message = _object(choice.get("message"), "OpenAI message")
        content = _optional_string(message.get("content"), "message content")
        if content is None:
            raise ModelProviderError(
                "OpenAI response does not contain message content",
                code="model.response.invalid",
            )
        usage = _object(response.get("usage", {}), "OpenAI usage")
        input_tokens = _integer(usage.get("prompt_tokens"), "prompt token count")
        output_tokens = _integer(
            usage.get("completion_tokens"),
            "completion token count",
        )
        total_tokens = _integer(usage.get("total_tokens"), "total token count")
        if "total_tokens" not in usage:
            total_tokens = input_tokens + output_tokens
        elif total_tokens < input_tokens + output_tokens:
            raise ModelProviderError(
                "OpenAI total token count is smaller than its input and output",
                code="model.response.invalid",
            )
        structured = None
        if request.response_schema is not None:
            structured = _structured_output(content, request)
        return ModelResponse(
            content=content,
            structuredOutput=structured,
            usage=TokenUsage(
                inputTokens=input_tokens,
                outputTokens=output_tokens,
                totalTokens=total_tokens,
            ),
            providerRequestId=_optional_string(response.get("id"), "request id"),
            finishReason=_optional_string(choice.get("finish_reason"), "finish reason"),
        )


class OllamaModelProvider(_HttpModelProvider):
    """Normalize Ollama's non-streaming chat endpoint."""

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:11434/api/chat",
        *,
        transport: JsonHttpTransport | None = None,
        headers: Mapping[str, str] | None = None,
        max_response_bytes: int = _DEFAULT_RESPONSE_LIMIT,
    ) -> None:
        super().__init__(
            endpoint,
            transport=transport,
            headers=headers,
            max_response_bytes=max_response_bytes,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, JsonValue] = {
            "model": request.model,
            "messages": _messages(request),
            "stream": False,
            "options": request.parameters,
        }
        if request.response_schema is not None:
            payload["format"] = request.response_schema
        response = self._post(request, payload, backend="ollama")
        message = _object(response.get("message"), "Ollama message")
        content = _optional_string(message.get("content"), "message content")
        if content is None:
            raise ModelProviderError(
                "Ollama response does not contain message content",
                code="model.response.invalid",
            )
        input_tokens = _integer(
            response.get("prompt_eval_count"),
            "prompt token count",
        )
        output_tokens = _integer(response.get("eval_count"), "output token count")
        structured = None
        if request.response_schema is not None:
            structured = _structured_output(content, request)
        return ModelResponse(
            content=content,
            structuredOutput=structured,
            usage=TokenUsage(
                inputTokens=input_tokens,
                outputTokens=output_tokens,
                totalTokens=input_tokens + output_tokens,
            ),
            finishReason=_optional_string(response.get("done_reason"), "finish reason"),
        )
