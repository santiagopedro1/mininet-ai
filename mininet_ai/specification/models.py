"""The ``mininet-ai/v1alpha1`` public schema.

The models intentionally describe logical placement. Process isolation and real
network attachment are substrate/runtime concerns introduced in later phases.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


API_VERSION = "mininet-ai/v1alpha1"
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
    CONTROLLER_DOMAIN = "controller-domain"
    SWITCH = "switch"
    HOST = "host"
    PORT = "port"
    LINK = "link"
    FLOW = "flow"


class Resource(StrictModel):
    name: Name
    kind: ResourceKind
    labels: dict[str, str] = Field(default_factory=dict)
    parent: Name | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class Link(StrictModel):
    name: Name
    endpoints: tuple[Name, Name]
    labels: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def endpoints_differ(self) -> Link:
        if self.endpoints[0] == self.endpoints[1]:
            raise ValueError("link endpoints must be different")
        return self


class Topology(StrictModel):
    resources: list[Resource] = Field(min_length=1)
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
    timeout: str | None = None


class LoopConfiguration(StrictModel):
    phases: list[Name] = Field(default_factory=list)


class AgentBlueprint(StrictModel):
    api_version: Literal[API_VERSION] = Field(alias="apiVersion")
    kind: Literal["AgentBlueprint"]
    metadata: Metadata
    implementation: Implementation = Field(default_factory=Implementation)
    model: ModelConfiguration | None = None
    reasoning: ReasoningConfiguration = Field(default_factory=ReasoningConfiguration)
    memory: dict[str, Any] = Field(default_factory=dict)
    loop: LoopConfiguration = Field(default_factory=LoopConfiguration)


class CapabilityDefinition(StrictModel):
    api_version: Literal[API_VERSION] = Field(alias="apiVersion")
    kind: Literal["Capability"]
    metadata: Metadata
    targets: list[ResourceKind] = Field(min_length=1)
    layers: list[str] = Field(min_length=1)
    effects: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="input-schema")
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="output-schema")
    reversible: bool = False
    provider: str | None = None


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


class AgentDeployment(StrictModel):
    name: Name
    blueprint: Name | str
    placement: Placement
    observe: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    priority: int = 0


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


class Experiment(StrictModel):
    api_version: Literal[API_VERSION] = Field(alias="apiVersion")
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
