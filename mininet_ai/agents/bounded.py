"""Explicit, native llama.cpp bounded-choice execution primitives."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from mininet_ai.errors import AgentRuntimeError


@dataclass(frozen=True)
class ChoiceResult:
    choice_id: str
    proposals: tuple[dict[str, Any], ...]
    probability: float | None = None
    confidence: float | None = None


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
    answer = answers.get("next_action") if isinstance(answers, Mapping) else None
    if not isinstance(answer, Mapping) or answer.get("type") != "choice":
        raise AgentRuntimeError(
            "native response must contain answers.next_action of type choice",
            code="agent.bounded-decision.invalid-output",
        )
    choice_id = answer.get("choice")
    if not isinstance(choice_id, str) or choice_id not in choices:
        raise AgentRuntimeError(
            "native response contains a missing or unknown choice",
            code="agent.bounded-decision.invalid-output",
        )
    if choice_id != "no_action":
        raise AgentRuntimeError(
            f"choice {choice_id!r} is not supported by this runtime slice",
            code="agent.bounded-decision.choice-not-supported",
        )
    if choices[choice_id]:
        raise AgentRuntimeError(
            "the no_action choice must have no proposals",
            code="agent.bounded-decision.configuration-invalid",
        )
    diagnostics: dict[str, float | None] = {}
    for field, maximum in (("probability", 1.0), ("confidence", 1.0)):
        value = answer.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= maximum
        ):
            raise AgentRuntimeError(
                f"native response {field} must be finite and between 0 and 1",
                code="agent.bounded-decision.invalid-output",
            )
        diagnostics[field] = float(value) if value is not None else None
    return ChoiceResult(
        choice_id=choice_id,
        proposals=tuple(choices[choice_id]),
        **diagnostics,
    )


class DecisionTransport(Protocol):
    def post_json(self, url: str, payload: Mapping[str, Any], **kwargs: Any) -> Any: ...


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
            raise AgentRuntimeError(
                f"native llama.cpp decision request failed: {error}",
                code="agent.bounded-decision.service-unavailable",
            ) from error
        return map_choice(response, choices)
