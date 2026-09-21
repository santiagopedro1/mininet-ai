"""Stable, serializable output of the experiment compiler."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from mininet_ai.specification.models import (
    AttachmentLayer,
    ControllerProtocol,
    ControllerType,
    CoordinationMode,
    Metadata,
    OpenFlowProtocol,
    Policy,
    ResourceKind,
    StrictModel,
    SwitchDatapath,
    SwitchFailMode,
)


class PlannedResourceBase(StrictModel):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    parent: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class PlannedNetwork(PlannedResourceBase):
    kind: Literal[ResourceKind.NETWORK]


class PlannedRegion(PlannedResourceBase):
    kind: Literal[ResourceKind.REGION]


class PlannedFlow(PlannedResourceBase):
    kind: Literal[ResourceKind.FLOW]


class PlannedController(PlannedResourceBase):
    kind: Literal[ResourceKind.CONTROLLER]
    controller_type: ControllerType = Field(alias="type")
    address: str | None = None
    protocol: ControllerProtocol
    port: int


class PlannedControllerDomain(PlannedResourceBase):
    kind: Literal[ResourceKind.CONTROLLER_DOMAIN]
    controllers: tuple[str, ...]


class PlannedSwitch(PlannedResourceBase):
    kind: Literal[ResourceKind.SWITCH]
    fail_mode: SwitchFailMode = Field(alias="failMode")
    datapath: SwitchDatapath
    controllers: tuple[str, ...]
    protocols: tuple[OpenFlowProtocol, ...]


class PlannedHost(PlannedResourceBase):
    kind: Literal[ResourceKind.HOST]
    default_route: str | None = Field(default=None, alias="defaultRoute")


class PlannedPort(PlannedResourceBase):
    kind: Literal[ResourceKind.PORT]
    role: Literal["host-interface", "switch-port"]
    number: int
    ipv4: str | None = None
    mac: str | None = None
    mtu: int


class PlannedLink(PlannedResourceBase):
    kind: Literal[ResourceKind.LINK]
    endpoints: tuple[str, str]
    bandwidth: float | None = None
    delay: str | None = None
    jitter: str | None = None
    loss: float = 0
    max_queue_size: int | None = Field(default=None, alias="maxQueueSize")


PlannedResource = Annotated[
    PlannedNetwork
    | PlannedRegion
    | PlannedController
    | PlannedControllerDomain
    | PlannedSwitch
    | PlannedHost
    | PlannedPort
    | PlannedLink
    | PlannedFlow,
    Field(discriminator="kind"),
]


class Attachment(StrictModel):
    layer: AttachmentLayer
    custom_layer: str | None = Field(default=None, alias="custom-layer")
    targets: tuple[str, ...]
    target_kind: ResourceKind = Field(alias="target-kind")
    runtime: str


class AgentInstance(StrictModel):
    id: str
    deployment: str
    blueprint: str
    attachment: Attachment
    observes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    privileges: tuple[str, ...] = ()
    priority: int = 0


class CoordinationEdge(StrictModel):
    source: str
    target: str
    relationship: Literal["coordinates", "parent", "peer"]


class CoordinationPlan(StrictModel):
    mode: CoordinationMode
    edges: tuple[CoordinationEdge, ...] = ()


class DeploymentPlan(StrictModel):
    api_version: Literal["mininet-ai/v1alpha1"] = Field(alias="apiVersion")
    kind: Literal["DeploymentPlan"] = "DeploymentPlan"
    metadata: Metadata
    source: str
    digest: str
    substrate: str
    resources: tuple[PlannedResource, ...]
    agents: tuple[AgentInstance, ...]
    coordination: CoordinationPlan
    policies: Policy
    snapshot: dict[str, Any]
