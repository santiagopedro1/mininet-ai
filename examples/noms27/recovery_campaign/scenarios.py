"""Frozen structural incidents. No treatment outcomes enter repairability checks."""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

type Family = Literal["diamond", "extended_diamond"]
type FaultKind = Literal["link", "transit_switch_isolation"]

CLASSES: dict[str, tuple[Family, FaultKind, str]] = {
    "diamond_link": ("diamond", "link", "local_repair_sufficient"),
    "diamond_switch": (
        "diamond",
        "transit_switch_isolation",
        "local_repair_sufficient",
    ),
    "extended_link": ("extended_diamond", "link", "broader_repair_required"),
    "extended_switch": (
        "extended_diamond",
        "transit_switch_isolation",
        "broader_repair_required",
    ),
    "tail_link": ("extended_diamond", "link", "no_surviving_path"),
    "tail_switch": (
        "extended_diamond",
        "transit_switch_isolation",
        "no_surviving_path",
    ),
}


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    placement_id: str
    topology_id: str
    class_id: str
    family: Family
    split: Literal["calibration", "heldout"]
    links: tuple[tuple[str, str], ...]
    initial_forwarding: dict[str, str]
    backup_forwarding: dict[str, str]
    fault_kind: FaultKind
    fault_target: tuple[str, ...]
    local_switch: Literal["s1"] = "s1"
    automatic_restoration: Literal[False] = False

    @property
    def ports(self) -> dict[str, dict[str, int]]:
        ports: dict[str, dict[str, int]] = {}
        for left, right in self.links:
            for node, neighbor in ((left, right), (right, left)):
                ports.setdefault(node, {})[neighbor] = len(ports.get(node, {})) + 1
        return {
            node: value for node, value in ports.items() if not node.startswith("h")
        }

    @property
    def disabled_links(self) -> tuple[tuple[str, str], ...]:
        if self.fault_kind == "link":
            return tuple(
                edge for edge in self.links if set(edge) == set(self.fault_target)
            )
        return tuple(edge for edge in self.links if self.fault_target[0] in edge)

    def path(self, forwarding: dict[str, str], *, faulted: bool = True) -> list[str]:
        edges = {frozenset(edge) for edge in self.links}
        if faulted:
            edges -= {frozenset(edge) for edge in self.disabled_links}
        path = ["h1", "s1"]
        while path[-1] != "h2":
            target = forwarding.get(path[-1])
            if (
                target is None
                or target in path
                or frozenset((path[-1], target)) not in edges
            ):
                return []
            path.append(target)
        return path

    def structural_check(self) -> dict:
        neighbors: dict[str, set[str]] = {}
        for left, right in self.links:
            if (left, right) not in self.disabled_links:
                neighbors.setdefault(left, set()).add(right)
                neighbors.setdefault(right, set()).add(left)
        reached, pending = {"h1"}, ["h1"]
        while pending:
            for node in neighbors.get(pending.pop(), set()) - reached:
                reached.add(node)
                pending.append(node)
        local = {**self.initial_forwarding, "s1": "s3"}
        broader = {**self.initial_forwarding, **self.backup_forwarding}
        local_path, broader_path = self.path(local), self.path(broader)
        regime = (
            "no_surviving_path"
            if "h2" not in reached
            else "local_repair_sufficient"
            if local_path
            else "broader_repair_required"
        )
        return {
            "basis": "topology_forwarding_declared_authority_only",
            "regime": regime,
            "healthy_path": self.path(self.initial_forwarding, faulted=False),
            "faulted_initial_path": self.path(self.initial_forwarding),
            "local_path": local_path,
            "broader_path": broader_path,
            "disabled_links": [list(edge) for edge in self.disabled_links],
            "local_authority": {"s1": "s3"},
            "broader_authority": "forwarding_only_on_surviving_ports",
            "live_feasibility": "pending",
        }

    @model_validator(mode="after")
    def check_structure(self):
        if self.class_id not in CLASSES:
            raise ValueError("unknown scenario class")
        family, kind, regime = CLASSES[self.class_id]
        if (self.family, self.fault_kind) != (family, kind):
            raise ValueError("class family/fault mismatch")
        if len({frozenset(edge) for edge in self.links}) != len(self.links) or any(
            left == right for left, right in self.links
        ):
            raise ValueError("duplicate or self link")
        if self.fault_kind == "transit_switch_isolation":
            if len(self.fault_target) != 1 or self.fault_target[0] in {
                "h1",
                "h2",
                "s1",
                "s4",
                "s9",
            }:
                raise ValueError(
                    "isolation requires a transit switch; endpoints survive"
                )
        elif len(self.fault_target) != 2:
            raise ValueError("link target requires two endpoints")
        elif any(node.startswith("h") for node in self.fault_target):
            raise ValueError("host attachment links survive")
        check = self.structural_check()
        if (
            not self.disabled_links
            or not check["healthy_path"]
            or check["faulted_initial_path"]
        ):
            raise ValueError("fault must disrupt healthy initial forwarding")
        if check["regime"] != regime or (
            regime != "no_surviving_path" and not check["broader_path"]
        ):
            raise ValueError("declared regime lacks structural witness")
        if self.ports["s1"] != {"h1": 1, "p0": 2, "s3": 3}:
            raise ValueError("frozen local catalogue requires s1 ports 1/2/3")
        return self


def make_scenario(class_id: str, placement: int) -> Scenario:
    family, kind, regime = CLASSES[class_id]
    primary = ["s1", *(f"p{i}" for i in range(placement + 1)), "s4"]
    backup = ["s1", "s3", *(["s5"] if family == "extended_diamond" else []), "s4"]
    tail = (
        ["s4", "t0", "t1", "t2", "s9", "h2"]
        if family == "extended_diamond"
        else ["s4", "h2"]
    )
    # These first edges freeze the catalogue's local port numbers.
    links = [("h1", "s1"), ("s1", "p0"), ("s1", "s3")]
    links += list(pairwise(primary[1:]))
    links += list(pairwise(backup[1:])) + list(pairwise(tail))
    initial = dict(pairwise(primary)) | dict(pairwise(tail))
    repairs = dict(pairwise(backup))
    if regime == "local_repair_sufficient":
        initial.update({k: v for k, v in repairs.items() if k != "s1"})
    target = ("s1", "p0") if kind == "link" else ("p0",)
    if regime == "no_surviving_path":
        target = (
            (f"t{placement}", tail[placement + 2])
            if kind == "link"
            else (f"t{placement}",)
        )
    return Scenario(
        placement_id=f"{class_id}-p{placement}",
        topology_id=f"{family}-instance-{placement}",
        class_id=class_id,
        family=family,
        split="calibration" if placement == 0 else "heldout",
        links=tuple(links),
        initial_forwarding=initial,
        backup_forwarding=repairs,
        fault_kind=kind,
        fault_target=target,
    )
