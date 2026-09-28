"""Validate compiled coordination edges and authorize directed delivery."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import NoReturn

from mininet_ai.compiler import DeploymentPlan
from mininet_ai.compiler.models import CoordinationEdge
from mininet_ai.errors import MininetAIError
from mininet_ai.specification.models import CoordinationMode


class CoordinationGraphError(MininetAIError):
    """A compiled coordination graph cannot be executed safely."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        source: str | None = None,
        target: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.source = source
        self.target = target


class CoordinationGraph:
    """Executable, directed view of one deployment plan's coordination graph."""

    def __init__(self, plan: DeploymentPlan) -> None:
        agent_ids = tuple(agent.id for agent in plan.agents)
        if len(set(agent_ids)) != len(agent_ids):
            self._invalid("deployment plan contains duplicate agent instance IDs")

        self._mode = plan.coordination.mode
        self._agents = frozenset(agent_ids)
        self._edges: dict[tuple[str, str], CoordinationEdge] = {}
        destinations: dict[str, list[str]] = defaultdict(list)

        expected_relationship = {
            CoordinationMode.CENTRALIZED: "coordinates",
            CoordinationMode.HIERARCHICAL: "parent",
            CoordinationMode.DISTRIBUTED: "peer",
        }.get(self._mode)

        for edge in plan.coordination.edges:
            self._validate_edge(edge, expected_relationship)
            key = (edge.source, edge.target)
            if key in self._edges:
                self._invalid(
                    f"coordination graph contains duplicate edge "
                    f"{edge.source!r} -> {edge.target!r}"
                )
            self._edges[key] = edge
            destinations[edge.source].append(edge.target)

        self._destinations = {
            source: tuple(sorted(targets))
            for source, targets in destinations.items()
        }
        self._coordinator: str | None = None
        self._validate_shape()

    @property
    def mode(self) -> CoordinationMode:
        return self._mode

    def entry_agent(self, requested_agent: str) -> str:
        """Resolve the first recipient for an externally submitted intent."""

        self._require_agent(requested_agent, role="requested")
        if self._mode == CoordinationMode.CENTRALIZED:
            if self._coordinator is None:
                raise AssertionError("validated centralized graph has no coordinator")
            return self._coordinator
        return requested_agent

    def destinations(self, source: str) -> tuple[str, ...]:
        """Return the sorted agents to which source may initiate delivery."""

        self._require_agent(source, role="source")
        return self._destinations.get(source, ())

    def authorize(self, source: str, target: str) -> CoordinationEdge:
        """Return the compiled edge authorizing one directed delivery."""

        self._require_agent(source, role="source")
        self._require_agent(target, role="target")
        try:
            return self._edges[(source, target)]
        except KeyError as error:
            raise CoordinationGraphError(
                f"coordination delivery from {source!r} to {target!r} is not allowed",
                code="coordination.graph.delivery-denied",
                source=source,
                target=target,
            ) from error

    def _validate_edge(
        self,
        edge: CoordinationEdge,
        expected_relationship: str | None,
    ) -> None:
        if expected_relationship is None:
            self._invalid("independent coordination cannot contain edges")
        if edge.relationship != expected_relationship:
            self._invalid(
                f"{self._mode.value} coordination requires "
                f"{expected_relationship!r} edges, found {edge.relationship!r}"
            )
        if edge.source not in self._agents:
            self._invalid(
                f"coordination edge references unknown source agent {edge.source!r}"
            )
        if edge.target not in self._agents:
            self._invalid(
                f"coordination edge references unknown target agent {edge.target!r}"
            )
        if edge.source == edge.target:
            self._invalid(
                f"coordination graph contains self edge for {edge.source!r}"
            )

    def _validate_shape(self) -> None:
        if self._mode == CoordinationMode.INDEPENDENT:
            return
        if self._mode == CoordinationMode.CENTRALIZED:
            self._validate_centralized()
            return
        if self._mode == CoordinationMode.HIERARCHICAL:
            self._validate_hierarchy()
            return
        if self._mode == CoordinationMode.DISTRIBUTED:
            self._validate_peers()
            return
        self._invalid(f"unsupported coordination mode {self._mode!r}")

    def _validate_centralized(self) -> None:
        if not self._agents:
            self._invalid("centralized coordination requires an agent")
        if len(self._agents) == 1:
            if self._edges:
                self._invalid("a singleton centralized graph cannot contain edges")
            self._coordinator = next(iter(self._agents))
            return

        sources = {source for source, _ in self._edges}
        if len(sources) != 1:
            self._invalid(
                "centralized coordination requires exactly one coordinator source"
            )
        coordinator = next(iter(sources))
        expected = {
            (coordinator, target)
            for target in self._agents
            if target != coordinator
        }
        if set(self._edges) != expected:
            self._invalid(
                "centralized coordinator must have one outgoing edge to every "
                "other agent"
            )
        self._coordinator = coordinator

    def _validate_hierarchy(self) -> None:
        incoming = {agent: 0 for agent in self._agents}
        for _, target in self._edges:
            incoming[target] += 1
        ready = deque(sorted(agent for agent, count in incoming.items() if count == 0))
        visited = 0
        while ready:
            source = ready.popleft()
            visited += 1
            for target in self._destinations.get(source, ()):
                incoming[target] -= 1
                if incoming[target] == 0:
                    ready.append(target)
        if visited != len(self._agents):
            self._invalid("hierarchical coordination graph contains a cycle")

    def _validate_peers(self) -> None:
        for source, target in self._edges:
            if (target, source) not in self._edges:
                self._invalid(
                    f"peer edge {source!r} -> {target!r} has no reverse edge"
                )

    def _require_agent(self, agent_id: str, *, role: str) -> None:
        if agent_id not in self._agents:
            raise CoordinationGraphError(
                f"unknown {role} agent {agent_id!r}",
                code="coordination.graph.agent-unknown",
                **{role if role in {"source", "target"} else "target": agent_id},
            )

    @staticmethod
    def _invalid(message: str) -> NoReturn:
        raise CoordinationGraphError(
            message,
            code="coordination.graph.invalid-plan",
        )
