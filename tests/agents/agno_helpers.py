"""Offline Agno agents used by the native-provider tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

from agno.agent import Agent
from agno.models.base import Model
from agno.models.response import ModelResponse

from mininet_ai.sdk import AgentResponse


class StaticModel(Model):
    """Return one deterministic JSON response without external I/O."""

    def __init__(self, response: str = '{"message":"from Agno"}') -> None:
        super().__init__(id="static")
        self.response_content = response
        self.calls = 0

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        del args, kwargs
        self.calls += 1
        return ModelResponse(content=self.response_content)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(
        self, *args: Any, **kwargs: Any
    ) -> Iterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(
        self, response: Any, **kwargs: Any
    ) -> ModelResponse:
        del kwargs
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


class SlowAsyncModel(StaticModel):
    """Block only the async path so timeout tests remain deterministic."""

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        await asyncio.sleep(1)
        return self.invoke(*args, **kwargs)


def agent_factory(definition: Any) -> Agent:
    return Agent(
        id=definition.instance.id,
        model=StaticModel('{"message":"from Python Agno factory"}'),
        output_schema=AgentResponse,
        telemetry=False,
    )


direct_agent = Agent(
    id="direct-agent",
    model=StaticModel('{"message":"from direct Agno agent"}'),
    output_schema=AgentResponse,
    telemetry=False,
)


def invalid_factory(definition: Any) -> object:
    del definition
    return object()


not_an_agent = "invalid"
