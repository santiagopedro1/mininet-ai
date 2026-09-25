"""Authorization and validated execution of capability proposals."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from pydantic import JsonValue, TypeAdapter

from mininet_ai.capabilities.verification import PostconditionVerifier
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
    ReversibleCapabilityProvider,
)
from mininet_ai.specification.models import CapabilityDefinition
from mininet_ai.substrates.runtime import (
    ActionResult,
    ActionStatus,
    PostconditionResult,
    RollbackResult,
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
        verifier: PostconditionVerifier | None = None,
    ) -> None:
        self._catalog = catalog
        self._providers = providers
        self._clock = clock
        self._verifier = verifier

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

        result = provider_result or self._result(
            context,
            proposal,
            status=ActionStatus.SUCCEEDED,
            changed=outcome.changed,
            output=outcome.output,
        )
        return self._verify_effect(
            definition,
            capability,
            provider,
            context,
            proposal,
            outcome,
            result,
        )

    def _verify_effect(
        self,
        definition: AgentExecutionDefinition,
        capability: CapabilityDefinition,
        provider: CapabilityProvider,
        context: AgentContext,
        proposal: ActionProposal,
        outcome: CapabilityOutcome,
        result: ActionResult,
    ) -> ActionResult:
        if (
            not definition.policy.require_postcondition_check
            or not capability.postconditions
        ):
            return result
        if self._verifier is None:
            return self._postcondition_failure(
                capability,
                provider,
                context,
                proposal,
                outcome,
                result,
                checks=(),
                effect_seconds=0,
                message="postcondition verification has no observation provider",
            )
        report = self._verifier.verify(
            context,
            proposal,
            tuple(capability.postconditions),
        )
        if report.satisfied:
            return result.model_copy(
                update={
                    "completed_at": self._clock(),
                    "postconditions": report.checks,
                    "effect_latency_seconds": report.duration_seconds,
                }
            )
        failed = [
            f"{check.observation}:{check.path}"
            for check in report.checks
            if not check.satisfied
        ]
        return self._postcondition_failure(
            capability,
            provider,
            context,
            proposal,
            outcome,
            result,
            checks=report.checks,
            effect_seconds=report.duration_seconds,
            message="postconditions not satisfied: " + ", ".join(failed),
        )

    def _postcondition_failure(
        self,
        capability: CapabilityDefinition,
        provider: CapabilityProvider,
        context: AgentContext,
        proposal: ActionProposal,
        outcome: CapabilityOutcome,
        result: ActionResult,
        *,
        checks: tuple[PostconditionResult, ...],
        effect_seconds: float,
        message: str,
    ) -> ActionResult:
        rollback = None
        changed = result.changed
        issue_code = "capability.postcondition.failed"
        if result.changed and capability.rollback is not None:
            rollback = self._rollback(
                capability,
                provider,
                context,
                proposal,
                outcome,
            )
            if rollback.status == ActionStatus.SUCCEEDED:
                changed = False
                message += "; action was rolled back"
            else:
                issue_code = "capability.rollback.failed"
                message += "; rollback failed"
        return result.model_copy(
            update={
                "status": ActionStatus.FAILED,
                "completed_at": self._clock(),
                "changed": changed,
                "postconditions": checks,
                "rollback": rollback,
                "effect_latency_seconds": effect_seconds,
                "issue": RuntimeIssue(
                    code=issue_code,
                    message=message,
                    target=proposal.target,
                ),
            }
        )

    def _rollback(
        self,
        capability: CapabilityDefinition,
        provider: CapabilityProvider,
        context: AgentContext,
        proposal: ActionProposal,
        outcome: CapabilityOutcome,
    ) -> RollbackResult:
        configuration = capability.rollback
        if configuration is None:
            raise AssertionError("rollback requested without configuration")
        if not isinstance(provider, ReversibleCapabilityProvider):
            return RollbackResult(
                status=ActionStatus.FAILED,
                completedAt=self._clock(),
                issue=RuntimeIssue(
                    code="capability.rollback.unsupported",
                    message="capability provider does not support rollback",
                    target=proposal.target,
                ),
            )
        try:
            raw = provider.rollback(
                context,
                proposal,
                outcome,
                timeout_seconds=duration_seconds(configuration.timeout),
            )
            if isinstance(raw, ActionResult):
                if raw.run_id != context.run_id:
                    raise ValueError("rollback result changed run identity")
                return RollbackResult(
                    status=raw.status,
                    completedAt=self._clock(),
                    changed=raw.changed,
                    output=raw.output,
                    issue=raw.issue,
                )
            normalized = self._normalize_outcome(raw)
            return RollbackResult(
                status=ActionStatus.SUCCEEDED,
                completedAt=self._clock(),
                changed=normalized.changed,
                output=normalized.output,
            )
        except Exception as error:
            code = getattr(error, "code", None)
            return RollbackResult(
                status=ActionStatus.FAILED,
                completedAt=self._clock(),
                issue=RuntimeIssue(
                    code=(
                        code
                        if isinstance(code, str) and code
                        else "capability.rollback.failed"
                    ),
                    message=str(error) or type(error).__name__,
                    target=proposal.target,
                ),
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
