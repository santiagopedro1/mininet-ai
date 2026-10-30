"""The ``mininet-ai/v1alpha4`` public schema.

The models intentionally describe logical placement. Process isolation and real
network attachment are substrate/runtime concerns introduced in later phases.
"""

from __future__ import annotations

import re
import warnings
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address
from typing import Annotated, Any, Literal, TypedDict
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
    with_config,
)

from mininet_ai.durations import duration_seconds

API_VERSION: Literal["mininet-ai/v1alpha4"] = "mininet-ai/v1alpha4"
NAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_.-]*$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SpecificationDeprecationWarning(FutureWarning):
    """Visible warning for bounded acceptance of old source documents."""


class VersionedDocument(StrictModel):
    api_version: Literal["mininet-ai/v1alpha4", "mininet-ai/v1alpha5"] = Field(
        alias="apiVersion"
    )

    @field_validator("api_version", mode="wrap")
    @classmethod
    def supported_version(
        cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> str:
        if value == "mininet-ai/v1alpha3":
            # Saved snapshots are executable inputs, never source migrations.
            if info.context and "legacy_saved_plan" in info.context:
                return value if info.context["legacy_saved_plan"] else handler(value)
            source = (
                info.context.get("source", "Python input")
                if info.context
                else "Python input"
            )
            warnings.warn(
                f"{source}: {cls.__name__} mininet-ai/v1alpha3 is deprecated; "
                "validate against current rules and migrate to mininet-ai/v1alpha4",
                SpecificationDeprecationWarning,
                stacklevel=3,
            )
            value = API_VERSION
        return handler(value)


Name = Annotated[str, Field(pattern=NAME_PATTERN, min_length=1, max_length=128)]


class Metadata(StrictModel):
    name: Name
    labels: dict[str, str] = Field(default_factory=dict)
    description: str | None = None


class ResourceKind(StrEnum):
    NETWORK = "network"
    REGION = "region"
    CONTROLLER = "controller"
    CONTROLLER_DOMAIN = "controller-domain"
    SWITCH = "switch"
    HOST = "host"
    PORT = "port"
    LINK = "link"
    FLOW = "flow"


class ResourceBase(StrictModel):
    name: Name
    labels: dict[str, str] = Field(default_factory=dict)
    parent: Name | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class NetworkResource(ResourceBase):
    kind: Literal[ResourceKind.NETWORK]


class RegionResource(ResourceBase):
    kind: Literal[ResourceKind.REGION]


class FlowResource(ResourceBase):
    kind: Literal[ResourceKind.FLOW]


class ControllerType(StrEnum):
    BUILTIN = "builtin"
    REMOTE = "remote"


class ControllerProtocol(StrEnum):
    TCP = "tcp"
    SSL = "ssl"


class ControllerResource(ResourceBase):
    kind: Literal[ResourceKind.CONTROLLER]
    type: ControllerType
    address: IPv4Address | IPv6Address | None = None
    protocol: ControllerProtocol = ControllerProtocol.TCP
    port: int = Field(default=6653, ge=1, le=65535)

    @model_validator(mode="after")
    def remote_controller_has_address(self) -> ControllerResource:
        if self.type == ControllerType.REMOTE and self.address is None:
            raise ValueError("a remote controller requires an address")
        return self


class ControllerDomainResource(ResourceBase):
    kind: Literal[ResourceKind.CONTROLLER_DOMAIN]
    controllers: list[Name] = Field(min_length=1)


class OpenFlowProtocol(StrEnum):
    OPENFLOW_10 = "OpenFlow10"
    OPENFLOW_11 = "OpenFlow11"
    OPENFLOW_12 = "OpenFlow12"
    OPENFLOW_13 = "OpenFlow13"
    OPENFLOW_14 = "OpenFlow14"
    OPENFLOW_15 = "OpenFlow15"


class SwitchPort(StrictModel):
    name: Name | None = None
    number: int | Literal["auto"] = "auto"
    mtu: int = Field(default=1500, ge=576, le=65535)

    @field_validator("number")
    @classmethod
    def port_number_is_positive(cls, value: int | str) -> int | str:
        if isinstance(value, int) and value < 1:
            raise ValueError("switch port number must be positive")
        return value


MAC_ADDRESS_PATTERN = r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$"
MAC_PREFIX_PATTERN = r"^(?:[0-9a-fA-F]{2}:){2}[0-9a-fA-F]{2}$"


def _mac_octets(value: str) -> tuple[int, ...]:
    return tuple(int(part, 16) for part in value.split(":"))


class HostInterface(StrictModel):
    name: Name | None = None
    ipv4: IPv4Interface | Literal["auto", "none"] = "auto"
    mac: str = "auto"
    mtu: int = Field(default=1500, ge=576, le=65535)

    @field_validator("mac")
    @classmethod
    def valid_unicast_mac_or_auto(cls, value: str) -> str:
        if value == "auto":
            return value
        if not re.fullmatch(MAC_ADDRESS_PATTERN, value):
            raise ValueError("MAC must use six colon-separated hexadecimal octets")
        normalized = value.lower()
        if _mac_octets(normalized)[0] & 1:
            raise ValueError("MAC must be a unicast address")
        return normalized


class SwitchFailMode(StrEnum):
    STANDALONE = "standalone"
    SECURE = "secure"


class SwitchDatapath(StrEnum):
    KERNEL = "kernel"
    USERSPACE = "userspace"


class SwitchResource(ResourceBase):
    kind: Literal[ResourceKind.SWITCH]
    dpid: str | None = None
    fail_mode: SwitchFailMode = Field(default=SwitchFailMode.SECURE, alias="failMode")
    datapath: SwitchDatapath = SwitchDatapath.KERNEL
    controllers: list[Name] = Field(default_factory=list)
    protocols: list[OpenFlowProtocol] = Field(default_factory=list)
    ports: list[SwitchPort] = Field(default_factory=list)

    @field_validator("dpid")
    @classmethod
    def valid_dpid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[0-9a-fA-F]{1,16}", value):
            raise ValueError("DPID must contain 1 to 16 hexadecimal digits")
        if int(value, 16) == 0:
            raise ValueError("DPID must be non-zero")
        return value.lower().zfill(16)


class HostResource(ResourceBase):
    kind: Literal[ResourceKind.HOST]
    interfaces: list[HostInterface] = Field(default_factory=list)
    default_route: str | None = Field(default=None, alias="defaultRoute")


TopologyResource = Annotated[
    NetworkResource
    | RegionResource
    | ControllerResource
    | ControllerDomainResource
    | SwitchResource
    | HostResource
    | FlowResource,
    Field(discriminator="kind"),
]


class IPv4Allocation(StrictModel):
    subnet: IPv4Network = IPv4Network("10.0.0.0/24")
    strategy: Literal["sequential"] = "sequential"


class MACAllocation(StrictModel):
    prefix: str = "02:00:00"
    strategy: Literal["sequential"] = "sequential"

    @field_validator("prefix")
    @classmethod
    def valid_local_unicast_prefix(cls, value: str) -> str:
        if not re.fullmatch(MAC_PREFIX_PATTERN, value):
            raise ValueError("MAC prefix must contain three hexadecimal octets")
        normalized = value.lower()
        first = _mac_octets(normalized)[0]
        if first & 0b11 != 0b10:
            raise ValueError("MAC prefix must be locally administered and unicast")
        return normalized


class Addressing(StrictModel):
    ipv4: IPv4Allocation = Field(default_factory=IPv4Allocation)
    mac: MACAllocation = Field(default_factory=MACAllocation)


Duration = Annotated[
    str,
    Field(pattern=r"^(?:0|[0-9]+(?:\.[0-9]+)?)(?:us|ms|s)$"),
]


class LinkEndpoint(StrictModel):
    node: Name
    adapter: Name | None = None


class Link(StrictModel):
    name: Name
    endpoints: tuple[LinkEndpoint, LinkEndpoint]
    labels: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    bandwidth: float | None = Field(default=None, gt=0)
    delay: Duration | None = None
    jitter: Duration | None = None
    loss: float = Field(default=0, ge=0, le=100)
    max_queue_size: int | None = Field(default=None, alias="maxQueueSize", gt=0)

    @model_validator(mode="after")
    def endpoints_differ(self) -> Link:
        if self.endpoints[0].node == self.endpoints[1].node:
            raise ValueError("link endpoint nodes must be different")
        return self


class Topology(StrictModel):
    addressing: Addressing = Field(default_factory=Addressing)
    resources: list[TopologyResource] = Field(min_length=1)
    links: list[Link] = Field(default_factory=list)


class Substrate(StrictModel):
    driver: Name = "fake"
    topology: Topology | str
    options: dict[str, Any] = Field(default_factory=dict)


class Implementation(StrictModel):
    type: Literal["declarative", "python"] = "declarative"
    entrypoint: str | None = None

    @model_validator(mode="after")
    def python_needs_entrypoint(self) -> Implementation:
        if self.type == "python" and not self.entrypoint:
            raise ValueError("a Python implementation requires an entrypoint")
        return self


_HTTP_ENDPOINT = TypeAdapter(HttpUrl)


def validate_ollama_host(value: object) -> str:
    """Validate an explicit endpoint without normalizing serialized input."""
    if (
        not isinstance(value, str)
        or not value
        or any(
            char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\"
            for char in value
        )
    ):
        raise ValueError("Ollama host must be a non-empty HTTP/HTTPS URL")
    try:
        parsed = urlsplit(value)
        _HTTP_ENDPOINT.validate_python(value)
    except ValueError as error:
        raise ValueError("Ollama host must be a valid HTTP/HTTPS URL") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Ollama host must be an HTTP/HTTPS URL with a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            "Ollama host must not contain credentials; use environment variables"
        )
    return value


class ModelConfiguration(StrictModel):
    provider: Name
    name: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def ollama_host_is_valid(
        cls, parameters: dict[str, Any], info: ValidationInfo
    ) -> dict[str, Any]:
        if info.data.get("provider") == "ollama" and "host" in parameters:
            try:
                validate_ollama_host(parameters["host"])
            except ValueError as error:
                # Keep the offending field in nested/union validation locations.
                raise ValidationError.from_exception_data(
                    cls.__name__,
                    [
                        {
                            "type": "value_error",
                            "loc": ("host",),
                            "input": None,
                            "ctx": {"error": error},
                        }
                    ],
                ) from error
        if info.data.get("provider") == "llamacpp-systemone":
            unknown = set(parameters) - {"endpoint", "expectedIdentity"}
            if unknown:
                raise ValueError(
                    "llamacpp-systemone parameters only allow endpoint and expectedIdentity"
                )
            endpoint = parameters.get("endpoint")
            if not isinstance(endpoint, str):
                raise ValueError("llamacpp-systemone requires parameters.endpoint")
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "llamacpp-systemone endpoint must be an HTTP/HTTPS base URL without credentials, query, or fragment"
                )
            identity = parameters.get("expectedIdentity")
            required = {"repository", "revision", "artifact", "sha256"}
            if not isinstance(identity, dict) or not required.issubset(identity):
                raise ValueError(
                    "llamacpp-systemone expectedIdentity requires repository, revision, artifact, and sha256"
                )
            if set(identity) - (required | {"serverVersion", "serverCommit"}):
                raise ValueError("expectedIdentity contains unsupported fields")
            if any(
                not isinstance(identity[key], str) or not identity[key]
                for key in required
            ):
                raise ValueError(
                    "expectedIdentity repository, revision, artifact, and sha256 must be non-empty strings"
                )
            if not re.fullmatch(r"[0-9a-fA-F]{64}", str(identity["sha256"])):
                raise ValueError("expectedIdentity.sha256 must be a SHA-256 hex digest")
            if any(
                key in identity
                and (not isinstance(identity[key], str) or not identity[key])
                for key in ("serverVersion", "serverCommit")
            ):
                raise ValueError(
                    "expectedIdentity serverVersion and serverCommit must be non-empty strings"
                )
        return parameters


@with_config(ConfigDict(extra="forbid"))
class ModelOverrides(TypedDict, total=False):
    """Only supplied keys exist: omission inherits, explicit null is not omission."""

    name: Annotated[str, Field(min_length=1)]
    parameters: dict[str, Any]


class ModelReference(StrictModel):
    file: str = Field(min_length=1)
    entry: Name
    overrides: ModelOverrides = Field(default_factory=lambda: ModelOverrides())

    @field_validator("file")
    @classmethod
    def local_file_only(cls, value: str) -> str:
        if (
            "://" in value
            or value.startswith("~")
            or "$" in value
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError(
                "model.file must be a local path; URLs and path expansion are unsupported"
            )
        return value


class ModelConfigurations(StrictModel):
    models: dict[Name, ModelConfiguration]


class ReasoningConfiguration(StrictModel):
    instructions: str | None = None
    output_schema: str | None = Field(default=None, alias="output-schema")
    timeout: Duration | None = None

    @field_validator("timeout")
    @classmethod
    def timeout_is_positive(cls, value: str | None) -> str | None:
        if value is not None and duration_seconds(value) <= 0:
            raise ValueError("reasoning timeout must be positive")
        return value


class LoopConfiguration(StrictModel):
    phases: list[Name] = Field(default_factory=list)


class LocalMemoryConfiguration(StrictModel):
    max_entries: int = Field(default=1000, alias="maxEntries", ge=1)


class ConversationMemoryConfiguration(StrictModel):
    max_messages: int = Field(default=50, alias="maxMessages", ge=1)
    summaries: bool = False


class LearnedMemoryConfiguration(StrictModel):
    scope: Literal["run", "agent"] = "run"
    mode: Literal["automatic", "agentic"] = "automatic"


class SharedMemoryConfiguration(StrictModel):
    scopes: tuple[Literal["deployment", "run"], ...] = Field(min_length=1)
    max_entries: int = Field(default=1000, alias="maxEntries", ge=1)

    @field_validator("scopes")
    @classmethod
    def scopes_are_unique(
        cls,
        scopes: tuple[Literal["deployment", "run"], ...],
    ) -> tuple[Literal["deployment", "run"], ...]:
        if len(set(scopes)) != len(scopes):
            raise ValueError("shared memory scopes must be unique")
        return scopes


class MemoryConfiguration(StrictModel):
    local: LocalMemoryConfiguration | None = None
    conversation: ConversationMemoryConfiguration | None = None
    learned: LearnedMemoryConfiguration | None = None
    shared: SharedMemoryConfiguration | None = None


class AgentExecutionSelection(StrictModel):
    kind: Literal["agno", "bounded-decision"]
    scenario: Name | None = None

    @model_validator(mode="after")
    def scenario_matches_kind(self) -> AgentExecutionSelection:
        if self.kind == "bounded-decision" and not self.scenario:
            raise ValueError("bounded-decision execution requires scenario")
        if self.kind == "agno" and self.scenario is not None:
            raise ValueError("Agno execution cannot select a decision scenario")
        return self


class DecisionChoice(StrictModel):
    id: Name
    description: str = Field(min_length=1)
    proposals: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def proposals_have_fixed_shape(self) -> DecisionChoice:
        for proposal in self.proposals:
            if set(proposal) != {"capability", "target", "arguments"}:
                raise ValueError(
                    "decision proposals require exactly capability, target, and arguments"
                )
            TypeAdapter(dict[str, Any]).validate_python(
                proposal["arguments"], strict=True
            )
            for field in ("capability", "target"):
                if not isinstance(proposal[field], str) or not re.fullmatch(
                    NAME_PATTERN, proposal[field]
                ):
                    raise ValueError(
                        f"decision proposal {field} must be a non-empty identifier"
                    )
        if self.id == "install_recovery" and not self.proposals:
            raise ValueError("install_recovery requires its fixed proposal bundle")
        return self


class DiagnosticFlowRole(StrictModel):
    """Static meaning of one required rule, never its observed presence."""

    purpose: str = Field(min_length=1)
    required: Literal[True]
    requirement: str = Field(min_length=1)

    @field_validator("required", mode="before")
    @classmethod
    def explicitly_required(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("diagnostic flow roles must explicitly be required")
        return value


class DiagnosticContext(StrictModel):
    """Opt-in, versioned static annotations for current-only diagnosis."""

    api_version: Literal["mininet-ai/bounded-decision-context/v1alpha2"] = Field(
        alias="apiVersion"
    )
    diagnostic_scope: Literal["required-local-configuration"] = Field(
        alias="diagnosticScope"
    )
    flow_roles: dict[
        Name,
        dict[Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]+$")], DiagnosticFlowRole],
    ] = Field(alias="flowRoles", min_length=1)


class DecisionScenario(VersionedDocument):
    api_version: Literal["mininet-ai/v1alpha5"] = Field(alias="apiVersion")
    kind: Literal["DecisionScenario"]
    metadata: Metadata
    input_mode: Literal["recovery-history", "current-only"] = Field(
        default="recovery-history", alias="inputMode"
    )
    instructions: str = Field(min_length=1)
    choices: tuple[DecisionChoice, ...] = Field(min_length=2, max_length=2)
    paths: dict[str, Any]
    observations: dict[str, Any]
    timing: dict[str, Any]
    policy: dict[str, Any]
    local_expectations: dict[Name, dict[str, Any]] = Field(
        default_factory=dict,
        alias="localExpectations",
        exclude_if=lambda value: not value,
    )
    diagnostic_context: DiagnosticContext | None = Field(
        default=None,
        alias="diagnosticContext",
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def bounded_scenario_contract(self) -> DecisionScenario:
        if self.input_mode == "current-only":
            for expected in self.local_expectations.values():
                if set(expected) != {"ports", "flows"} or (
                    not isinstance(expected["ports"], list)
                    or not expected["ports"]
                    or any(
                        not isinstance(port, str) or not port
                        for port in expected["ports"]
                    )
                    or len(set(expected["ports"])) != len(expected["ports"])
                    or not isinstance(expected["flows"], list)
                    or not expected["flows"]
                ):
                    raise ValueError(
                        "localExpectations requires non-empty ports and flows"
                    )
                for flow in expected["flows"]:
                    if not isinstance(flow, dict) or set(flow) != {
                        "cookie",
                        "table",
                        "priority",
                        "match",
                        "actions",
                    }:
                        raise ValueError(
                            "local expected flows require cookie, table, priority, match, actions"
                        )
                    if (
                        not isinstance(flow["cookie"], str)
                        or not re.fullmatch(r"0x[0-9a-fA-F]+", flow["cookie"])
                        or type(flow["table"]) is not int
                        or flow["table"] < 0
                        or type(flow["priority"]) is not int
                        or not 0 <= flow["priority"] <= 65535
                        or not isinstance(flow["match"], dict)
                        or any(
                            not isinstance(key, str)
                            or not isinstance(value, (str, bool))
                            for key, value in flow["match"].items()
                        )
                        or not isinstance(flow["actions"], str)
                        or not flow["actions"]
                    ):
                        raise ValueError(
                            "local expected flow fields have invalid types or ranges"
                        )
            if self.diagnostic_context is not None:
                roles = self.diagnostic_context.flow_roles
                if set(roles) != set(self.local_expectations):
                    raise ValueError(
                        "diagnosticContext flowRoles must cover exactly localExpectations targets"
                    )
                for target, expected in self.local_expectations.items():
                    cookies = [flow["cookie"] for flow in expected["flows"]]
                    if len(set(cookies)) != len(cookies) or set(roles[target]) != set(
                        cookies
                    ):
                        raise ValueError(
                            "diagnosticContext flowRoles must identify every unique expected flow cookie"
                        )
            if len({choice.id for choice in self.choices}) != 2:
                raise ValueError("current-only diagnosis choices must have unique IDs")
            if any(choice.proposals for choice in self.choices):
                raise ValueError(
                    "current-only diagnosis choices cannot contain proposals"
                )
            if self.paths or self.policy:
                raise ValueError(
                    "current-only diagnosis cannot declare network paths or policy"
                )
            if not self.observations or any(
                not isinstance(key, str)
                or not key
                or not isinstance(value, str)
                or not value
                for key, value in self.observations.items()
            ):
                raise ValueError(
                    "current-only diagnosis requires named current observation bindings"
                )
            self._validate_timing()
            return self
        if self.diagnostic_context is not None:
            raise ValueError(
                "diagnosticContext is only valid for current-only diagnosis"
            )
        if self.local_expectations:
            raise ValueError(
                "localExpectations is only valid for current-only diagnosis"
            )
        if tuple(choice.id for choice in self.choices) != (
            "no_action",
            "install_recovery",
        ):
            raise ValueError("decision choices must be no_action then install_recovery")
        if self.choices[0].proposals:
            raise ValueError("no_action must have no proposals")
        if set(self.paths) != {"primary", "recovery"}:
            raise ValueError("decision scenario requires primary and recovery paths")
        if any(
            not isinstance(path, list) or len(path) < 2 for path in self.paths.values()
        ):
            raise ValueError("decision scenario paths must be ordered node lists")
        required = {
            "pathProbes",
            "linkStates",
            "policyState",
            "actionOutcomes",
            "historyRounds",
            "maxAge",
        }
        if set(self.observations) != required:
            raise ValueError("decision scenario observations are incomplete")
        if any(
            not isinstance(self.observations[name], str) or not self.observations[name]
            for name in ("pathProbes", "linkStates", "policyState", "actionOutcomes")
        ):
            raise ValueError(
                "logical decision observation bindings must be non-empty source names"
            )
        if (
            self.observations["historyRounds"] != 3
            or self.observations["maxAge"] != "2s"
        ):
            raise ValueError(
                "decision scenario historyRounds and maxAge are fixed at 3 and 2s"
            )
        if set(self.policy) != {"initial", "recovery", "probePolicy"}:
            raise ValueError(
                "decision scenario requires initial, recovery, and probePolicy"
            )
        self._validate_timing()
        return self

    def _validate_timing(self) -> None:
        if (
            set(self.timing) != {"cadence", "decisionDeadline"}
            or self.timing.get("cadence") != "1s"
            or not isinstance(self.timing.get("decisionDeadline"), str)
            or duration_seconds(self.timing["decisionDeadline"]) <= 0
        ):
            raise ValueError(
                "decision scenario requires a 1s cadence and positive decisionDeadline"
            )


class AgentBlueprint(VersionedDocument):
    kind: Literal["AgentBlueprint"]
    metadata: Metadata
    implementation: Implementation = Field(default_factory=Implementation)
    model: ModelConfiguration | ModelReference | None = None
    reasoning: ReasoningConfiguration = Field(default_factory=ReasoningConfiguration)
    memory: MemoryConfiguration = Field(default_factory=MemoryConfiguration)
    loop: LoopConfiguration = Field(default_factory=LoopConfiguration)
    execution: AgentExecutionSelection | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def execution_requires_current_source(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "execution" not in value:
            return value
        api_version = value.get("apiVersion", value.get("api_version"))
        if api_version == "mininet-ai/v1alpha5":
            # v1alpha5: execution selection is always allowed
            return value
        # v1alpha3 / v1alpha4: new fields are not admitted under old document versions.
        # Omitted execution selection means Agno (historical behavior).
        # Explicit execution selection is rejected.
        raise ValueError(
            "execution selection requires mininet-ai/v1alpha5; "
            "historical versions omit the execution field to mean Agno"
        )

    @model_validator(mode="after")
    def bounded_execution_is_explicitly_supported(self) -> AgentBlueprint:
        if self.execution is not None and self.execution.kind == "bounded-decision":
            if self.api_version != "mininet-ai/v1alpha5":
                raise ValueError(
                    "bounded-decision execution requires mininet-ai/v1alpha5"
                )
            if self.implementation.type != "declarative":
                raise ValueError(
                    "bounded-decision execution requires declarative implementation"
                )
            if (
                isinstance(self.model, ModelConfiguration)
                and self.model.provider != "llamacpp-systemone"
            ):
                raise ValueError(
                    "bounded-decision execution requires llamacpp-systemone model configuration"
                )
            if self.model is None:
                raise ValueError(
                    "bounded-decision execution requires model configuration"
                )
            if any((self.memory.local, self.memory.conversation, self.memory.learned)):
                raise ValueError(
                    "bounded-decision does not support local, conversation, or learned memory"
                )
            if (
                self.reasoning.instructions is not None
                or self.reasoning.output_schema is not None
            ):
                raise ValueError(
                    "bounded-decision does not support chat reasoning instructions or output schemas"
                )
            if self.loop.phases:
                raise ValueError("bounded-decision does not support chat loop phases")
            if self.reasoning.timeout is not None:
                raise ValueError(
                    "bounded-decision timing is scenario-owned; reasoning.timeout is not applicable"
                )
        return self


class PostconditionDefinition(StrictModel):
    observation: str = Field(min_length=1)
    path: str = Field(min_length=1)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    expected: JsonValue
    timeout: Duration = "5s"
    interval: Duration = "250ms"

    @model_validator(mode="after")
    def timing_is_positive_and_bounded(self) -> PostconditionDefinition:
        timeout = duration_seconds(self.timeout)
        interval = duration_seconds(self.interval)
        if timeout <= 0 or interval <= 0:
            raise ValueError("postcondition timeout and interval must be positive")
        if interval > timeout:
            raise ValueError("postcondition interval cannot exceed its timeout")
        return self


class CapabilityRollbackConfiguration(StrictModel):
    timeout: Duration = "30s"

    @field_validator("timeout")
    @classmethod
    def timeout_is_positive(cls, value: str) -> str:
        if duration_seconds(value) <= 0:
            raise ValueError("rollback timeout must be positive")
        return value


class CapabilityDefinition(VersionedDocument):
    kind: Literal["Capability"]
    metadata: Metadata
    targets: list[ResourceKind] = Field(min_length=1)
    layers: list[str] = Field(min_length=1)
    effects: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="input-schema")
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="output-schema")
    reversible: bool = False
    provider: str | None = None
    postconditions: list[PostconditionDefinition] = Field(default_factory=list)
    rollback: CapabilityRollbackConfiguration | None = None

    @model_validator(mode="after")
    def rollback_matches_reversibility(self) -> CapabilityDefinition:
        if self.rollback is not None and not self.reversible:
            raise ValueError("rollback requires a reversible capability")
        return self


class AttachmentLayer(StrEnum):
    GLOBAL = "global"
    MANAGEMENT = "management"
    CONTROL = "control"
    DATA = "data"
    HOST = "host"
    OBSERVER = "observer"
    CUSTOM = "custom"


class Cardinality(StrEnum):
    SINGLETON = "singleton"
    PER_TARGET = "per-target"
    PER_GROUP = "per-group"


class Selector(StrictModel):
    kind: ResourceKind
    names: list[Name] = Field(default_factory=list)
    match_labels: dict[str, str] = Field(default_factory=dict, alias="matchLabels")


class Placement(StrictModel):
    layer: AttachmentLayer
    custom_layer: Name | None = Field(default=None, alias="custom-layer")
    targets: Selector
    cardinality: Cardinality = Cardinality.PER_TARGET
    runtime: Name
    group_by: str | None = Field(default=None, alias="groupBy")

    @model_validator(mode="after")
    def placement_is_complete(self) -> Placement:
        if self.layer == AttachmentLayer.CUSTOM and not self.custom_layer:
            raise ValueError("custom layer requires custom-layer")
        if self.layer != AttachmentLayer.CUSTOM and self.custom_layer:
            raise ValueError("custom-layer is only valid when layer is custom")
        if self.cardinality == Cardinality.PER_GROUP and not self.group_by:
            raise ValueError("per-group cardinality requires groupBy")
        if self.cardinality != Cardinality.PER_GROUP and self.group_by:
            raise ValueError("groupBy is only valid with per-group cardinality")
        return self


class ManualTrigger(StrictModel):
    type: Literal["manual"] = "manual"
    name: Name = "manual"


class IntervalTrigger(StrictModel):
    type: Literal["interval"] = "interval"
    name: Name
    every: Duration
    initial_delay: Duration = Field(default="0s", alias="initialDelay")

    @field_validator("every")
    @classmethod
    def interval_is_positive(cls, value: str) -> str:
        if duration_seconds(value) <= 0:
            raise ValueError("trigger interval must be positive")
        return value


class EventTrigger(StrictModel):
    type: Literal["event"] = "event"
    name: Name
    event: Name
    source: str | None = Field(default=None, min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    cooldown: Duration = "0s"


Trigger = Annotated[
    ManualTrigger | IntervalTrigger | EventTrigger,
    Field(discriminator="type"),
]


class ThresholdDetector(StrictModel):
    type: Literal["threshold"] = "threshold"
    name: Name
    event: Name
    path: str = Field(min_length=1)
    operator: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    value: float
    cooldown: Duration = "0s"


class AnomalyDetector(StrictModel):
    type: Literal["anomaly"] = "anomaly"
    name: Name
    event: Name
    path: str = Field(min_length=1)
    method: Literal["zscore"] = "zscore"
    sensitivity: float = Field(default=3, gt=0)
    min_samples: int = Field(default=10, alias="minSamples", ge=2)
    cooldown: Duration = "0s"


ObservationDetector = Annotated[
    ThresholdDetector | AnomalyDetector,
    Field(discriminator="type"),
]


class ObservationPolicy(StrictModel):
    observation: str = Field(min_length=1)
    every: Duration
    window: Duration
    aggregation: Literal["latest", "minimum", "maximum", "mean", "sum"] = "latest"
    detectors: list[ObservationDetector] = Field(default_factory=list)

    @model_validator(mode="after")
    def policy_is_consistent(self) -> ObservationPolicy:
        every = duration_seconds(self.every)
        window = duration_seconds(self.window)
        if every <= 0:
            raise ValueError("observation sampling interval must be positive")
        if window < every:
            raise ValueError(
                "observation window cannot be shorter than its sampling interval"
            )
        detector_names = [detector.name for detector in self.detectors]
        if len(set(detector_names)) != len(detector_names):
            raise ValueError("observation detector names must be unique")
        return self


class RestartConfiguration(StrictModel):
    policy: Literal["never", "on-failure"] = "never"
    max_attempts: int = Field(default=0, alias="maxAttempts", ge=0)
    backoff: Duration = "1s"

    @model_validator(mode="after")
    def attempts_match_policy(self) -> RestartConfiguration:
        if self.policy == "never" and self.max_attempts != 0:
            raise ValueError("never restart policy requires maxAttempts 0")
        if self.policy == "on-failure" and self.max_attempts < 1:
            raise ValueError("on-failure restart policy requires maxAttempts")
        return self


class ExecutionConfiguration(StrictModel):
    queue_capacity: int = Field(default=64, alias="queueCapacity", ge=1)
    max_concurrency: int = Field(default=1, alias="maxConcurrency", ge=1)
    overflow: Literal["reject", "drop-oldest", "coalesce"] = "reject"
    action_timeout: Duration = Field(default="30s", alias="actionTimeout")
    restart: RestartConfiguration = Field(default_factory=RestartConfiguration)

    @field_validator("action_timeout")
    @classmethod
    def action_timeout_is_positive(cls, value: str) -> str:
        if duration_seconds(value) <= 0:
            raise ValueError("action timeout must be positive")
        return value


class AgentDeployment(StrictModel):
    name: Name
    blueprint: Name | str
    placement: Placement
    observe: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    priority: int = 0
    triggers: list[Trigger] = Field(default_factory=lambda: [ManualTrigger()])
    observation_policies: list[ObservationPolicy] = Field(
        default_factory=list,
        alias="observationPolicies",
    )
    execution: ExecutionConfiguration = Field(default_factory=ExecutionConfiguration)
    decision_inputs: dict[str, str] = Field(
        default_factory=dict, alias="decisionInputs", exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def trigger_names_are_unique(self) -> AgentDeployment:
        names = [trigger.name for trigger in self.triggers]
        if len(set(names)) != len(names):
            raise ValueError("agent trigger names must be unique")
        return self


class CoordinationMode(StrEnum):
    INDEPENDENT = "independent"
    CENTRALIZED = "centralized"
    HIERARCHICAL = "hierarchical"
    DISTRIBUTED = "distributed"


class CoordinationRelationship(StrictModel):
    source: Name
    targets: list[Name] = Field(min_length=1)


class Coordination(StrictModel):
    mode: CoordinationMode = CoordinationMode.INDEPENDENT
    coordinator: Name | None = None
    peers: Literal["all", "topology-neighbors"] | None = None
    relationships: list[CoordinationRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def mode_has_required_configuration(self) -> Coordination:
        if self.mode == CoordinationMode.CENTRALIZED and not self.coordinator:
            raise ValueError("centralized coordination requires coordinator")
        if self.mode != CoordinationMode.CENTRALIZED and self.coordinator:
            raise ValueError("coordinator is only valid with centralized coordination")
        if self.mode == CoordinationMode.DISTRIBUTED and not self.peers:
            raise ValueError("distributed coordination requires peers")
        if self.mode != CoordinationMode.DISTRIBUTED and self.peers:
            raise ValueError("peers is only valid with distributed coordination")
        if self.mode == CoordinationMode.HIERARCHICAL and not self.relationships:
            raise ValueError("hierarchical coordination requires relationships")
        if self.mode != CoordinationMode.HIERARCHICAL and self.relationships:
            raise ValueError(
                "relationships are only valid with hierarchical coordination"
            )
        return self


class Policy(StrictModel):
    conflicting_actions: Literal["reject", "serialize", "priority"] = Field(
        default="reject", alias="conflicting-actions"
    )
    require_postcondition_check: bool = Field(
        default=False, alias="require-postcondition-check"
    )


class ResourceLimits(StrictModel):
    max_instances: int = Field(default=256, ge=1, alias="max-instances")
    max_concurrent_invocations: int = Field(
        default=32,
        ge=1,
        alias="max-concurrent-invocations",
    )
    max_queued_events: int = Field(
        default=4096,
        ge=1,
        alias="max-queued-events",
    )


class Experiment(VersionedDocument):
    kind: Literal["Experiment"]
    metadata: Metadata
    substrate: Substrate
    blueprints: list[AgentBlueprint | str] = Field(default_factory=list)
    decision_scenarios: list[DecisionScenario | str] = Field(
        default_factory=list,
        alias="decisionScenarios",
        exclude_if=lambda value: not value,
    )
    capability_definitions: list[CapabilityDefinition | str] = Field(
        default_factory=list, alias="capabilityDefinitions"
    )
    agents: list[AgentDeployment] = Field(default_factory=list)
    coordination: Coordination = Field(default_factory=Coordination)
    policies: Policy = Field(default_factory=Policy)
    resource_limits: ResourceLimits = Field(
        default_factory=ResourceLimits, alias="resourceLimits"
    )

    @model_validator(mode="before")
    @classmethod
    def new_fields_require_current_source(cls, value: Any) -> Any:
        agents = value.get("agents", ()) if isinstance(value, dict) else ()
        if not isinstance(agents, (list, tuple)):
            agents = ()
        if (
            isinstance(value, dict)
            and value.get("apiVersion", value.get("api_version"))
            != "mininet-ai/v1alpha5"
            and (
                "decisionScenarios" in value
                or "decision_scenarios" in value
                or any(
                    isinstance(agent, dict)
                    and ("decisionInputs" in agent or "decision_inputs" in agent)
                    for agent in agents
                )
            )
        ):
            raise ValueError(
                "decisionScenarios and decisionInputs require mininet-ai/v1alpha5"
            )
        return value

    @model_validator(mode="after")
    def versioned_bounded_authoring(self) -> Experiment:
        if self.api_version == "mininet-ai/v1alpha4":
            if self.decision_scenarios or any(
                agent.decision_inputs for agent in self.agents
            ):
                raise ValueError(
                    "decisionScenarios and decisionInputs require mininet-ai/v1alpha5"
                )
            if any(
                isinstance(blueprint, AgentBlueprint)
                and blueprint.execution is not None
                and blueprint.execution.kind == "bounded-decision"
                for blueprint in self.blueprints
            ):
                raise ValueError(
                    "bounded-decision execution requires mininet-ai/v1alpha5"
                )
        return self
