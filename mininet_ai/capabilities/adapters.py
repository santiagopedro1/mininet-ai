"""Built-in adapters for substrate, process, and HTTP capabilities."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, cast
from urllib.parse import urlsplit

from pydantic import JsonValue, ValidationError

from mininet_ai.errors import RuntimeOperationError
from mininet_ai.sdk.contracts import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    ActionProposal,
    AgentContext,
    CapabilityOutcome,
    CapabilityProviderError,
)
from mininet_ai.specification.models import CapabilityDefinition
from mininet_ai.substrates.runtime import (
    ActionRequest,
    ActionResult,
    ObservationQuery,
    SubstrateRuntime,
)
from mininet_ai.transports import (
    HttpTransportError,
    HttpxJsonTransport,
    JsonHttpTransport,
)


_DEFAULT_OUTPUT_LIMIT = 1024 * 1024


def _payload(context: AgentContext, proposal: ActionProposal) -> dict[str, JsonValue]:
    return {
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "context": context.model_dump(mode="json", by_alias=True),
        "proposal": proposal.model_dump(mode="json", by_alias=True),
    }


class SubstrateActionProvider:
    """Map an authorized capability to a substrate action of the same name."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        definition: CapabilityDefinition,
        runtime: SubstrateRuntime,
    ) -> None:
        self._definition = definition
        self._runtime = runtime

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> ActionResult:
        try:
            return self._runtime.execute(
                context.run_id,
                ActionRequest(
                    id=proposal.id,
                    name=self._definition.metadata.name,
                    target=proposal.target,
                    parameters=proposal.arguments,
                    timeout_seconds=proposal.timeout_seconds,
                ),
            )
        except RuntimeOperationError as error:
            raise CapabilityProviderError(
                str(error),
                code=error.code,
            ) from error


class SubstrateObservationProvider:
    """Map an authorized capability to a substrate observation of the same name."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        definition: CapabilityDefinition,
        runtime: SubstrateRuntime,
    ) -> None:
        self._definition = definition
        self._runtime = runtime

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityOutcome:
        try:
            result = self._runtime.observe(
                context.run_id,
                ObservationQuery(
                    name=self._definition.metadata.name,
                    targets=(proposal.target,),
                    parameters=proposal.arguments,
                ),
            )
        except RuntimeOperationError as error:
            raise CapabilityProviderError(
                str(error),
                code=error.code,
            ) from error
        return CapabilityOutcome(output=result.values)


class ProcessCapabilityProvider:
    """Execute a JSON capability protocol without invoking a shell."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        max_output_bytes: int = _DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        if not command or any(
            not isinstance(item, str) or not item or "\x00" in item
            for item in command
        ):
            raise ValueError("process capability command must contain valid arguments")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self._command = tuple(command)
        self._environment = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            **dict(environment or {}),
        }
        self._cwd = cwd
        self._max_output_bytes = max_output_bytes

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityOutcome:
        request = json.dumps(_payload(context, proposal)).encode("utf-8")
        with tempfile.TemporaryFile() as input_stream:
            input_stream.write(request)
            input_stream.seek(0)
            try:
                process = subprocess.Popen(
                    self._command,
                    stdin=input_stream,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._cwd,
                    env=self._environment,
                    start_new_session=True,
                )
            except OSError as error:
                raise CapabilityProviderError(
                    f"could not start capability process: {error}",
                    code="capability.process.start-failed",
                ) from error
            stdout, stderr = self._read_bounded(
                process,
                timeout_seconds=proposal.timeout_seconds,
            )

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            suffix = f": {detail}" if detail else ""
            raise CapabilityProviderError(
                f"capability process exited with status {process.returncode}{suffix}",
                code="capability.process.failed",
            )
        try:
            value = json.loads(stdout.decode("utf-8"))
            return CapabilityOutcome.model_validate(value)
        except (UnicodeDecodeError, ValueError, ValidationError) as error:
            raise CapabilityProviderError(
                f"capability process returned an invalid response: {error}",
                code="capability.process.invalid-response",
            ) from error

    def _read_bounded(
        self,
        process: subprocess.Popen[bytes],
        *,
        timeout_seconds: float,
    ) -> tuple[bytes, bytes]:
        if process.stdout is None or process.stderr is None:
            self._terminate(process)
            raise CapabilityProviderError(
                "capability process did not expose output streams",
                code="capability.process.start-failed",
            )
        stdout_stream = process.stdout
        stderr_stream = process.stderr
        streams = {stdout_stream: bytearray(), stderr_stream: bytearray()}
        selector = selectors.DefaultSelector()
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate(process)
                    raise TimeoutError("capability process timed out")
                for key, _ in selector.select(min(remaining, 0.1)):
                    stream = cast(IO[bytes], key.fileobj)
                    chunk = os.read(stream.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    content = streams[stream]
                    content.extend(chunk)
                    if len(content) > self._max_output_bytes:
                        self._terminate(process)
                        raise CapabilityProviderError(
                            "capability process exceeded the output size limit",
                            code="capability.process.output-too-large",
                        )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(process)
                raise TimeoutError("capability process timed out")
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            self._terminate(process)
            raise TimeoutError("capability process timed out") from error
        finally:
            selector.close()
            for stream in streams:
                stream.close()
        return bytes(streams[stdout_stream]), bytes(streams[stderr_stream])

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        process_group = process.pid
        try:
            os.killpg(process_group, signal.SIGTERM)
        except ProcessLookupError:
            process.poll()
            return
        deadline = time.monotonic() + 0.5
        while ProcessCapabilityProvider._group_exists(process_group):
            process.poll()
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        if ProcessCapabilityProvider._group_exists(process_group):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            raise CapabilityProviderError(
                "capability process group could not be stopped",
                code="capability.process.cleanup-failed",
            ) from error
        kill_deadline = time.monotonic() + 1
        while ProcessCapabilityProvider._group_exists(process_group):
            if time.monotonic() >= kill_deadline:
                raise CapabilityProviderError(
                    "capability process group could not be stopped",
                    code="capability.process.cleanup-failed",
                )
            time.sleep(0.02)

    @staticmethod
    def _group_exists(process_group: int) -> bool:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


class ServiceCapabilityProvider:
    """Execute the versioned capability protocol over bounded JSON HTTP."""

    contract_version = AGENT_RUNTIME_CONTRACT_VERSION

    def __init__(
        self,
        endpoint: str,
        *,
        transport: JsonHttpTransport | None = None,
        headers: Mapping[str, str] | None = None,
        max_response_bytes: int = _DEFAULT_OUTPUT_LIMIT,
    ) -> None:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError(
                "service endpoint must be an HTTP(S) URL without credentials "
                "or a fragment"
            )
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._endpoint = endpoint
        self._transport = transport or HttpxJsonTransport()
        self._headers = {"content-type": "application/json", **dict(headers or {})}
        self._max_response_bytes = max_response_bytes

    def execute(
        self,
        context: AgentContext,
        proposal: ActionProposal,
    ) -> CapabilityOutcome:
        try:
            response = self._transport.post_json(
                self._endpoint,
                _payload(context, proposal),
                headers=self._headers,
                timeout_seconds=proposal.timeout_seconds,
                max_response_bytes=self._max_response_bytes,
            )
        except HttpTransportError as error:
            raise CapabilityProviderError(
                str(error),
                code=f"capability.service.{error.code}",
            ) from error
        try:
            return CapabilityOutcome.model_validate(response)
        except (ValueError, ValidationError) as error:
            raise CapabilityProviderError(
                f"capability service returned an invalid response: {error}",
                code="capability.service.invalid-response",
            ) from error
