"""Link-failure measurements, separate from runtime postcondition timings."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from random import Random
from statistics import mean, median
from threading import Event, Lock
from time import monotonic, sleep
from typing import Any, Literal

from pydantic import Field

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.agents.agno import AgnoAgentFactory
from mininet_ai.compiler import DeploymentPlan, compile_experiment
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.sdk import AgentInvocationResult, InvocationStatus
from mininet_ai.specification.models import Experiment, StrictModel
from mininet_ai.substrates import (
    ActionRequest,
    ActionStatus,
    MininetOVSRuntime,
    ObservationQuery,
    ResourceOperationalState,
    SubstrateRuntime,
)


class AgentMeasurement(StrictModel):
    agent_id: str
    invocation_id: str | None = None
    status: InvocationStatus | Literal["not_invoked", "preparing", "error"] = (
        "not_invoked"
    )
    issue: str | None = None
    detected_at_seconds: float | None = Field(default=None, ge=0)
    action_started_at_seconds: float | None = Field(default=None, ge=0)
    detection_seconds: float | None = Field(default=None, ge=0)
    reasoning_seconds: float | None = Field(default=None, ge=0)
    action_execution_seconds: float | None = Field(default=None, ge=0)
    action_effect_seconds: float | None = Field(default=None, ge=0)


class TrialResult(StrictModel):
    trial_id: str
    seed: int
    failed_link: str
    status: Literal[
        "recovered", "timeout", "baseline_failed", "injection_failed", "error"
    ]
    recovery_seconds: float | None = Field(default=None, ge=0)
    elapsed_seconds: float = Field(ge=0)
    agents: tuple[AgentMeasurement, ...]
    issue: str | None = None


class TrialMeasurements:
    """Collect monotonic timestamps from one trial, safely across workers.

    Action effect ends at the *shared* recovery observation. It is not proof
    that any individual agent caused recovery. Unobserved phases remain null.
    """

    def __init__(
        self,
        *,
        trial_id: str,
        seed: int,
        failed_link: str,
        agent_ids: tuple[str, ...],
        started_at: float,
    ) -> None:
        if len(set(agent_ids)) != len(agent_ids):
            raise ValueError("agent IDs must be unique")
        self._trial_id = trial_id
        self._seed = seed
        self._failed_link = failed_link
        self._start = started_at
        self._agents = {name: AgentMeasurement(agent_id=name) for name in agent_ids}
        self._lock = Lock()

    def detected(self, agent_id: str, *, at: float) -> None:
        offset = self._offset(at)
        with self._lock:
            record = self._agents[agent_id]
            if record.detection_seconds is None:
                self._agents[agent_id] = record.model_copy(
                    update={
                        "detection_seconds": offset,
                        "detected_at_seconds": offset,
                    }
                )

    def invoked(
        self,
        agent_id: str,
        *,
        invocation_id: str,
        status: str,
        reasoning_seconds: float,
        action_execution_seconds: float,
        action_started_at: float | None,
        issue: str | None = None,
    ) -> None:
        offset = (
            self._offset(action_started_at) if action_started_at is not None else None
        )
        with self._lock:
            record = self._agents[agent_id]
            self._agents[agent_id] = AgentMeasurement(
                **(
                    record.model_dump()
                    | {
                        "invocation_id": invocation_id,
                        "status": status,
                        "issue": issue,
                        "reasoning_seconds": reasoning_seconds,
                        "action_execution_seconds": (
                            action_execution_seconds
                            if action_started_at is not None
                            else None
                        ),
                        "action_started_at_seconds": offset,
                    }
                )
            )

    def invocation_started(
        self, agent_id: str, *, invocation_id: str | None = None
    ) -> None:
        with self._lock:
            self._agents[agent_id] = self._agents[agent_id].model_copy(
                update={
                    "status": "preparing",
                    "invocation_id": invocation_id,
                }
            )

    def action_started(self, agent_id: str, *, at: float) -> None:
        offset = self._offset(at)
        with self._lock:
            self._agents[agent_id] = self._agents[agent_id].model_copy(
                update={
                    "action_started_at_seconds": offset,
                }
            )

    def failed(self, agent_id: str, *, issue: str) -> None:
        with self._lock:
            self._agents[agent_id] = self._agents[agent_id].model_copy(
                update={
                    "status": "error",
                    "issue": issue,
                }
            )

    def finish(
        self,
        *,
        recovered_at: float | None,
        completed_at: float,
        status: Literal["baseline_failed", "injection_failed", "error"] | None = None,
        issue: str | None = None,
    ) -> TrialResult:
        elapsed = self._offset(completed_at)
        recovery = self._offset(recovered_at) if recovered_at is not None else None
        if recovery is not None and (recovery > elapsed or status is not None):
            raise ValueError("recovery must precede completion of a successful trial")
        with self._lock:
            agents = tuple(
                record.model_copy(
                    update={
                        "action_effect_seconds": (
                            recovery - record.action_started_at_seconds
                            if recovery is not None
                            and record.action_started_at_seconds is not None
                            and record.action_started_at_seconds <= recovery
                            else None
                        ),
                    }
                )
                for record in self._agents.values()
            )
        return TrialResult(
            trial_id=self._trial_id,
            seed=self._seed,
            failed_link=self._failed_link,
            status=status or ("recovered" if recovery is not None else "timeout"),
            recovery_seconds=recovery,
            elapsed_seconds=elapsed,
            agents=agents,
            issue=issue,
        )

    def _offset(self, timestamp: float) -> float:
        offset = timestamp - self._start
        if offset < 0:
            raise ValueError("timestamp precedes failure onset")
        return offset


@dataclass(frozen=True)
class LinkFailureScenario:
    plan: DeploymentPlan
    seed: int
    ring: tuple[str, ...]
    failed_link: str
    ports: dict[tuple[str, str], int]

    def route(
        self, source: str, destination: str, *, failed: bool = False
    ) -> tuple[str, ...]:
        """Return the switch path between the two experiment hosts."""
        hosts = {"h1": self.ring[0], "h2": self.ring[1]}
        return self._path(hosts[source], hosts[destination], failed=failed)

    def rules(self, switch: str, *, failed: bool = False) -> tuple[dict[str, Any], ...]:
        """Explicit unicast and directed ARP rules; no learning or failover."""
        rules = []
        for host, attachment, ip, mac in (
            ("h1", self.ring[0], "10.0.0.1", "02:00:00:00:00:01"),
            ("h2", self.ring[1], "10.0.0.2", "02:00:00:00:00:02"),
        ):
            neighbor = (
                host
                if switch == attachment
                else self._path(switch, attachment, failed=failed)[1]
            )
            action = f"output:{self.ports[switch, neighbor]}"
            rules.extend(
                (
                    {"priority": 100, "match": {"dl_dst": mac}, "actions": action},
                    {
                        "priority": 100,
                        "match": {"arp": True, "arp_tpa": ip},
                        "actions": action,
                    },
                )
            )
        return tuple(rules)

    def _path(self, source: str, destination: str, *, failed: bool) -> tuple[str, ...]:
        queue: deque[tuple[str, ...]] = deque([(source,)])
        visited = {source}
        while queue:
            path = queue.popleft()
            node = path[-1]
            if node == destination:
                return path
            index = self.ring.index(node)
            for neighbor in (
                self.ring[(index + 1) % len(self.ring)],
                self.ring[(index - 1) % len(self.ring)],
            ):
                if failed and {node, neighbor} == set(self.ring[:2]):
                    continue
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((*path, neighbor))
        raise ValueError("no alternate route")


def build_scenario(
    *,
    seed: int = 0,
    model: str = "qwen3.5:latest",
    ollama_host: str = "http://10.10.10.152:11434",
) -> LinkFailureScenario:
    """Compile a seeded random ring with one Ollama-backed agent per switch."""
    switches = [f"s{i}" for i in range(1, 11)]
    Random(seed).shuffle(switches)
    resources: list[dict[str, Any]] = [{"name": "network", "kind": "network"}]
    nodes = {}
    ports: dict[tuple[str, str], int] = {}
    for switch in switches:
        node = {
            "name": switch,
            "kind": "switch",
            "parent": "network",
            "controllers": [],
            "failMode": "secure",
            "protocols": ["OpenFlow13"],
            "ports": [],
        }
        resources.append(node)
        nodes[switch] = node
    links = []

    def endpoint(node: str, neighbor: str) -> dict[str, str]:
        if node.startswith("h"):
            return {"node": node, "adapter": f"{node}-eth0"}
        number = len(nodes[node]["ports"]) + 1
        name = f"{node}-eth{number}"
        nodes[node]["ports"].append({"name": name, "number": number})
        ports[node, neighbor] = number
        return {"node": node, "adapter": name}

    def link(left: str, right: str) -> None:
        links.append(
            {
                "name": f"{left}-{right}",
                "endpoints": [endpoint(left, right), endpoint(right, left)],
                "bandwidth": 100,
                "delay": "1ms",
            }
        )

    for index, switch in enumerate(switches):
        link(switch, switches[(index + 1) % len(switches)])
    for index in range(2):
        host = f"h{index + 1}"
        resources.append(
            {
                "name": host,
                "kind": "host",
                "parent": "network",
                "interfaces": [
                    {
                        "name": f"{host}-eth0",
                        "ipv4": f"10.0.0.{index + 1}/24",
                        "mac": f"02:00:00:00:00:{index + 1:02x}",
                    }
                ],
            }
        )
        link(host, switches[index])
    header = {"apiVersion": "mininet-ai/v1alpha3"}
    experiment = Experiment.model_validate(
        {
            **header,
            "kind": "Experiment",
            "metadata": {"name": "link-failure-latency", "labels": {"seed": str(seed)}},
            "substrate": {
                "driver": "mininet-ovs",
                "topology": {"resources": resources, "links": links},
            },
            "blueprints": [
                {
                    **header,
                    "kind": "AgentBlueprint",
                    "metadata": {"name": "ollama-switch-router"},
                    "implementation": {
                        "type": "python",
                        "entrypoint": "mininet_ai.agents.agno.ollama_factory:create_deterministic_prompt_parsed_agent",
                    },
                    "model": {
                        "provider": "ollama",
                        "name": model,
                        "parameters": {"host": ollama_host, "think": False},
                    },
                    "reasoning": {
                        "timeout": "120s",
                        "instructions": (
                            "A topology monitor detected a link failure. Install the explicit recovery policy "
                            "in the intent on targets[0] only. Return JSON with a nonempty message and exactly "
                            "one proposal per supplied rule. Each proposal uses a unique id, capability "
                            "openflow.flow.install, target targets[0], and arguments equal to the rule. "
                            "Do not invent ports, use NORMAL, enable links, or change the policy. "
                            "Return delegations: [] and sharedStateUpdates: []."
                        ),
                    },
                }
            ],
            "capabilityDefinitions": [
                {
                    **header,
                    "kind": "Capability",
                    "metadata": {"name": "openflow.flow.install"},
                    "targets": ["switch"],
                    "layers": ["data"],
                    "effects": ["dataplane.write"],
                    "input-schema": {
                        "type": "object",
                        "required": ["priority", "match", "actions"],
                        "properties": {
                            "priority": {"type": "integer"},
                            "match": {"type": "object"},
                            "actions": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "output-schema": {"type": "object"},
                    "provider": "substrate.action",
                }
            ],
            "agents": [
                {
                    "name": "switch-router",
                    "blueprint": "ollama-switch-router",
                    "placement": {
                        "layer": "data",
                        "targets": {"kind": "switch"},
                        "cardinality": "per-target",
                        "runtime": "device-sidecar",
                    },
                    "observe": ["topology.neighbors", "openflow.flows"],
                    "capabilities": ["openflow.flow.install"],
                    "triggers": [{"type": "manual", "name": "failure-monitor"}],
                }
            ],
            "coordination": {"mode": "independent"},
            "policies": {
                "conflicting-actions": "reject",
                "require-postcondition-check": False,
            },
            "resourceLimits": {
                "max-instances": 10,
                "max-concurrent-invocations": 10,
                "max-queued-events": 32,
            },
        }
    )
    return LinkFailureScenario(
        plan=compile_experiment(experiment),
        seed=seed,
        ring=tuple(switches),
        failed_link=f"{switches[0]}-{switches[1]}",
        ports=ports,
    )


def run_trial(
    scenario: LinkFailureScenario,
    substrate: SubstrateRuntime,
    agents: OneShotAgentRuntime,
    *,
    run_id: str,
    trial_id: str,
    timeout_seconds: float = 180,
    poll_interval_seconds: float = 0.1,
    clock: Callable[[], float] = monotonic,
) -> TrialResult:
    """Reset baseline, inject one failure, and observe agent-driven recovery.

    Workers poll the global topology independently (not local switch telemetry).
    A bounded reasoning call is drained before returning, even on trial timeout;
    no worker is permitted to start a new action after the trial ends.
    """
    if timeout_seconds <= 0 or poll_interval_seconds <= 0:
        raise ValueError("timeout and polling interval must be positive")
    identities = tuple(agent.id for agent in scenario.plan.agents)

    def measurements(start: float) -> TrialMeasurements:
        return TrialMeasurements(
            trial_id=trial_id,
            seed=scenario.seed,
            failed_link=scenario.failed_link,
            agent_ids=identities,
            started_at=start,
        )

    def action(
        name: str, target: str, parameters: dict[str, Any] | None = None
    ) -> None:
        result = substrate.execute(
            run_id,
            ActionRequest(
                id=f"{trial_id}-{name}-{target}",
                name=name,
                target=target,
                parameters=parameters or {},
            ),
        )
        if result.status != ActionStatus.SUCCEEDED:
            raise RuntimeError(f"{name} on {target}: {result.issue}")

    def link_is_down() -> bool:
        return any(
            resource.name == scenario.failed_link
            and resource.state == ResourceOperationalState.DOWN
            for resource in substrate.inspect(run_id).resources
        )

    def reachable() -> bool:
        observed = substrate.observe(
            run_id,
            ObservationQuery(
                name="host.reachability",
                targets=("h1",),
                parameters={"destinations": ["h2"], "timeoutSeconds": 1},
            ),
        )
        probes = observed.values.get("h1", {}).get("probes", [])
        return len(probes) == 1 and probes[0].get("reachable") is True

    baseline_start = clock()
    try:
        action("link.enable", scenario.failed_link)
        for switch in scenario.ring:
            for rule in scenario.rules(switch):
                action("openflow.flow.install", switch, rule)
        if link_is_down() or not reachable():
            raise RuntimeError("healthy baseline connectivity was not established")
    except Exception as error:
        return measurements(baseline_start).finish(
            recovered_at=None,
            completed_at=clock(),
            status="baseline_failed",
            issue=str(error),
        )

    start = clock()  # Command submission: includes administrative fault application.
    measured = measurements(start)
    deadline = start + timeout_seconds
    try:
        action("link.disable", scenario.failed_link)
        if not link_is_down() or reachable():
            raise RuntimeError("failure did not leave the active path disconnected")
    except Exception as error:
        return measured.finish(
            recovered_at=None,
            completed_at=clock(),
            status="injection_failed",
            issue=str(error),
        )

    stopped = Event()
    errors: list[str] = []
    error_lock = Lock()

    def react(agent_id: str, switch: str) -> None:
        try:
            while not stopped.is_set() and clock() < deadline:
                if link_is_down():
                    measured.detected(agent_id, at=clock())
                    break
                sleep(poll_interval_seconds)
            else:
                return
            rules = scenario.rules(switch, failed=True)
            measured.invocation_started(agent_id)
            prepared = agents.prepare(
                run_id,
                agent_id,
                f"Trial {trial_id}: observed {scenario.failed_link} DOWN. "
                "Keep it down. Install this recovery policy: " + json.dumps(rules),
            )
            action_start = None
            if isinstance(prepared, AgentInvocationResult):
                result = prepared
            else:
                measured.invocation_started(
                    agent_id, invocation_id=prepared.context.invocation_id
                )
                expected = sorted(json.dumps(rule, sort_keys=True) for rule in rules)
                supplied = sorted(
                    json.dumps(proposal.arguments, sort_keys=True)
                    for proposal in prepared.response.proposals
                )
                admitted = expected == supplied and all(
                    proposal.target == switch
                    and proposal.capability == "openflow.flow.install"
                    for proposal in prepared.response.proposals
                )
                outcomes = []
                for proposal in prepared.response.proposals:
                    if not admitted or stopped.is_set() or clock() >= deadline:
                        outcomes.append(
                            agents.reject(
                                prepared,
                                proposal,
                                code="experiment.policy-rejected",
                                message="Recovery policy mismatched or trial measurement has ended",
                            )
                        )
                    else:
                        if action_start is None:
                            action_start = clock()
                            measured.action_started(agent_id, at=action_start)
                        try:
                            outcomes.append(agents.execute(prepared, proposal))
                        except Exception as error:
                            outcomes.append(
                                agents.reject(
                                    prepared,
                                    proposal,
                                    code="experiment.action-error",
                                    message=str(error),
                                ).model_copy(update={"status": ActionStatus.FAILED})
                            )
                result = agents.complete(prepared, tuple(outcomes))
            measured.invoked(
                agent_id,
                invocation_id=result.invocation_id,
                status=result.status.value,
                reasoning_seconds=result.timings.reasoning_seconds,
                action_execution_seconds=result.timings.action_execution_seconds,
                action_started_at=action_start,
                issue=str(result.issue) if result.issue is not None else None,
            )
        except Exception as error:
            measured.failed(agent_id, issue=str(error))
            with error_lock:
                errors.append(f"{agent_id}: {error}")

    recovered_at = None
    observation_error = None
    try:
        with ThreadPoolExecutor(
            max_workers=len(identities), thread_name_prefix="link-failure"
        ) as workers:
            for agent in scenario.plan.agents:
                workers.submit(react, agent.id, agent.attachment.targets[0])
            try:
                while clock() < deadline:
                    if reachable():
                        observed_at = clock()
                        if observed_at <= deadline and link_is_down():
                            recovered_at = observed_at
                        break
                    sleep(poll_interval_seconds)
            except Exception as error:
                observation_error = str(error)
            finally:
                stopped.set()
    finally:
        stopped.set()
    if observation_error is not None:
        return measured.finish(
            recovered_at=None,
            completed_at=clock(),
            status="error",
            issue=observation_error,
        )
    return measured.finish(
        recovered_at=recovered_at,
        completed_at=clock(),
        issue="; ".join(errors) if errors else None,
    )


def summarize_trials(trials: tuple[TrialResult, ...]) -> dict[str, Any]:
    """Summarize measured values only, with explicit missing-value counts."""

    def distribution(values: list[float | None]) -> dict[str, Any]:
        measured = sorted(value for value in values if value is not None)
        return {
            "count": len(measured),
            "missing": len(values) - len(measured),
            "mean": mean(measured) if measured else None,
            "median": median(measured) if measured else None,
            "p95": measured[ceil(len(measured) * 0.95) - 1] if measured else None,
        }

    return {
        "trials": len(trials),
        "statuses": dict(Counter(trial.status for trial in trials)),
        "recovery_seconds": distribution([trial.recovery_seconds for trial in trials]),
        **{
            name: distribution(
                [getattr(agent, name) for trial in trials for agent in trial.agents]
            )
            for name in (
                "detection_seconds",
                "reasoning_seconds",
                "action_execution_seconds",
                "action_effect_seconds",
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure agent-driven link-failure recovery (no model sweep)."
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--model", default="qwen3.5:latest")
    parser.add_argument("--ollama-host", default="http://10.10.10.152:11434")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--poll-interval", type=float, default=0.1)
    parser.add_argument(
        "--output", type=Path, default=Path("link-failure-results.jsonl")
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--spec",
        action="store_true",
        help="Emit a rootless, compilable experiment specification.",
    )
    mode.add_argument(
        "--plan",
        action="store_true",
        help="Emit the deployment plan without running Mininet or Ollama.",
    )
    arguments = parser.parse_args()
    if arguments.trials < 1 or arguments.timeout <= 0 or arguments.poll_interval <= 0:
        parser.error("trials, timeout, and poll interval must be positive")
    scenario = build_scenario(
        seed=arguments.seed, model=arguments.model, ollama_host=arguments.ollama_host
    )
    if arguments.spec:
        print(json.dumps(scenario.plan.snapshot, indent=2))
        return
    if arguments.plan:
        print(scenario.plan.model_dump_json(by_alias=True, indent=2))
        return
    if os.geteuid() != 0:
        parser.error(
            "live trials require root, Mininet, and a running Open vSwitch service"
        )
    results: list[TrialResult] = []
    # Exclusive creation protects earlier experiment results from accidental overwrite.
    with arguments.output.open("x", encoding="utf-8") as output:

        def write(record: dict[str, Any]) -> None:
            output.write(json.dumps(record, allow_nan=False) + "\n")
            output.flush()
            os.fsync(output.fileno())

        write(
            {
                "type": "manifest",
                "schema": "mininet-ai/link-failure-latency/v1",
                "seed": scenario.seed,
                "failed_link": scenario.failed_link,
                "model": arguments.model,
                "ollama_host": arguments.ollama_host,
                "timeout_seconds": arguments.timeout,
                "poll_interval_seconds": arguments.poll_interval,
                "requested_trials": arguments.trials,
                "plan_digest": scenario.plan.digest,
                "experiment": scenario.plan.snapshot,
            }
        )
        try:
            for index in range(arguments.trials):
                substrate = None
                factory = None
                run = None
                trial_id = f"trial-{index + 1}"
                trial_start = monotonic()
                try:
                    try:
                        substrate = MininetOVSRuntime()
                        factory = AgnoAgentFactory()
                        run = substrate.deploy(scenario.plan)
                        registries = ProviderRegistries()
                        register_builtin_providers(registries, substrate)
                        agents = OneShotAgentRuntime(
                            scenario.plan, substrate, registries, agent_factory=factory
                        )
                        result = run_trial(
                            scenario,
                            substrate,
                            agents,
                            run_id=run.id,
                            trial_id=trial_id,
                            timeout_seconds=arguments.timeout,
                            poll_interval_seconds=arguments.poll_interval,
                        )
                    except Exception as error:
                        result = TrialMeasurements(
                            trial_id=trial_id,
                            seed=scenario.seed,
                            failed_link=scenario.failed_link,
                            agent_ids=tuple(agent.id for agent in scenario.plan.agents),
                            started_at=trial_start,
                        ).finish(
                            recovered_at=None,
                            completed_at=monotonic(),
                            status="error",
                            issue=str(error),
                        )
                    results.append(result)
                    write(
                        {
                            "type": "trial",
                            "run_id": run.id if run is not None else None,
                            **result.model_dump(mode="json"),
                        }
                    )
                finally:
                    try:
                        try:
                            if factory is not None:
                                factory.close()
                        finally:
                            if run is not None and substrate is not None:
                                substrate.teardown(run.id)
                    except Exception as error:
                        # Do not deploy another topology after unverified cleanup.
                        write(
                            {
                                "type": "cleanup_error",
                                "trial_id": trial_id,
                                "issue": str(error),
                            }
                        )
                        raise
        finally:
            summary = summarize_trials(tuple(results))
            write({"type": "summary", **summary})
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
