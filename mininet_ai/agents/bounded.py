"""Explicit, native llama.cpp bounded-choice execution primitives."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from time import monotonic
from typing import Any, Protocol, cast

from mininet_ai.agents.bounded_contracts import (
    AgentExecutionRecord,
    BoundedDecisionRecord,
)
from mininet_ai.agents.reconciliation import reconcile_bundle
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.specification.models import duration_seconds
from mininet_ai.substrates.runtime import ActionRequest, ObservationQuery


@dataclass(frozen=True)
class ChoiceResult:
    choice_id: str
    proposals: tuple[dict[str, Any], ...]
    probabilities: dict[str, float] | None = None
    probability: float | None = None
    confidence: float | None = None
    native_response: dict[str, Any] | None = None


def parse_response(payload: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    """Parse a native response while rejecting duplicate keys and invalid JSON."""
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(
                payload,
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant {value}")
                ),
            )
        except (ValueError, UnicodeDecodeError) as error:
            raise AgentRuntimeError(
                f"native choice response is invalid: {error}",
                code="agent.bounded-decision.invalid-output",
            ) from error
    if not isinstance(payload, Mapping):
        raise AgentRuntimeError(
            "native choice response must be an object",
            code="agent.bounded-decision.invalid-output",
        )
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _eligibility_signals(value: Any) -> tuple[list[bool], bool]:
    signals: list[bool] = []
    malformed = False
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "eligible":
                if isinstance(child, bool):
                    signals.append(child)
                else:
                    malformed = True
                    nested_signals, nested_malformed = _eligibility_signals(child)
                    signals.extend(nested_signals)
                    malformed = malformed or nested_malformed
            else:
                nested_signals, nested_malformed = _eligibility_signals(child)
                signals.extend(nested_signals)
                malformed = malformed or nested_malformed
    elif isinstance(value, (list, tuple)):
        for child in value:
            nested_signals, nested_malformed = _eligibility_signals(child)
            signals.extend(nested_signals)
            malformed = malformed or nested_malformed
    return signals, malformed


def map_choice(
    payload: str | bytes | Mapping[str, Any],
    choices: Mapping[str, Sequence[dict[str, Any]]],
) -> ChoiceResult:
    """Map only an exact native choice ID to its fixed, scenario-owned bundle."""
    response = parse_response(payload)
    answers = response.get("answers")
    if not isinstance(answers, Mapping) or set(answers) != {"next_action"}:
        raise AgentRuntimeError(
            "native response choice must answer exactly next_action",
            code="agent.bounded-decision.invalid-output",
        )
    usage = response.get("usage")
    if usage is not None and not isinstance(usage, Mapping):
        raise AgentRuntimeError(
            "native response usage must be an object",
            code="agent.bounded-decision.invalid-output",
        )
    output_tokens = (
        usage.get("output_tokens", usage.get("outputTokens", 0))
        if isinstance(usage, Mapping)
        else 0
    )
    if (
        isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        raise AgentRuntimeError(
            "native response generated-token usage is invalid",
            code="agent.bounded-decision.invalid-output",
        )
    if output_tokens != 0:
        raise AgentRuntimeError(
            "native choice contract must not generate output tokens",
            code="agent.bounded-decision.native-contract-violation",
        )
    answer = answers.get("next_action") if isinstance(answers, Mapping) else None
    if not isinstance(answer, Mapping) or answer.get("type") != "choice":
        raise AgentRuntimeError(
            "native response must contain answers.next_action of type choice",
            code="agent.bounded-decision.invalid-output",
        )
    if set(answer) - {"type", "choice", "probability", "confidence"}:
        raise AgentRuntimeError(
            "native choice answer contains unsupported fields",
            code="agent.bounded-decision.invalid-output",
        )
    choice_id = answer.get("choice")
    if not isinstance(choice_id, str) or choice_id not in choices:
        raise AgentRuntimeError(
            "native response contains a missing or unknown choice",
            code="agent.bounded-decision.invalid-output",
        )
    if choice_id not in {"no_action", "install_recovery"}:
        raise AgentRuntimeError(
            f"choice {choice_id!r} is not part of the bounded-decision contract",
            code="agent.bounded-decision.invalid-output",
        )
    if choice_id == "no_action" and choices[choice_id]:
        raise AgentRuntimeError(
            "the no_action choice must have no proposals",
            code="agent.bounded-decision.configuration-invalid",
        )
    probabilities = response.get("probabilities")
    if "probabilities" in response:
        if not isinstance(probabilities, Mapping):
            raise AgentRuntimeError(
                "native response probabilities must be an object",
                code="agent.bounded-decision.invalid-output",
            )
        if not set(probabilities).issubset(choices):
            raise AgentRuntimeError(
                "native response probabilities contain an unknown choice",
                code="agent.bounded-decision.invalid-output",
            )
        normalized_probabilities: dict[str, float] = {}
        for key, value in probabilities.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise AgentRuntimeError(
                    "native response probabilities must be finite and between 0 and 1",
                    code="agent.bounded-decision.invalid-output",
                )
            normalized_probabilities[str(key)] = float(value)
        probabilities = normalized_probabilities
    confidence = response.get("confidence", answer.get("confidence"))
    if "confidence" in response or "confidence" in answer:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise AgentRuntimeError(
                "native response confidence must be finite and between 0 and 1",
                code="agent.bounded-decision.invalid-output",
            )
        confidence = float(confidence)
    else:
        confidence = None
    probability = answer.get("probability")
    if "probability" in answer:
        if (
            isinstance(probability, bool)
            or not isinstance(probability, (int, float))
            or not math.isfinite(probability)
            or not 0 <= probability <= 1
        ):
            raise AgentRuntimeError(
                "native response probability must be finite and between 0 and 1",
                code="agent.bounded-decision.invalid-output",
            )
        probability = float(probability)
    else:
        probability = None
    return ChoiceResult(
        choice_id=choice_id,
        proposals=tuple(choices[choice_id]),
        native_response=dict(response),
        probabilities=probabilities,
        probability=probability,
        confidence=confidence,
    )


class DecisionTransport(Protocol):
    def post_json(self, url: str, payload: Mapping[str, Any], **kwargs: Any) -> Any: ...
    def get_json(self, url: str, **kwargs: Any) -> Any: ...


class NativeSystemOneClient:
    """Issue the single native choice request; no chat/generation fallback exists."""

    def __init__(self, endpoint: str, transport: DecisionTransport) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport

    def decide(
        self,
        *,
        state: str,
        instructions: str,
        criteria: Mapping[str, str],
        choices: Mapping[str, Sequence[dict[str, Any]]],
        timeout_seconds: float,
        request_id: str | None = None,
    ) -> ChoiceResult:
        if tuple(choices) != tuple(criteria) or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in criteria.items()
        ):
            raise AgentRuntimeError(
                "scenario choice IDs and native criteria must have identical stable order",
                code="agent.bounded-decision.configuration-invalid",
            )
        payload = {
            "state": state,
            "questions": {
                "next_action": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": dict(criteria),
                }
            },
        }
        try:
            response = self.transport.post_json(
                f"{self.endpoint}/v1/systemone",
                payload,
                headers={"Content-Type": "application/json"},
                timeout_seconds=timeout_seconds,
                max_response_bytes=1_048_576,
                **({"request_id": request_id} if request_id is not None else {}),
            )
        except Exception as error:
            if isinstance(error, TimeoutError):
                raise AgentRuntimeError(
                    f"native llama.cpp decision request timed out: {error}",
                    code="agent.bounded-decision.timeout",
                ) from error
            if getattr(error, "code", None) == "invalid-json":
                raise AgentRuntimeError(
                    f"native choice response is malformed: {error}",
                    code="agent.bounded-decision.invalid-output",
                ) from error
            raise AgentRuntimeError(
                f"native llama.cpp decision request failed: {error}",
                code="agent.bounded-decision.service-unavailable",
            ) from error
        return map_choice(response, choices)

    def verify_startup(
        self,
        *,
        model_name: str,
        expected_identity: Mapping[str, Any],
        instructions: str,
        criteria: Mapping[str, str],
        choices: Mapping[str, Sequence[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Verify availability, observable identity and native interface without mutation."""
        try:
            health = self.transport.get_json(
                f"{self.endpoint}/health",
                headers={"Accept": "application/json"},
                timeout_seconds=2.0,
                max_response_bytes=65536,
            )
            props = self.transport.get_json(
                f"{self.endpoint}/props",
                headers={"Accept": "application/json"},
                timeout_seconds=2.0,
                max_response_bytes=65536,
            )
        except Exception as error:
            raise AgentRuntimeError(
                f"bounded decision startup service check failed: {error}",
                code="agent.bounded-decision.startup-unavailable",
            ) from error
        if not isinstance(health, Mapping) or not isinstance(props, Mapping):
            raise AgentRuntimeError(
                "bounded decision startup endpoints returned invalid documents",
                code="agent.bounded-decision.startup-interface-invalid",
            )
        if health.get("status") not in {"ok", "ready"}:
            raise AgentRuntimeError(
                "bounded decision service is not ready",
                code="agent.bounded-decision.startup-unavailable",
            )
        observed_name = props.get("model_alias") or props.get("model")
        if observed_name is not None and observed_name != model_name:
            raise AgentRuntimeError(
                f"bounded decision served model {observed_name!r} does not match expected {model_name!r}",
                code="agent.bounded-decision.startup-identity-mismatch",
            )
        observable_fields = {
            "serverVersion": "version",
            "serverCommit": "commit",
            "artifact": "model_path",
            "sha256": "model_sha256",
            "revision": "model_revision",
            "repository": "model_repository",
        }
        observed_identity = {}
        for expected_key, props_key in observable_fields.items():
            expected = expected_identity.get(expected_key)
            observed = props.get(props_key)
            if expected is not None and observed is not None:
                if expected != observed:
                    raise AgentRuntimeError(
                        f"bounded decision startup identity mismatch for {expected_key}",
                        code="agent.bounded-decision.startup-identity-mismatch",
                    )
                observed_identity[expected_key] = observed
        try:
            self.decide(
                state="{}",
                instructions=instructions,
                criteria=criteria,
                choices={key: () for key in criteria},
                timeout_seconds=2.0,
            )
        except AgentRuntimeError as error:
            raise AgentRuntimeError(
                f"bounded decision native interface check failed: {error}",
                code="agent.bounded-decision.startup-interface-invalid",
            ) from error
        return {
            "status": "verified"
            if len(observed_identity) == len(expected_identity)
            else "unverified",
            "expectedModel": model_name,
            "observedModel": observed_name,
            "expectedIdentity": dict(expected_identity),
            "observedIdentity": observed_identity,
            "interface": "verified",
            "health": dict(health),
        }


@dataclass(frozen=True)
class BoundedExecutionResult:
    response: Any
    model_queueing_seconds: float
    runtime_name: str
    runtime_data: dict[str, Any]
    authority: DecisionAuthority | None = None


class DecisionAuthority:
    """Synchronize revocation with the start boundary of local actions."""

    def __init__(self, deadline_tick: float, clock: Any = monotonic) -> None:
        self.deadline_tick = deadline_tick
        self._clock = clock
        self._lock = Lock()
        self._action_serial = Lock()
        self._revocation: str | None = None
        self._action_starts: list[float] = []

    def revoke(self, reason: str) -> bool:
        if reason not in {"cancelled", "timeout"}:
            raise ValueError("decision revocation reason must be cancelled or timeout")
        with self._lock:
            if self._revocation is not None:
                return False
            self._revocation = reason
            return True

    def execute_if_authorized(self, action: Any) -> tuple[bool, Any]:
        """Atomically check authority and cross the local action-start boundary."""
        with self._action_serial:
            with self._lock:
                if self._revocation is not None:
                    return False, None
                started_tick = self._clock()
                if started_tick >= self.deadline_tick:
                    self._revocation = "timeout"
                    return False, None
                self._action_starts.append(started_tick)
            # Cancellation can revoke future starts while this effect runs;
            # it does not wait for, or interrupt, the already-started effect.
            return True, action()

    def validate_response(self) -> str | None:
        with self._lock:
            if self._revocation is not None:
                return self._revocation
            if self._clock() >= self.deadline_tick:
                self._revocation = "timeout"
            return self._revocation

    @property
    def revocation(self) -> str | None:
        with self._lock:
            return self._revocation

    @property
    def action_starts(self) -> tuple[float, ...]:
        with self._lock:
            return tuple(self._action_starts)


class BoundedDecisionAdapter:
    """Run one explicitly compiled bounded decision without constructing Agno."""

    contract_version = "mininet-ai/agent-runtime/v1alpha4"

    def __init__(
        self,
        definition: Any,
        transport: DecisionTransport,
        startup: Mapping[str, Any] | None = None,
        monotonic_clock: Any = monotonic,
        substrate: Any | None = None,
    ) -> None:
        self.definition = definition
        self.transport = transport
        self.startup = dict(startup or {})
        self._monotonic = monotonic_clock
        self._substrate = substrate
        self._reconciliation: dict[str, Any] = {}
        self._bundle_requests = tuple(
            ActionRequest(
                id=f"bounded-{index + 1}",
                name=proposal["capability"],
                target=proposal["target"],
                parameters=proposal["arguments"],
            )
            for index, proposal in enumerate(
                next(
                    choice["proposals"]
                    for choice in definition.instance.decision_scenario["choices"]
                    if choice["id"] == "install_recovery"
                )
            )
        )
        model = definition.blueprint.model
        if not hasattr(model, "parameters") or model.provider != "llamacpp-systemone":
            raise AgentRuntimeError(
                "bounded execution requires a resolved llamacpp-systemone model",
                code="agent.bounded-decision.configuration-invalid",
            )
        self.model = model
        scenario = definition.instance.decision_scenario
        if not isinstance(scenario, Mapping):
            raise AgentRuntimeError(
                "bounded execution plan has no embedded decision scenario",
                code="agent.bounded-decision.configuration-invalid",
            )
        self.scenario = dict(scenario)
        self.client = NativeSystemOneClient(model.parameters["endpoint"], transport)
        self.authority: DecisionAuthority | None = None
        self._failure_execution_record: dict[str, Any] | None = None
        self._active_context: Any = None
        self._active_request: dict[str, Any] | None = None
        self._active_deadline = 0.0
        self._active_started = 0.0
        self._active_original_tick = 0.0
        self._local_cancellation_status = "not-requested"

    def bind_authority(self, authority: DecisionAuthority) -> None:
        self.authority = authority

    def cancel_local(self, request_id: str) -> str:
        """Revoke local admission and optionally ask a capable transport to stop."""
        if self.authority is not None:
            self.authority.revoke("cancelled")
        cancel = getattr(self.transport, "cancel_request", None)
        if callable(cancel):
            def attempt_transport_cancel() -> None:
                try:
                    cancel(request_id)
                except Exception:
                    self._local_cancellation_status = "transport-cancellation-failed"

            Thread(
                target=attempt_transport_cancel,
                name="mininet-ai-bounded-cancel",
                daemon=True,
            ).start()
            self._local_cancellation_status = "transport-cancellation-attempted"
        else:
            self._local_cancellation_status = "local-authority-revoked"
        return self._local_cancellation_status

    def run(self, context: Any) -> BoundedExecutionResult:
        from mininet_ai.sdk import ActionProposal, AgentResponse

        clock = self._monotonic
        metadata = (
            context.coordination.metadata
            if context.coordination is not None
            else {}
        )
        scheduled_tick = metadata.get("deadlineStartedAt")
        deadline = (
            float(scheduled_tick) + float(metadata.get("deadlineSeconds", 0.8))
            if scheduled_tick is not None
            else clock() + 0.8
        )
        if self.authority is None:
            self.authority = DecisionAuthority(deadline, clock)
        deadline = self.authority.deadline_tick
        started = clock()
        self._active_context = context
        self._active_deadline = deadline
        self._active_started = started
        self._active_original_tick = (
            float(scheduled_tick)
            if isinstance(scheduled_tick, (int, float))
            else started
        )
        choices_list = self.scenario["choices"]
        choices = {item["id"]: tuple(item["proposals"]) for item in choices_list}
        criteria = {item["id"]: item["description"] for item in choices_list}
        observation_bindings = self.definition.instance.decision_inputs
        latest = {
            logical_name: {
                "source": source_name,
                "availability": "available"
                if source_name in context.observations
                else "missing",
                "value": context.observations.get(source_name),
                "observedAt": None,
            }
            for logical_name, source_name in observation_bindings.items()
        }
        tick_id = (
            context.coordination.correlation_id
            if context.coordination is not None
            else context.invocation_id
        )
        decision_context = {
            "apiVersion": "mininet-ai/bounded-decision-context/v1alpha1",
            "scenario": self.scenario["metadata"],
            "identity": {
                "runId": context.run_id,
                "agentId": context.agent_id,
                "invocationId": context.invocation_id,
                "tickId": tick_id,
                "invokedAt": context.invoked_at.isoformat(),
            },
            "paths": self.scenario["paths"],
            "latestObservations": latest,
            "history": {
                "requiredRounds": 3,
                "completedRounds": [],
                "gaps": [
                    "scheduled round history is not available from the agent-runtime contract"
                ],
            },
            "policy": {
                **self.scenario["policy"],
                "current": self._inspect_policy(
                    context.run_id,
                    timeout_seconds=max(0.0, deadline - clock()),
                ),
            },
        }
        state = json.dumps(
            decision_context,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._active_request = {
            "url": f"{self.client.endpoint}/v1/systemone",
            "state": state,
            "instructions": self.scenario["instructions"],
            "criteria": criteria,
        }
        remaining_budget = deadline - clock()
        if remaining_budget <= 0:
            self.authority.revoke("timeout")
            self._retain_failure(context, "timeout", phase="input-assembly")
            raise AgentRuntimeError(
                "bounded decision deadline expired during input assembly",
                code="agent.bounded-decision.timeout",
                agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        inference_started = clock()
        try:
            result = self.client.decide(
                state=state,
                instructions=self.scenario["instructions"],
                criteria=criteria,
                choices=choices,
                timeout_seconds=remaining_budget,
                request_id=context.invocation_id,
            )
        except Exception as error:
            if self.authority.revocation == "cancelled":
                reason = "cancelled"
            elif (
                getattr(error, "code", None) == "agent.bounded-decision.timeout"
                or clock() >= deadline
            ):
                reason = "timeout"
                self.authority.revoke(reason)
            else:
                reason = "failed"
            self._retain_failure(context, reason, phase="inference")
            raise
        if result.choice_id == "no_action" and result.proposals:
            raise AgentRuntimeError(
                "no_action unexpectedly mapped to proposals",
                code="agent.bounded-decision.native-contract-violation",
            )
        completed_tick = clock()
        revoked = self.authority.validate_response()
        if revoked is not None or completed_tick >= deadline:
            reason = revoked or "timeout"
            self.authority.revoke(reason)
            self._retain_failure(
                context,
                reason,
                phase="inference",
                native_response=result.native_response or {},
                late=True,
            )
            raise AgentRuntimeError(
                "bounded decision result arrived after cancellation or its admission deadline; late result discarded",
                code=(
                    "agent.bounded-decision.cancelled"
                    if reason == "cancelled"
                    else "agent.bounded-decision.timeout"
                ),
                agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        policy_state = self._reconciliation.get("policy", "unknown")
        if result.choice_id == "install_recovery" and policy_state == "unknown":
            self._retain_failure(
                context,
                "reconciliation-failed",
                phase="reconciliation",
                native_response=result.native_response or {},
                selected_choice=result.choice_id,
            )
            assert self._failure_execution_record is not None
            self._failure_execution_record["runtime"]["boundedDecision"][
                "reconciliation"
            ] = self._reconciliation
            raise AgentRuntimeError(
                "scenario-owned rule state is unknown; recovery retry refused",
                code="agent.bounded-decision.reconciliation-failed",
                agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        if result.choice_id == "install_recovery" and policy_state == "verified-recovery":
            self._retain_failure(
                context,
                "redundant-recovery",
                phase="reconciliation",
                native_response=result.native_response or {},
                selected_choice=result.choice_id,
            )
            assert self._failure_execution_record is not None
            self._failure_execution_record["runtime"]["boundedDecision"][
                "reconciliation"
            ] = self._reconciliation
            raise AgentRuntimeError(
                "recovery was selected after the full policy was already verified",
                code="agent.bounded-decision.redundant-recovery",
                agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        if result.choice_id == "install_recovery":
            retry_evidence = self._recheck_recovery_evidence(
                context.run_id,
                timeout_seconds=max(0.0, deadline - clock()),
            )
            completed_tick = clock()
            revoked = self.authority.validate_response()
            if revoked is not None or completed_tick >= deadline:
                reason = revoked or "timeout"
                self.authority.revoke(reason)
                self._retain_failure(
                    context,
                    reason,
                    phase="reconciliation",
                    native_response=result.native_response or {},
                    late=True,
                    selected_choice=result.choice_id,
                )
                raise AgentRuntimeError(
                    "bounded decision deadline or cancellation occurred during recovery evidence recheck",
                    code=(
                        "agent.bounded-decision.cancelled"
                        if reason == "cancelled"
                        else "agent.bounded-decision.timeout"
                    ),
                    agent_id=context.agent_id,
                    invocation_id=context.invocation_id,
                )
            if retry_evidence["eligible"] is not True:
                refusal_code = str(retry_evidence["refusalCode"])
                self._retain_failure(
                    context,
                    "retry-ineligible",
                    phase="reconciliation",
                    native_response=result.native_response or {},
                    selected_choice=result.choice_id,
                )
                assert self._failure_execution_record is not None
                decision = self._failure_execution_record["runtime"]["boundedDecision"]
                decision["reconciliation"] = self._reconciliation
                decision["refusal"] = {
                    "code": refusal_code,
                    "message": retry_evidence["message"],
                    "evidence": retry_evidence,
                }
                raise AgentRuntimeError(
                    str(retry_evidence["message"]),
                    code="agent.bounded-decision.retry-ineligible",
                    agent_id=context.agent_id,
                    invocation_id=context.invocation_id,
                )
        elapsed = max(0.0, completed_tick - inference_started)
        present = set(self._reconciliation.get("presentIndexes", ()))
        remaining_indices = tuple(
            index for index in range(len(result.proposals)) if index not in present
        ) if result.choice_id == "install_recovery" else ()
        if result.choice_id == "install_recovery" and policy_state == "partial":
            proposals = tuple(result.proposals[index] for index in remaining_indices)
            proposal_ids = tuple(f"bounded-{index + 1}" for index in remaining_indices)
        else:
            proposals = result.proposals
            proposal_ids = tuple(f"bounded-{index + 1}" for index in range(len(proposals)))
        record = BoundedDecisionRecord(
            runId=context.run_id,
            agentId=context.agent_id,
            invocationId=context.invocation_id,
            correlationId=(
                context.coordination.correlation_id
                if context.coordination
                else context.invocation_id
            ),
            scenario=self.scenario,
            effectiveConfiguration={
                "model": self.model.name,
                "provider": self.model.provider,
                "parameters": self.model.parameters,
            },
            request={
                "url": f"{self.client.endpoint}/v1/systemone",
                "state": state,
                "instructions": self.scenario["instructions"],
                "criteria": criteria,
            },
            response=result.native_response or {},
            selectedChoice=result.choice_id,
            modelProvenance={
                "startup": self.startup,
                "expectedIdentity": self.model.parameters["expectedIdentity"],
                "verification": self.startup.get("status", "unverified"),
                "artifactVerification": "unverified",
                "serverBuild": "unavailable",
                "hardware": "unavailable",
            },
            timings={
                "decisionSeconds": elapsed,
                "inputAssemblySeconds": max(0.0, inference_started - started),
                "startedTick": (
                    float(scheduled_tick) if scheduled_tick is not None else started
                ),
                "completedTick": completed_tick,
                "deadlineTick": deadline,
                "clockDomain": "process-monotonic",
                "invokedAt": context.invoked_at.isoformat(),
                "phaseTransitions": [
                    {"phase": "input-assembly", "tick": started},
                    {"phase": "inference", "tick": inference_started},
                    {"phase": "decision-complete", "tick": completed_tick},
                ],
            },
            proposalMapping={
                "choice": result.choice_id,
                "proposals": [
                    {
                        "id": proposal_ids[index],
                        "bundleStep": int(proposal_ids[index].split("-")[1]),
                        "capability": proposal["capability"],
                        "target": proposal["target"],
                        "arguments": proposal["arguments"],
                    }
                    for index, proposal in enumerate(proposals)
                ],
            },
            reconciliation=self._reconciliation,
            authorization={"status": "pending" if result.proposals else "not-required"},
            admission={"status": "pending" if result.proposals else "not-required"},
            actionOutcomes=(),
            gaps=(
                "scheduled round history is not available from the agent-runtime contract",
                "observation timestamps are unavailable from the agent-runtime contract",
                "server build and hardware provenance are unavailable",
                "full model artifact SHA-256 has not been independently verified",
                "remote inference terminal status is unknown; local timeout/discard does not prove remote termination",
                *(
                    ()
                    if self.startup.get("status") == "verified"
                    else ("model/server identity is not fully verifiable",)
                ),
            ),
        )
        execution_record = AgentExecutionRecord(
            executionKind="bounded-decision",
            outcome="succeeded",
            runtime={"boundedDecision": record.model_dump(mode="json", by_alias=True)},
        ).model_dump(mode="json", by_alias=True)
        self._last_execution_record = execution_record
        response = AgentResponse(
            message=f"bounded decision selected {result.choice_id}",
            proposals=tuple(
                ActionProposal(
                    id=proposal_ids[index],
                    capability=proposal["capability"],
                    target=proposal["target"],
                    arguments=proposal["arguments"],
                )
                for index, proposal in enumerate(proposals)
            ),
            metadata={
                "executionKind": "bounded-decision",
                "selectedChoice": result.choice_id,
                "executionRecord": execution_record,
            },
        )
        return BoundedExecutionResult(
            response=response,
            model_queueing_seconds=0.0,
            runtime_name="bounded-decision",
            runtime_data=execution_record,
            authority=self.authority,
        )

    def _inspect_policy(
        self, run_id: str, *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        inspector = getattr(self._substrate, "inspect_scenario_actions", None)
        if timeout_seconds is not None and timeout_seconds <= 0:
            states = tuple("unreadable" for _ in self._bundle_requests)
            detail = "decision deadline expired before scenario rule inspection"
            status = "expired"
        elif not callable(inspector):
            states = tuple("unknown" for _ in self._bundle_requests)
            detail = "substrate does not provide trusted scenario rule inspection"
            status = "unsupported"
        else:
            try:
                states = cast(
                    tuple[str, ...],
                    tuple(
                        cast(Any, inspector)(
                            run_id,
                            self._bundle_requests,
                            timeout_seconds=timeout_seconds,
                        )
                    ),
                )
                if len(states) != len(self._bundle_requests):
                    raise ValueError("inspection returned an incomplete rule state")
                detail = None
                status = "recorded"
            except Exception as error:
                states = tuple("unreadable" for _ in self._bundle_requests)
                detail = f"{type(error).__name__}: {error}"
                status = "unreadable"
        reconciled = reconcile_bundle(states)
        self._reconciliation = {
            "identity": {
                "runId": run_id,
                "scenario": self.scenario["metadata"].get("name"),
                "bundleRequestIds": [request.id for request in self._bundle_requests],
            },
            "inspectionStatus": status,
            "policy": reconciled.policy,
            "stepStates": list(states),
            "presentIndexes": list(reconciled.present),
            "missingIndexes": list(reconciled.missing),
            "failure": detail,
        }
        return self._reconciliation

    def _recheck_recovery_evidence(
        self, run_id: str, *, timeout_seconds: float
    ) -> dict[str, Any]:
        """Refresh bound observations after inference; unknown/stale evidence refuses admission."""
        if timeout_seconds <= 0 or self._substrate is None:
            return {
                "eligible": False,
                "refusalCode": "evidence-unavailable",
                "message": "recovery evidence cannot be refreshed before admission",
                "observations": {},
            }
        bindings = self.definition.instance.decision_inputs
        refreshed: dict[str, Any] = {}
        max_age = duration_seconds(self.scenario["observations"]["maxAge"])
        try:
            for source_name in dict.fromkeys(bindings.values()):
                query = ObservationQuery(
                    name=source_name,
                    targets=self.definition.instance.attachment.targets,
                )
                observed = self._substrate.observe(run_id, query)
                age = (datetime.now(UTC) - observed.observed_at).total_seconds()
                if observed.run_id != run_id or observed.query != query:
                    raise ValueError(f"observation {source_name!r} identity mismatch")
                if age < 0 or age > max_age:
                    stale_observation = {
                        "source": source_name,
                        "observedAt": observed.observed_at.isoformat(),
                        "ageSeconds": age,
                        "values": observed.values,
                    }
                    self._reconciliation["retryEvidence"] = {
                        "observations": refreshed,
                        "staleObservation": stale_observation,
                    }
                    return {
                        "eligible": False,
                        "refusalCode": "stale-evidence",
                        "message": f"recovery observation {source_name!r} is stale or future-dated",
                        "observations": refreshed,
                        "staleObservation": stale_observation,
                    }
                refreshed[source_name] = {
                    "observedAt": observed.observed_at.isoformat(),
                    "ageSeconds": age,
                    "values": observed.values,
                }
        except Exception as error:
            return {
                "eligible": False,
                "refusalCode": "evidence-unavailable",
                "message": f"recovery evidence refresh failed: {error}",
                "observations": refreshed,
            }
        probes_source = bindings.get("pathProbes")
        probe_values = refreshed.get(probes_source, {}).get("values", {})
        targets = self.definition.instance.attachment.targets
        eligibility_by_target: dict[str, list[bool]] = {}
        malformed_targets: list[str] = []
        missing_targets: list[str] = []
        for target in targets:
            if not isinstance(probe_values, Mapping) or target not in probe_values:
                missing_targets.append(target)
                continue
            signals, malformed = _eligibility_signals(probe_values[target])
            if malformed:
                malformed_targets.append(target)
            if not signals:
                missing_targets.append(target)
            eligibility_by_target[target] = signals
        eligibility = [
            signal
            for target_signals in eligibility_by_target.values()
            for signal in target_signals
        ]
        self._reconciliation["retryEvidence"] = {
            "observations": refreshed,
            "eligibilitySignals": eligibility,
            "eligibilityByTarget": eligibility_by_target,
        }
        if malformed_targets:
            return {
                "eligible": False,
                "refusalCode": "malformed-eligibility",
                "message": "current recovery observations contain malformed eligibility evidence",
                "observations": refreshed,
                "eligibilitySignals": eligibility,
                "malformedTargets": malformed_targets,
            }
        if missing_targets:
            return {
                "eligible": False,
                "refusalCode": "missing-eligibility",
                "message": "current recovery observations omit required eligibility evidence",
                "observations": refreshed,
                "eligibilitySignals": eligibility,
                "missingTargets": missing_targets,
            }
        if len(set(eligibility)) > 1:
            return {
                "eligible": False,
                "refusalCode": "conflicting-eligibility",
                "message": "current recovery observations contain conflicting eligibility signals",
                "observations": refreshed,
                "eligibilitySignals": eligibility,
            }
        if any(value is False for value in eligibility):
            return {
                "eligible": False,
                "refusalCode": "ineligible-evidence",
                "message": "current recovery observations report ineligible retry evidence",
                "observations": refreshed,
                "eligibilitySignals": eligibility,
            }
        return {
            "eligible": True,
            "refusalCode": "",
            "message": "fresh current recovery evidence permits retry admission",
            "observations": refreshed,
            "eligibilitySignals": eligibility,
        }

    def failure_response(self, error: Exception) -> Any | None:
        from mininet_ai.sdk import AgentResponse

        record = self._failure_execution_record
        if record is None:
            return None
        record["issue"] = {
            "code": getattr(error, "code", "agent.bounded-decision.failed"),
            "message": str(error) or type(error).__name__,
        }
        return AgentResponse(
            message="bounded decision failed before action admission",
            metadata={
                "executionKind": "bounded-decision",
                "selectedChoice": "unavailable",
                "executionRecord": record,
            },
        )

    def _retain_failure(
        self,
        context: Any,
        reason: str,
        *,
        phase: str,
        native_response: Mapping[str, Any] | None = None,
        late: bool = False,
        selected_choice: str = "unavailable",
    ) -> None:
        from mininet_ai.agents.bounded_contracts import AgentExecutionRecord

        now = self._monotonic()
        decision = BoundedDecisionRecord(
            runId=context.run_id,
            agentId=context.agent_id,
            invocationId=context.invocation_id,
            correlationId=(
                context.coordination.correlation_id
                if context.coordination
                else context.invocation_id
            ),
            scenario=self.scenario,
            effectiveConfiguration={
                "model": self.model.name,
                "provider": self.model.provider,
                "parameters": self.model.parameters,
            },
            request=self._active_request or {},
            response=dict(native_response or {}),
            selectedChoice=selected_choice,
            modelProvenance={
                "startup": self.startup,
                "expectedIdentity": self.model.parameters["expectedIdentity"],
                "verification": self.startup.get("status", "unverified"),
                "remoteTerminalStatus": "unknown",
            },
            timings={
                "startedTick": self._active_original_tick,
                "completedTick": now,
                "deadlineTick": self._active_deadline,
                "clockDomain": "run-monotonic",
                "invokedAt": context.invoked_at.isoformat(),
                "phaseTransitions": [
                    {"phase": "input-assembly", "tick": self._active_started},
                    {"phase": phase, "tick": now},
                ],
            },
            proposalMapping={"choice": "unavailable", "proposals": []},
            authorization={"status": "not-reached"},
            admission={"status": reason},
            actionOutcomes=(),
            reconciliation=self._reconciliation,
            cancellationStatus="cancelled" if reason == "cancelled" else "not-requested",
            lateResultDiscarded=late,
            gaps=(
                "remote inference terminal status is unknown",
                "no network action was admitted from this decision",
            ),
        )
        self._failure_execution_record = AgentExecutionRecord(
            executionKind="bounded-decision",
            outcome="failed",
            runtime={
                "boundedDecision": {
                    **decision.model_dump(mode="json", by_alias=True),
                    "timeoutStatus": "expired" if reason == "timeout" else "not-expired",
                    "remoteTerminalStatus": "unknown",
                    "localCancellationStatus": self._local_cancellation_status,
                }
            },
        ).model_dump(mode="json", by_alias=True)

    def record_action_results(
        self, context: Any, results: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        # Results are recorded by Mininet after the standard authorization,
        # arbitration, execution, and completion lifecycle has run.
        if not results:
            return None
        # The normalized execution record is carried through AgentResponse metadata.
        # This mutable JSON payload is the existing public completion envelope.
        record = self._last_execution_record
        record["runtime"]["boundedDecision"]["actionOutcomes"] = [
            {
                **result.model_dump(mode="json", by_alias=True),
                "stepStatus": (
                    "not-started"
                    if result.issue is not None
                    and result.issue.code
                    in {
                        "agent.bounded-decision.bundle-stopped",
                        "agent.bounded-decision.timeout",
                        "agent.bounded-decision.cancelled",
                    }
                    else "completed"
                    if result.status.value == "succeeded"
                    else "failed"
                    if result.status.value == "failed"
                    else "rejected"
                ),
            }
            for result in results
        ]
        authorization_results = []
        for result in results:
            issue_code = result.issue.code if result.issue is not None else None
            authorization_status = (
                "not-started"
                if issue_code == "agent.bounded-decision.bundle-stopped"
                else "rejected"
                if result.status.value == "rejected"
                and issue_code is not None
                and issue_code.startswith("capability.")
                else "authorized"
            )
            authorization_results.append(
                {
                    "requestId": result.request_id,
                    "status": authorization_status,
                    "issue": result.issue.model_dump(mode="json", by_alias=True)
                    if result.issue
                    else None,
                }
            )
        record["runtime"]["boundedDecision"]["authorization"] = {
            "status": "rejected"
            if any(item["status"] == "rejected" for item in authorization_results)
            else "authorized",
            "results": authorization_results,
        }
        admission_status = (
            "cancelled"
            if self.authority is not None and self.authority.revocation == "cancelled"
            else "expired"
            if self.authority is not None and self.authority.revocation == "timeout"
            else "rejected"
            if any(
                result.status.value == "rejected"
                and not (
                    result.issue is not None
                    and result.issue.code == "agent.bounded-decision.bundle-stopped"
                )
                for result in results
            )
            else "failed"
            if any(result.status.value == "failed" for result in results)
            else "completed"
        )
        record["runtime"]["boundedDecision"]["admission"] = {
            "status": admission_status,
            "results": [
                {
                    "requestId": result.request_id,
                    "status": result.status.value,
                    "issue": result.issue.model_dump(mode="json", by_alias=True)
                    if result.issue
                    else None,
                }
                for result in results
            ],
        }
        record["outcome"] = (
            "succeeded"
            if all(result.status.value == "succeeded" for result in results)
            else "failed"
        )
        decision = record["runtime"]["boundedDecision"]
        decision["cancellationStatus"] = (
            "cancelled"
            if self.authority is not None and self.authority.revocation == "cancelled"
            else "not-requested"
        )
        decision["timeoutStatus"] = (
            "expired"
            if self.authority is not None and self.authority.revocation == "timeout"
            else "not-expired"
        )
        decision["remoteTerminalStatus"] = "unknown"
        decision["localCancellationStatus"] = self._local_cancellation_status
        decision["timings"]["phaseTransitions"].extend(
            {"phase": "action-start", "tick": tick}
            for tick in (self.authority.action_starts if self.authority else ())
        )
        decision["incompleteEffects"] = [
            {
                "requestId": result.request_id,
                "status": result.status.value,
                "effects": result.effects.value,
            }
            for result in results
            if result.status.value != "succeeded"
            or result.effects.value not in {"changed", "unchanged"}
        ]
        if decision.get("selectedChoice") == "install_recovery":
            after = self._inspect_policy(context.run_id)
            decision["reconciliation"] = {
                **decision.get("reconciliation", {}),
                "afterActions": after,
            }
            if (
                all(result.status.value == "succeeded" for result in results)
                and after.get("policy") != "verified-recovery"
            ):
                record["outcome"] = "failed"
                decision["correctnessFailure"] = {
                    "code": "agent.bounded-decision.recovery-verification-failed",
                    "message": "all recovery actions completed but exact full policy verification failed",
                }
        return record
