"""Explicit, native llama.cpp bounded-choice execution primitives."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from mininet_ai.agents.bounded_contracts import (
    AgentExecutionRecord,
    BoundedDecisionRecord,
)
from mininet_ai.errors import AgentRuntimeError


@dataclass(frozen=True)
class ChoiceResult:
    choice_id: str
    proposals: tuple[dict[str, Any], ...]
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
    diagnostics: dict[str, float | None] = {}
    for field, maximum in (("probability", 1.0), ("confidence", 1.0)):
        if field not in answer:
            diagnostics[field] = None
            continue
        value = answer[field]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= maximum
        ):
            raise AgentRuntimeError(
                f"native response {field} must be finite and between 0 and 1",
                code="agent.bounded-decision.invalid-output",
            )
        diagnostics[field] = float(value)
    return ChoiceResult(
        choice_id=choice_id,
        proposals=tuple(choices[choice_id]),
        native_response=dict(response),
        **diagnostics,
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
        criteria: Sequence[str],
        choices: Mapping[str, Sequence[dict[str, Any]]],
        timeout_seconds: float,
    ) -> ChoiceResult:
        if tuple(choices) != tuple(criteria):
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
                    "criteria": list(criteria),
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
        criteria: Sequence[str],
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


class BoundedDecisionAdapter:
    """Run one explicitly compiled bounded decision without constructing Agno."""

    contract_version = "mininet-ai/agent-runtime/v1alpha4"

    def __init__(
        self,
        definition: Any,
        transport: DecisionTransport,
        startup: Mapping[str, Any] | None = None,
        monotonic_clock: Any = monotonic,
    ) -> None:
        self.definition = definition
        self.transport = transport
        self.startup = dict(startup or {})
        self._monotonic = monotonic_clock
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
        started = clock()
        choices_list = self.scenario["choices"]
        choices = {item["id"]: tuple(item["proposals"]) for item in choices_list}
        criteria = tuple(item["id"] for item in choices_list)
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
            "policy": self.scenario["policy"],
        }
        state = json.dumps(
            decision_context,
            sort_keys=True,
            separators=(",", ":"),
        )
        remaining_budget = deadline - clock()
        if remaining_budget <= 0:
            raise AgentRuntimeError(
                "bounded decision deadline expired during input assembly",
                code="agent.bounded-decision.timeout",
                agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        inference_started = clock()
        result = self.client.decide(
            state=state,
            instructions=self.scenario["instructions"],
            criteria=criteria,
            choices=choices,
            timeout_seconds=remaining_budget,
        )
        if result.choice_id == "no_action" and result.proposals:
            raise AgentRuntimeError(
                "no_action unexpectedly mapped to proposals",
                code="agent.bounded-decision.native-contract-violation",
            )
        completed_tick = clock()
        if completed_tick >= deadline:
            raise AgentRuntimeError(
                "bounded decision result arrived after its admission deadline; late result discarded",
                code="agent.bounded-decision.timeout",
                agent_id=context.agent_id,
                invocation_id=context.invocation_id,
            )
        elapsed = max(0.0, completed_tick - inference_started)
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
                "criteria": list(criteria),
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
                        "id": f"bounded-{index + 1}",
                        "capability": proposal["capability"],
                        "target": proposal["target"],
                        "arguments": proposal["arguments"],
                    }
                    for index, proposal in enumerate(result.proposals)
                ],
            },
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
                    id=f"bounded-{index + 1}",
                    capability=proposal["capability"],
                    target=proposal["target"],
                    arguments=proposal["arguments"],
                )
                for index, proposal in enumerate(result.proposals)
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
        )

    def record_action_results(
        self, context: Any, results: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        # Results are recorded by Mininet after the standard authorization,
        # arbitration, execution, and completion lifecycle has run.
        del context
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
                    and result.issue.code == "agent.bounded-decision.bundle-stopped"
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
            "rejected"
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
        return record
