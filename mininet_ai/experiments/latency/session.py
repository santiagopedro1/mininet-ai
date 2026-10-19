"""The bounded trial's adapter to existing agent and Substrate public contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import copy_context
from threading import Thread
from typing import Any
from uuid import uuid4

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.agents.agno import AgnoAgentFactory
from mininet_ai.errors import describe_failure
from mininet_ai.experiments.latency.trials import AdmissionGate
from mininet_ai.outcomes import CleanupOutcome
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime.instrumentation import (
    Operation,
    Phase,
    current_binding,
    recording_scope,
)
from mininet_ai.sdk import AgentInvocationResult
from mininet_ai.substrates import (
    ActionRequest,
    ActionStatus,
    ObservationQuery,
    SubstrateRuntime,
)


class SubstrateTrialSession:
    """Own one deployment/factory; never redeploy or reuse a prior agent session.

    Construction is inert. Deployment and healthy-policy installation run within
    the coordinator's setup budget. Failed deployment uses its public rollback
    evidence, not an alternate privileged cleanup mechanism.
    """

    def __init__(
        self,
        scenario: Any,
        trial_id: str,
        substrate: SubstrateRuntime,
        *,
        factory: AgnoAgentFactory | None = None,
        agents_factory: Callable[..., Any] = OneShotAgentRuntime,
    ):
        self.scenario, self.trial_id, self.substrate = scenario, trial_id, substrate
        self.factory = factory or AgnoAgentFactory()
        self.agents_factory = agents_factory
        self.run_id: str | None = None
        self.agents: Any = None
        self.deployment_error: Exception | None = None
        self.agent_results: list[Any] = []
        self.agent_errors: list[tuple[str, str]] = []
        self.endpoints = next(
            r.endpoints
            for r in scenario.plan.resources
            if r.name == scenario.failed_link
        )

    def setup(self) -> None:
        try:
            self.run_id = self.substrate.deploy(self.scenario.plan).id
        except Exception as error:
            self.deployment_error = error
            raise
        registries = ProviderRegistries()
        register_builtin_providers(registries, self.substrate)
        self.agents = self.agents_factory(
            self.scenario.plan, self.substrate, registries, agent_factory=self.factory
        )
        self._action("link.enable", self.scenario.failed_link)
        for switch in self.scenario.switches:
            for rule in self.scenario.rules(switch):
                self._action("openflow.flow.install", switch, rule)

    def _action(self, name: str, target: str, parameters=None) -> None:
        assert self.run_id is not None
        result = self.substrate.execute(
            self.run_id,
            ActionRequest(
                id=str(uuid4()),
                name=name,
                target=target,
                parameters=parameters or {},
            ),
        )
        if result.status != ActionStatus.SUCCEEDED:
            raise RuntimeError(f"{name}: {result.issue}")

    def ready(self) -> bool:
        # Explicit collection acknowledgement, not merely creating a worker.
        return self.endpoints_down() == (False, False)

    def inject(self) -> None:
        self._action("link.disable", self.scenario.failed_link)

    def endpoints_down(self) -> tuple[bool, bool]:
        assert self.run_id is not None
        observation = self.substrate.observe(
            self.run_id,
            ObservationQuery(
                name="topology.resources",
                targets=tuple(self.endpoints),
            ),
        )
        states = []
        for endpoint in self.endpoints:
            resource = observation.values.get(endpoint, {})
            state = resource.get("state")
            # Missing interface telemetry must not masquerade as admin-down.
            if state not in ("up", "down") or "operState" not in resource.get(
                "attributes", {}
            ):
                raise RuntimeError(f"unreliable endpoint observer: {endpoint}")
            states.append(state == "down")
        return states[0], states[1]

    def probe(self) -> tuple[bool, bool]:
        assert self.run_id is not None
        results = []
        for source, destination in (("h1", "h2"), ("h2", "h1")):
            observed = self.substrate.observe(
                self.run_id,
                ObservationQuery(
                    name="host.reachability",
                    targets=(source,),
                    parameters={"destinations": [destination], "timeoutSeconds": 1},
                ),
            )
            probes = observed.values.get(source, {}).get("probes", [])
            if (
                len(probes) != 1
                or probes[0].get("source") != source
                or probes[0].get("destination") != destination
                or type(probes[0].get("reachable")) is not bool
            ):
                raise RuntimeError("incomplete designated probe evidence")
            results.append(probes[0]["reachable"])
        return results[0], results[1]

    def respond(self, gate: AdmissionGate, observation_id: str) -> None:
        workers = []
        for agent in self.scenario.plan.agents:
            context = copy_context()
            worker = Thread(
                target=context.run,
                args=(
                    self._respond,
                    agent.id,
                    agent.attachment.targets[0],
                    gate,
                    observation_id,
                ),
                daemon=True,
            )
            worker.start()
            workers.append(worker)
        # This outer worker is itself bounded by the coordinator cleanup budget.
        for worker in workers:
            worker.join()

    def _respond(
        self, agent_id: str, switch: str, gate: AdmissionGate, observation_id: str
    ):
        assert self.agents is not None and self.run_id is not None
        try:
            with recording_scope(
                current_binding(),
                agent_id=agent_id,
                observation_id=observation_id,
                observation_source="global_harness",
            ):
                with Operation(Phase.ADMISSION) as admission:
                    accepted = gate.invocation(agent_id)
                    admission.finish(
                        "invocation_admitted" if accepted else "invocation_closed",
                        failed=not accepted,
                    )
                if not accepted:
                    return
                rules = self.scenario.rules(switch, failed=True)
                prepared = self.agents.prepare(
                    self.run_id,
                    agent_id,
                    f"Observed {self.scenario.failed_link} DOWN. Keep it down. Install exactly this supplied recovery policy: "
                    + json.dumps(rules),
                )
                if isinstance(prepared, AgentInvocationResult):
                    self.agent_results.append(prepared)
                    return
                proposals = prepared.response.proposals
                expected = sorted(json.dumps(rule, sort_keys=True) for rule in rules)
                supplied = sorted(
                    json.dumps(p.arguments, sort_keys=True) for p in proposals
                )
                exact = (
                    expected == supplied
                    and len({p.id for p in proposals}) == len(proposals)
                    and all(
                        p.target == switch and p.capability == "openflow.flow.install"
                        for p in proposals
                    )
                )
                with (
                    recording_scope(
                        current_binding(), invocation_id=prepared.context.invocation_id
                    ),
                    Operation(Phase.ADMISSION) as policy,
                ):
                    accepted = exact and gate.policy(agent_id)
                    policy.finish(
                        "policy_accepted" if accepted else "policy_rejected",
                        failed=not accepted,
                    )
                outcomes = []
                for proposal in proposals:
                    if not accepted:
                        outcomes.append(
                            self.agents.reject(
                                prepared,
                                proposal,
                                code="experiment.policy-rejected",
                                message="Full supplied policy required",
                            )
                        )
                        continue
                    with (
                        recording_scope(
                            current_binding(),
                            invocation_id=prepared.context.invocation_id,
                            proposal_id=proposal.id,
                        ),
                        Operation(Phase.ACTION_ATTEMPT),
                        Operation(Phase.ADMISSION) as admission,
                    ):
                        admitted = gate.action(agent_id, proposal.id)
                        admission.finish(
                            "action_admitted" if admitted else "action_closed",
                            failed=not admitted,
                        )
                    if admitted:
                        try:
                            outcomes.append(self.agents.execute(prepared, proposal))
                        except Exception as error:
                            self.agent_errors.append((agent_id, str(error)))
                            outcomes.append(
                                self.agents.reject(
                                    prepared,
                                    proposal,
                                    code="experiment.action-error",
                                    message=str(error),
                                ).model_copy(update={"status": ActionStatus.FAILED})
                            )
                    else:
                        outcomes.append(
                            self.agents.reject(
                                prepared,
                                proposal,
                                code="experiment.closed",
                                message="Trial admission closed",
                            )
                        )
                self.agent_results.append(
                    self.agents.complete(prepared, tuple(outcomes))
                )
        except Exception as error:
            self.agent_errors.append((agent_id, str(error)))

    def cleanup(self) -> CleanupOutcome:
        try:
            if self.run_id is not None:
                return self.substrate.teardown(self.run_id).cleanup
            return (
                describe_failure(self.deployment_error).cleanup
                if self.deployment_error
                else CleanupOutcome()
            )
        finally:
            self.factory.close()
