"""Bounded JSON-over-HTTP transport shared by provider adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import httpx
from pydantic import JsonValue


class HttpTransportError(Exception):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class JsonHttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: Mapping[str, JsonValue],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> JsonValue:
        """POST a JSON document and return a bounded JSON response."""
        ...


class HttpxJsonTransport:
    """httpx implementation with no redirects and a bounded response body."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def post_json(
        self,
        url: str,
        payload: Mapping[str, JsonValue],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> JsonValue:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("HTTP timeout and response limit must be positive")
        try:
            with httpx.Client(
                transport=self._transport,
                follow_redirects=False,
                timeout=timeout_seconds,
            ) as client:
                with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise HttpTransportError(
                            f"HTTP endpoint returned status {response.status_code}",
                            code="http-status",
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_response_bytes:
                            raise HttpTransportError(
                                "HTTP response exceeded the configured size limit",
                                code="response-too-large",
                            )
        except httpx.TimeoutException as error:
            raise TimeoutError("HTTP request timed out") from error
        except HttpTransportError:
            raise
        except httpx.HTTPError as error:
            raise HttpTransportError(
                f"HTTP request failed: {error}",
                code="request-failed",
            ) from error

        try:
            return json.loads(
                body.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise HttpTransportError(
                f"HTTP endpoint returned invalid JSON: {error}",
                code="invalid-json",
            ) from error


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")
