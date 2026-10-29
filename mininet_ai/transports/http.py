"""Bounded JSON-over-HTTP transport shared by provider adapters."""

from __future__ import annotations

import json
from base64 import b64encode
from collections.abc import Mapping
from threading import Lock
from typing import Protocol, runtime_checkable

import httpx
from pydantic import JsonValue


class HttpTransportError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int | None = None,
        response_body: str | None = None,
        response_body_base64: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.response_body = response_body
        self.response_body_base64 = response_body_base64


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
        self._active_lock = Lock()
        self._active_clients: dict[str, httpx.Client] = {}

    def cancel_request(self, request_id: str) -> bool:
        """Close the local HTTP client for a request; remote stop is unverified."""
        with self._active_lock:
            client = self._active_clients.get(request_id)
        if client is None:
            return False
        client.close()
        return True

    def post_json(
        self,
        url: str,
        payload: Mapping[str, JsonValue],
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
        request_id: str | None = None,
    ) -> JsonValue:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("HTTP timeout and response limit must be positive")
        try:
            client = httpx.Client(
                transport=self._transport,
                follow_redirects=False,
                timeout=timeout_seconds,
            )
            if request_id is not None:
                with self._active_lock:
                    self._active_clients[request_id] = client
            try:
                with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                ) as response:
                    return _read_json_response(response, max_response_bytes)
            finally:
                if request_id is not None:
                    with self._active_lock:
                        if self._active_clients.get(request_id) is client:
                            del self._active_clients[request_id]
                client.close()
        except httpx.TimeoutException as error:
            raise TimeoutError("HTTP request timed out") from error
        except HttpTransportError:
            raise
        except httpx.HTTPError as error:
            raise HttpTransportError(
                f"HTTP request failed: {error}",
                code="request-failed",
            ) from error

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> JsonValue:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise ValueError("HTTP timeout and response limit must be positive")
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    follow_redirects=False,
                    timeout=timeout_seconds,
                ) as client,
                client.stream("GET", url, headers=headers) as response,
            ):
                return _read_json_response(response, max_response_bytes)
        except httpx.TimeoutException as error:
            raise TimeoutError("HTTP request timed out") from error
        except HttpTransportError:
            raise
        except httpx.HTTPError as error:
            raise HttpTransportError(
                f"HTTP request failed: {error}", code="request-failed"
            ) from error


def _read_json_response(response: httpx.Response, limit: int) -> JsonValue:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise HttpTransportError(
                "HTTP response exceeded the configured size limit",
                code="response-too-large",
                status_code=response.status_code,
            )
    if not 200 <= response.status_code < 300:
        raise HttpTransportError(
            f"HTTP endpoint returned status {response.status_code}",
            code="http-status",
            status_code=response.status_code,
            response_body=body.decode("utf-8", errors="replace"),
            response_body_base64=b64encode(body).decode("ascii"),
        )
    try:
        return json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise HttpTransportError(
            f"HTTP endpoint returned invalid JSON: {error}",
            code="invalid-json",
            status_code=response.status_code,
            response_body=body.decode("utf-8", errors="replace"),
            response_body_base64=b64encode(body).decode("ascii"),
        ) from error


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
