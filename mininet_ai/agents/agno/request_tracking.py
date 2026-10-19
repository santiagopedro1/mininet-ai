"""Ollama protocol-boundary request accounting (not an invented stop protocol)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from agno.models.ollama import Ollama
from ollama import AsyncClient, Client

from mininet_ai.runtime.instrumentation import RecordingBinding, current_binding


class _Request:
    def __init__(self, binding: RecordingBinding) -> None:
        self.ledger = binding.recorder.requests
        self.id = self.ledger.start(binding.context, "ollama")
        self.completed = False

    def response(self, response: Any) -> None:
        done = (
            response.get("done")
            if isinstance(response, dict)
            else getattr(response, "done", None)
        )
        if done is True and not self.completed:
            self.ledger.terminal(
                self.id, evidence="normal_response", source="ollama.chat.done"
            )
            self.completed = True

    def interrupted(self, error: BaseException) -> None:
        self.ledger.interrupted(
            self.id,
            type(error).__name__,
            cancelled=isinstance(error, (asyncio.CancelledError, GeneratorExit)),
        )


class _Client:
    def __init__(self, client: Client, binding: RecordingBinding) -> None:
        self._client = client
        self._binding = binding

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        request = _Request(self._binding)
        try:
            response = self._client.chat(*args, **kwargs)
            if kwargs.get("stream"):
                return self._stream(response, request)
            request.response(response)
            if not request.completed:
                request.ledger.interrupted(
                    request.id, "response_without_terminal_marker"
                )
            return response
        except BaseException as error:
            request.interrupted(error)
            raise

    @staticmethod
    def _stream(response: Any, request: _Request):
        try:
            for chunk in response:
                request.response(chunk)
                yield chunk
        except BaseException as error:
            request.interrupted(error)
            raise
        else:
            if not request.completed:
                request.ledger.interrupted(
                    request.id, "stream_without_terminal_response"
                )


class _AsyncClient:
    def __init__(self, client: AsyncClient, binding: RecordingBinding) -> None:
        self._client = client
        self._binding = binding

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        request = _Request(self._binding)
        try:
            response = await self._client.chat(*args, **kwargs)
            if kwargs.get("stream"):
                return self._stream(response, request)
            request.response(response)
            if not request.completed:
                request.ledger.interrupted(
                    request.id, "response_without_terminal_marker"
                )
            return response
        except BaseException as error:
            request.interrupted(error)
            raise

    @staticmethod
    async def _stream(response: Any, request: _Request):
        try:
            async for chunk in response:
                request.response(chunk)
                yield chunk
        except BaseException as error:
            request.interrupted(error)
            raise
        else:
            if not request.completed:
                request.ledger.interrupted(
                    request.id, "stream_without_terminal_response"
                )


class TrackedOllama(Ollama):
    """Use the ordinary SDK clients unchanged when recording is disabled."""

    def get_client(self) -> Client:
        client = super().get_client()
        binding = current_binding()
        return cast(Client, _Client(client, binding)) if binding is not None else client

    def get_async_client(self) -> AsyncClient:
        client = super().get_async_client()
        binding = current_binding()
        return (
            cast(AsyncClient, _AsyncClient(client, binding))
            if binding is not None
            else client
        )
