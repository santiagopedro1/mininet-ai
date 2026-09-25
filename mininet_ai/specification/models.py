"""The ``mininet-ai/v1alpha1`` public schema.

The models intentionally describe logical placement. Process isolation and real
network attachment are substrate/runtime concerns introduced in later phases.
"""

from __future__ import annotations

import re
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from mininet_ai.durations import duration_seconds

API_VERSION: Literal["mininet-ai/v1alpha1"] = "mininet-ai/v1alpha1"
NAME_PATTERN = r"^[a-zA-Z][a-zA-Z0-9_.-]*$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


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
    fail_mode: SwitchFailMode = Field(default=SwitchFailMode.SECURE, alias="failMode")
    datapath: SwitchDatapath = SwitchDatapath.KERNEL
    controllers: list[Name] = Field(default_factory=list)
    protocols: list[OpenFlowProtocol] = Field(default_factory=list)
    ports: list[SwitchPort] = Field(default_factory=list)


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


class ModelConfiguration(StrictModel):
    provider: Name
    name: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


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


class AgentBlueprint(StrictModel):
    api_version: Literal["mininet-ai/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["AgentBlueprint"]
    metadata: Metadata
    implementation: Implementation = Field(default_factory=Implementation)
    model: ModelConfiguration | None = None
    reasoning: ReasoningConfiguration = Field(default_factory=ReasoningConfiguration)
    memory: MemoryConfiguration = Field(default_factory=MemoryConfiguration)
    loop: LoopConfiguration = Field(default_factory=LoopConfiguration)


class PostconditionDefinition(StrictModel):
    observation: str = Field(min_length=1)
    path: str = Field(min_length=1)
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    expected: JsonValue
    timeout: Duration = "5s"
    interval: Duration = "250ms"


class CapabilityRollbackConfiguration(StrictModel):
    timeout: Duration = "30s"


class CapabilityDefinition(StrictModel):
    api_version: Literal["mininet-ai/v1alpha1"] = Field(alias="apiVersion")
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
    aggregation: Literal["latest", "minimum", "maximum", "mean", "sum"] = (
        "latest"
    )
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
    triggers: list[Trigger] = Field(
        default_factory=lambda: [ManualTrigger()]
    )
    observation_policies: list[ObservationPolicy] = Field(
        default_factory=list,
        alias="observationPolicies",
    )
    execution: ExecutionConfiguration = Field(
        default_factory=ExecutionConfiguration
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


class Experiment(StrictModel):
    api_version: Literal["mininet-ai/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["Experiment"]
    metadata: Metadata
    substrate: Substrate
    blueprints: list[AgentBlueprint | str] = Field(default_factory=list)
    capability_definitions: list[CapabilityDefinition | str] = Field(
        default_factory=list, alias="capabilityDefinitions"
    )
    agents: list[AgentDeployment] = Field(default_factory=list)
    coordination: Coordination = Field(default_factory=Coordination)
    policies: Policy = Field(default_factory=Policy)
    resource_limits: ResourceLimits = Field(
        default_factory=ResourceLimits, alias="resourceLimits"
    )
