"""Authorization and validated execution of capability proposals."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue, TypeAdapter

from mininet_ai.durations import duration_seconds
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.plugins.registry import ProviderRegistry
from mininet_ai.sdk.catalog import AgentExecutionDefinition, ExecutionCatalog
from mininet_ai.sdk.contracts import (
    ActionProposal,
    AgentContext,
    CapabilityOutcome,
    CapabilityProvider,
    CapabilityProviderError,
)
from mininet_ai.specification.models import CapabilityDefinition
from mininet_ai.substrates.runtime import (
    ActionResult,
    ActionStatus,
    RuntimeIssue,
)


Clock = Callable[[], datetime]
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_FORMAT_CHECKER = FormatChecker()


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _CapabilityError(Exception):
    status: ActionStatus
    code: str
    message: str


class CapabilityEngine:
    """Authorize, validate, dispatch, and normalize one action proposal."""

    def __init__(
        self,
        catalog: ExecutionCatalog,
        providers: ProviderRegistry[CapabilityDefinition, CapabilityProvider],
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._catalog = catalog
        self._providers = providers
        self._clock = clock

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> ActionResult:
        """Return a normalized result without leaking provider exceptions."""

        try:
            definition = self._resolve(context)
            capability = self._authorize(definition, context, proposal)
            proposal = self._bounded_proposal(definition, proposal)
            self._validate_schema(
                capability.input_schema,
                proposal.arguments,
                invalid_code="capability.input.invalid",
                invalid_status=ActionStatus.REJECTED,
                label="input",
            )
            if capability.provider is None:
                self._fail(
                    "capability.provider.missing",
                    f"capability {proposal.capability!r} has no provider",
                )
            provider = self._providers.create(capability.provider, capability)
            raw_outcome = provider.execute(context, proposal)
            provider_result = self._provider_result(context, proposal, raw_outcome)
            if (
                provider_result is not None
                and provider_result.status != ActionStatus.SUCCEEDED
            ):
                return provider_result
            outcome = self._normalize_outcome(raw_outcome)
            self._validate_schema(
                capability.output_schema,
                outcome.output,
                invalid_code="capability.output.invalid",
                invalid_status=ActionStatus.FAILED,
                label="output",
            )
        except _CapabilityError as error:
            return self._result(
                context,
                proposal,
                status=error.status,
                issue=RuntimeIssue(
                    code=error.code,
                    message=error.message,
                    target=proposal.target,
                ),
            )
        except AgentRuntimeError as error:
            return self._result(
                context,
                proposal,
                status=ActionStatus.FAILED,
                issue=RuntimeIssue(
                    code=error.code,
                    message=str(error),
                    target=proposal.target,
                ),
            )
        except CapabilityProviderError as error:
            return self._result(
                context,
                proposal,
                status=error.status,
                issue=RuntimeIssue(
                    code=error.code,
                    message=str(error),
                    target=proposal.target,
                ),
            )
        except TimeoutError as error:
            return self._result(
                context,
                proposal,
                status=ActionStatus.FAILED,
                issue=RuntimeIssue(
                    code="capability.timeout",
                    message=(str(error) or "capability execution timed out"),
                    target=proposal.target,
                ),
            )
        except Exception as error:
            return self._result(
                context,
                proposal,
                status=ActionStatus.FAILED,
                issue=RuntimeIssue(
                    code="capability.execution.failed",
                    message=f"capability execution failed: {error}",
                    target=proposal.target,
                ),
            )

        if provider_result is not None:
            return provider_result
        return self._result(
            context,
            proposal,
            status=ActionStatus.SUCCEEDED,
            changed=outcome.changed,
            output=outcome.output,
        )

    def _resolve(self, context: AgentContext) -> AgentExecutionDefinition:
        try:
            definition = self._catalog.resolve(context.agent_id)
        except AgentRuntimeError as error:
            raise _CapabilityError(
                ActionStatus.REJECTED,
                error.code,
                str(error),
            ) from error
        instance = definition.instance
        attachment = instance.attachment
        if (
            context.deployment != instance.deployment
            or context.layer != attachment.layer
            or context.custom_layer != attachment.custom_layer
            or context.target_kind != attachment.target_kind
            or set(context.targets) != set(attachment.targets)
            or set(context.capabilities) != set(instance.capabilities)
        ):
            self._reject(
                "capability.context.invalid",
                f"agent context for {context.agent_id!r} does not match its "
                "compiled scope",
            )
        return definition

    def _authorize(
        self,
        definition: AgentExecutionDefinition,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityDefinition:
        capabilities = {
            item.metadata.name: item for item in definition.capabilities
        }
        try:
            capability = capabilities[proposal.capability]
        except KeyError as error:
            raise _CapabilityError(
                ActionStatus.REJECTED,
                "capability.not-assigned",
                f"capability {proposal.capability!r} is not assigned to agent "
                f"{context.agent_id!r}",
            ) from error
        if proposal.target not in definition.instance.attachment.targets:
            self._reject(
                "capability.target.out-of-scope",
                f"target {proposal.target!r} is outside agent "
                f"{context.agent_id!r}'s compiled scope",
            )
        if context.target_kind not in capability.targets:
            self._reject(
                "capability.target-kind.denied",
                f"capability {proposal.capability!r} cannot target "
                f"{context.target_kind.value!r}",
            )
        layer = context.custom_layer or context.layer.value
        if layer not in capability.layers:
            self._reject(
                "capability.layer.denied",
                f"capability {proposal.capability!r} is unavailable at layer "
                f"{layer!r}",
            )
        missing_effects = set(capability.effects) - set(
            definition.instance.privileges
        )
        if missing_effects:
            self._reject(
                "capability.effects.denied",
                f"agent {context.agent_id!r} lacks compiled effects: "
                + ", ".join(sorted(missing_effects)),
            )
        return capability

    @staticmethod
    def _bounded_proposal(
        definition: AgentExecutionDefinition,
        proposal: ActionProposal,
    ) -> ActionProposal:
        declared = duration_seconds(
            definition.instance.execution.action_timeout
        )
        return proposal.model_copy(
            update={"timeout_seconds": min(proposal.timeout_seconds, declared)}
        )

    def _validate_schema(
        self,
        schema: Mapping[str, JsonValue],
        value: Mapping[str, JsonValue],
        *,
        invalid_code: str,
        invalid_status: ActionStatus,
        label: str,
    ) -> None:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            self._fail(
                "capability.schema.invalid",
                f"capability {label} schema is invalid: {error.message}",
            )
        try:
            Draft202012Validator(
                schema,
                format_checker=_FORMAT_CHECKER,
            ).validate(value)
        except ValidationError as error:
            path = "$" + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in error.absolute_path
            )
            raise _CapabilityError(
                invalid_status,
                invalid_code,
                f"capability {label} at {path}: {error.message}",
            ) from error

    @staticmethod
    def _normalize_outcome(
        raw: CapabilityOutcome | Mapping[str, JsonValue] | ActionResult,
    ) -> CapabilityOutcome:
        if isinstance(raw, ActionResult):
            try:
                return CapabilityOutcome(changed=raw.changed, output=raw.output)
            except ValueError as error:
                raise _CapabilityError(
                    ActionStatus.FAILED,
                    "capability.output.invalid",
                    f"capability provider returned invalid JSON output: {error}",
                ) from error
        if isinstance(raw, CapabilityOutcome):
            return raw
        if not isinstance(raw, Mapping):
            raise _CapabilityError(
                ActionStatus.FAILED,
                "capability.output.invalid",
                "capability provider returned a non-object result",
            )
        try:
            output = _JSON_OBJECT.validate_python(dict(raw))
        except (TypeError, ValueError) as error:
            raise _CapabilityError(
                ActionStatus.FAILED,
                "capability.output.invalid",
                f"capability provider returned invalid JSON output: {error}",
            ) from error
        return CapabilityOutcome(output=output)

    @staticmethod
    def _provider_result(
        context: AgentContext,
        proposal: ActionProposal,
        raw: CapabilityOutcome | Mapping[str, JsonValue] | ActionResult,
    ) -> ActionResult | None:
        if not isinstance(raw, ActionResult):
            return None
        if raw.run_id != context.run_id or raw.request_id != proposal.id:
            raise _CapabilityError(
                ActionStatus.FAILED,
                "capability.result.invalid-identity",
                "capability provider returned a result for another run or request",
            )
        return raw

    def _result(
        self,
        context: AgentContext,
        proposal: ActionProposal,
        *,
        status: ActionStatus,
        changed: bool = False,
        output: dict[str, JsonValue] | None = None,
        issue: RuntimeIssue | None = None,
    ) -> ActionResult:
        return ActionResult(
            run_id=context.run_id,
            request_id=proposal.id,
            status=status,
            completed_at=self._clock(),
            changed=changed,
            output=output or {},
            issue=issue,
        )

    @staticmethod
    def _reject(code: str, message: str) -> NoReturn:
        raise _CapabilityError(ActionStatus.REJECTED, code, message)

    @staticmethod
    def _fail(code: str, message: str) -> NoReturn:
        raise _CapabilityError(ActionStatus.FAILED, code, message)
