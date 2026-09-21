"""Stable, serializable output of the experiment compiler."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from mininet_ai.specification.models import (
    AttachmentLayer,
    CoordinationMode,
    Metadata,
    Policy,
    ResourceKind,
    StrictModel,
)


class PlannedResource(StrictModel):
    name: str
    kind: ResourceKind
    labels: dict[str, str] = Field(default_factory=dict)
    parent: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    endpoints: tuple[str, str] | None = None


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
