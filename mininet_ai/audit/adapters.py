"""Audit decorators for agent-runtime seams."""

from __future__ import annotations

from typing import Protocol

from pydantic import JsonValue

from mininet_ai.audit.contracts import AuditEventType
from mininet_ai.audit.recorder import AuditRecorder
from mininet_ai.sdk import (
    ActionProposal,
    AgentContext,
    AgentProvider,
    AgentResponse,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from mininet_ai.substrates import ActionResult


def _error_data(error: Exception) -> dict[str, JsonValue]:
    data: dict[str, JsonValue] = {
        "errorType": type(error).__name__,
        "message": str(error) or type(error).__name__,
    }
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        data["code"] = code
    return data


class AuditedAgentProvider:
    """Record one agent invocation without changing provider behavior."""

    def __init__(self, provider: AgentProvider, recorder: AuditRecorder) -> None:
        self.contract_version = provider.contract_version
        self._provider = provider
        self._recorder = recorder

    def invoke(self, context: AgentContext) -> AgentResponse:
        self._recorder.record(
            AuditEventType.AGENT_STARTED,
            context,
            {"context": context.model_dump(mode="json", by_alias=True)},
        )
        try:
            response = self._provider.invoke(context)
        except Exception as error:
            self._recorder.record(
                AuditEventType.AGENT_FAILED,
                context,
                _error_data(error),
            )
            raise
        self._recorder.record(
            AuditEventType.AGENT_COMPLETED,
            context,
            {"response": response.model_dump(mode="json", by_alias=True)},
        )
        return response


class AuditedModelProvider:
    """Record prompts, normalized responses, usage, and model failures."""

    def __init__(
        self,
        provider: ModelProvider,
        recorder: AuditRecorder,
        context: AgentContext,
    ) -> None:
        self.contract_version = provider.contract_version
        self._provider = provider
        self._recorder = recorder
        self._context = context

    def generate(self, request: ModelRequest) -> ModelResponse:
        self._recorder.record(
            AuditEventType.MODEL_STARTED,
            self._context,
            {"request": request.model_dump(mode="json", by_alias=True)},
        )
        try:
            response = self._provider.generate(request)
        except Exception as error:
            self._recorder.record(
                AuditEventType.MODEL_FAILED,
                self._context,
                _error_data(error),
            )
            raise
        self._recorder.record(
            AuditEventType.MODEL_COMPLETED,
            self._context,
            {"response": response.model_dump(mode="json", by_alias=True)},
        )
        return response


class CapabilityExecutor(Protocol):
    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> ActionResult: ...


class AuditedCapabilityExecutor:
    """Record proposals and normalized action results around an executor."""

    def __init__(
        self,
        executor: CapabilityExecutor,
        recorder: AuditRecorder,
    ) -> None:
        self._executor = executor
        self._recorder = recorder

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> ActionResult:
        self._recorder.record(
            AuditEventType.CAPABILITY_STARTED,
            context,
            {"proposal": proposal.model_dump(mode="json", by_alias=True)},
        )
        try:
            result = self._executor.execute(context, proposal)
        except Exception as error:
            self._recorder.record(
                AuditEventType.CAPABILITY_FAILED,
                context,
                _error_data(error),
            )
            raise
        self._recorder.record(
            AuditEventType.CAPABILITY_COMPLETED,
            context,
            {"result": result.model_dump(mode="json", by_alias=True)},
        )
        return result
