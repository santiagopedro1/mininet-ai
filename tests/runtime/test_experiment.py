from __future__ import annotations

import asyncio
import copy
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from unittest.mock import patch

from agno.agent import Agent
from agno.run.agent import RunOutput

from mininet_ai.agents import register_builtin_providers
from mininet_ai.agents.agno import AgnoAgentFactory
from mininet_ai.audit import AuditEventType, AuditRecorder, MemoryAuditSink
from mininet_ai.capabilities import SubstrateActionProvider
from mininet_ai.compiler.models import CoordinationEdge, CoordinationPlan
from mininet_ai.experiment import (
    ExperimentRuntime,
    ExperimentRuntimeState,
)
from mininet_ai.plugins import ProviderKind, ProviderPlugin, ProviderRegistries
from mininet_ai.runtime import (
    ContinuousRuntimeError,
    ContinuousRuntimeReport,
    InMemorySharedStateStore,
    LedgerAuditSink,
    LedgerError,
    LedgerRecordCategory,
    RunManifest,
    SharedStateAccess,
    SQLiteRunLedger,
    TelemetryPipelineReport,
)
from mininet_ai.sdk import (
    ActionProposal,
    AgentContext,
    AgentResponse,
    DelegationProposal,
    SharedStateRead,
    SharedStateUpdate,
)
from mininet_ai.specification.models import CoordinationMode
from mininet_ai.substrates import (
    ActionStatus,
    FakeSubstrateRuntime,
    RunState,
    RuntimeIssue,
)
from tests.agents.test_runtime import configured_plan, configured_shared_plan

NOW = datetime(2026, 1, 2, tzinfo=UTC)


class ResponseAgent(Agent):
    def __init__(self, respond):
        super().__init__()
        self.respond = respond

    async def arun(self, input=None, **options):
        return await asyncio.to_thread(self.run, input, **options)

    def run(self, input=None, **options):
        assert isinstance(input, AgentContext)
        return RunOutput(
            run_id=options["run_id"],
            session_id=options["session_id"],
            user_id=options["user_id"],
            content=self.respond(input),
        )


class ResponseFactory(AgnoAgentFactory):
    def __init__(self, respond):
        super().__init__()
        self.respond = respond

    def _declarative(self, definition):
        return ResponseAgent(self.respond)


class ExperimentRuntimeTests(unittest.TestCase):
    def test_centralized_requests_supervise_the_actual_entry_agent(self) -> None:
        completed = Event()
        results = []

        def on_result(record):
            results.append(record.result.agent_id)
            if len(results) == 2:
                completed.set()

        plan = configured_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "resource_limits": plan.resource_limits.model_copy(
                    update={"max_concurrent_invocations": 1}
                ),
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.CENTRALIZED,
                    edges=tuple(
                        CoordinationEdge(
                            source="global-router",
                            target=a.id,
                            relationship="coordinates",
                        )
                        for a in plan.agents
                        if a.id != "global-router"
                    ),
                ),
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            agent_factory=ResponseFactory(lambda c: AgentResponse(message=c.intent)),
            invocation_listener=on_result,
        )
        owner.start()
        try:
            owner.pause_agent("switch-router@s1")
            owner.submit_intent("switch-router@s1", "first")
            owner.submit_intent("switch-router@s2", "second")
            self.assertTrue(
                completed.wait(2),
                "paused requested agent blocked its unpaused coordinator",
            )
        finally:
            report = owner.stop()
        self.assertEqual(results, ["global-router", "global-router"])
        self.assertEqual(report.continuous.failed, 0)

    def test_pausing_a_capacity_waiter_does_not_strand_the_global_budget(self) -> None:
        running, release, unrelated = Event(), Event(), Event()

        def respond(context):
            if context.agent_id == "switch-router@s1":
                running.set()
                if not release.wait(3):
                    raise TimeoutError("not released")
            if context.agent_id == "global-router":
                unrelated.set()
            return AgentResponse(message=context.intent)

        plan = configured_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "resource_limits": plan.resource_limits.model_copy(
                    update={"max_concurrent_invocations": 1}
                )
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan, substrate, registries, agent_factory=ResponseFactory(respond)
        )
        owner.start()
        try:
            owner.submit_intent("switch-router@s1", "running")
            self.assertTrue(running.wait(2))
            owner.submit_intent("switch-router@s2", "waiting")
            owner.pause_agent("switch-router@s2")
            release.set()
            owner.submit_intent("global-router", "unrelated")
            self.assertTrue(unrelated.wait(2))
        finally:
            release.set()
            owner.resume_agent("switch-router@s2")
            report = owner.stop()
        self.assertEqual(report.continuous.completed, 3)

    def test_partial_external_failure_does_not_commit_success_state(self) -> None:
        class FailingSubstrate(FakeSubstrateRuntime):
            def execute(self, run_id, request):
                return (
                    super()
                    .execute(run_id, request)
                    .model_copy(
                        update={
                            "status": ActionStatus.FAILED,
                            "issue": RuntimeIssue(
                                code="network.partial",
                                message="changed before failure",
                                target=request.target,
                            ),
                        }
                    )
                )

        def respond(context):
            return AgentResponse(
                proposals=(
                    ActionProposal(
                        id="route",
                        capability="openflow.flow.install",
                        target="s1",
                        arguments={"match": "ip", "actions": "normal"},
                    ),
                ),
                sharedStateUpdates=(
                    SharedStateUpdate(scope="run", key="ready", value=True),
                ),
            )

        plan = configured_shared_plan({"message": "unused"})
        substrate = FailingSubstrate()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        store = InMemorySharedStateStore()
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            shared_state=store,
            agent_factory=ResponseFactory(respond),
        )
        run = owner.start()
        owner.submit_intent("switch-router@s1", "route")
        report = owner.stop()
        result = report.continuous.invocations[0].result
        self.assertEqual(result.status.value, "failed")
        self.assertTrue(result.action_results[0].changed)
        self.assertEqual(result.shared_state_changes, ())
        self.assertEqual(
            store.snapshot(
                SharedStateAccess(
                    run_id=run.id,
                    deployment="unused",
                    agent_id="observer",
                    limits={"run": 4},
                )
            ).run,
            {},
        )

    def test_declared_cross_key_read_dependency_rejects_stale_action(self) -> None:
        read_started, writer_completed = Event(), Event()
        executed = []

        class RecordingSubstrate(FakeSubstrateRuntime):
            def execute(self, run_id, request):
                executed.append(request.id)
                return super().execute(run_id, request)

        def respond(context):
            if context.intent == "reader":
                read_started.set()
                if not writer_completed.wait(2):
                    raise TimeoutError("writer did not finish")
            return AgentResponse(
                proposals=(
                    ActionProposal(
                        id=context.intent,
                        capability="openflow.flow.install",
                        target=context.targets[0],
                        arguments={"match": "ip", "actions": "normal"},
                    ),
                ),
                sharedStateReads=(SharedStateRead(scope="run", key="route"),)
                if context.intent == "reader"
                else (),
                sharedStateUpdates=(
                    SharedStateUpdate(
                        scope="run",
                        key="ready" if context.intent == "reader" else "route",
                        value=True,
                    ),
                ),
            )

        plan = configured_shared_plan({"message": "unused"})
        store = InMemorySharedStateStore()
        substrate = RecordingSubstrate()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            shared_state=store,
            agent_factory=ResponseFactory(respond),
            invocation_listener=lambda r: (
                writer_completed.set()
                if r.result.agent_id == "switch-router@s1"
                else None
            ),
        )
        run = owner.start()
        store.apply(
            SharedStateAccess(
                run_id=run.id, deployment="unused", agent_id="seed", limits={"run": 4}
            ),
            (SharedStateUpdate(scope="run", key="route", value=False),),
        )
        try:
            owner.submit_intent("switch-router@s2", "reader")
            self.assertTrue(read_started.wait(2))
            owner.submit_intent("switch-router@s1", "writer")
        finally:
            report = owner.stop()
        self.assertEqual(executed, ["writer"])
        self.assertEqual(report.continuous.failed, 1)

    def test_requests_waiting_for_agent_capacity_remain_in_bounded_queue(self) -> None:
        started, release = Event(), Event()

        def respond(context):
            if context.intent == "running":
                started.set()
                if not release.wait(3):
                    raise TimeoutError("not released")
            return AgentResponse(message=context.intent)

        plan = configured_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "resource_limits": plan.resource_limits.model_copy(
                    update={"max_concurrent_invocations": 3}
                ),
                "agents": tuple(
                    a.model_copy(
                        update={
                            "execution": a.execution.model_copy(
                                update={"queue_capacity": 1, "overflow": "reject"}
                            )
                        }
                    )
                    for a in plan.agents
                ),
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan, substrate, registries, agent_factory=ResponseFactory(respond)
        )
        owner.start()
        try:
            owner.submit_intent("switch-router@s1", "running")
            self.assertTrue(started.wait(2))
            for intent in ("pending", "overflow-one", "overflow-two"):
                owner.submit_intent("switch-router@s1", intent)
            # Stop drains dispatch before timing out on the active invocation.
            failed = owner.stop(timeout_seconds=0.1)
            self.assertEqual(failed.continuous.rejected, 2)
        finally:
            release.set()
            report = owner.stop()
        self.assertEqual(report.continuous.completed, 2)

    def test_unknown_or_untrusted_effects_conflict_even_on_different_targets(
        self,
    ) -> None:
        for provider in ("fake.openflow", "custom.action"):
            with self.subTest(provider=provider):
                executing, release, rejected = Event(), Event(), Event()

                class BlockingSubstrate(FakeSubstrateRuntime):
                    def execute(
                        self, run_id, request, executing=executing, release=release
                    ):
                        executing.set()
                        if not release.wait(3):
                            raise TimeoutError("not released")
                        return super().execute(run_id, request)

                def respond(context):
                    return AgentResponse(
                        proposals=(
                            ActionProposal(
                                id=context.intent,
                                capability="openflow.flow.install",
                                target=context.targets[0],
                                arguments={"match": "ip", "actions": "normal"},
                            ),
                        )
                    )

                plan = configured_plan({"message": "unused"})
                snapshot = copy.deepcopy(plan.snapshot)
                capability = next(
                    c
                    for c in snapshot["capabilityDefinitions"]
                    if c["metadata"]["name"] == "openflow.flow.install"
                )
                capability["provider"] = provider
                if provider == "fake.openflow":
                    capability["effects"] = []
                plan = plan.model_copy(update={"snapshot": snapshot})
                substrate = BlockingSubstrate()
                registries = ProviderRegistries()
                register_builtin_providers(registries, substrate)
                if provider == "custom.action":
                    registries.capabilities.register(
                        provider,
                        ProviderPlugin(
                            kind=ProviderKind.CAPABILITY,
                            factory=lambda definition, substrate=substrate: (
                                SubstrateActionProvider(definition, substrate)
                            ),
                        ),
                    )
                owner = ExperimentRuntime(
                    plan,
                    substrate,
                    registries,
                    agent_factory=ResponseFactory(respond),
                    invocation_listener=lambda r, rejected=rejected: (
                        rejected.set() if r.result.status.value == "rejected" else None
                    ),
                )
                owner.start()
                try:
                    owner.submit_intent("switch-router@s1", "first")
                    self.assertTrue(executing.wait(2))
                    owner.submit_intent("switch-router@s2", "second")
                    self.assertTrue(rejected.wait(1))
                finally:
                    release.set()
                    report = owner.stop()
                issue = report.continuous.invocations[0].result.issue
                assert issue is not None
                self.assertEqual(issue.code, "coordination.conflict.rejected")

    def test_independent_state_keys_and_network_actions_overlap(self) -> None:
        actions = Barrier(2)

        class ConcurrentSubstrate(FakeSubstrateRuntime):
            def execute(self, run_id, request):
                actions.wait(timeout=2)
                return super().execute(run_id, request)

        def respond(context):
            return AgentResponse(
                proposals=(
                    ActionProposal(
                        id=context.agent_id,
                        capability="openflow.flow.install",
                        target=context.targets[0],
                        arguments={"match": "ip", "actions": "normal"},
                    ),
                ),
                sharedStateUpdates=(
                    SharedStateUpdate(scope="run", key=context.targets[0], value=True),
                ),
            )

        plan = configured_shared_plan({"message": "unused"})
        substrate = ConcurrentSubstrate()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        store = InMemorySharedStateStore()
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            shared_state=store,
            agent_factory=ResponseFactory(respond),
        )
        run = owner.start()
        store.apply(
            SharedStateAccess(
                run_id=run.id, deployment="unused", agent_id="seed", limits={"run": 4}
            ),
            (
                SharedStateUpdate(scope="run", key="s1", value=False),
                SharedStateUpdate(scope="run", key="s2", value=False),
            ),
        )
        owner.submit_intent("switch-router@s1", "write first key")
        owner.submit_intent("switch-router@s2", "write second key")
        report = owner.stop()
        self.assertEqual(
            report.continuous.failed,
            0,
            [r.result.issue for r in report.continuous.invocations],
        )
        state = store.snapshot(
            SharedStateAccess(
                run_id=run.id,
                deployment="unused",
                agent_id="observer",
                limits={"run": 4},
            )
        )
        self.assertEqual(set(state.run), {"s1", "s2"})

    def test_monitor_reads_intermediate_network_and_stop_drains_action(self) -> None:
        executing = Event()
        release = Event()
        monitored = Event()
        phases = []

        class TransitionalSubstrate(FakeSubstrateRuntime):
            def execute(self, run_id, request):
                executing.set()
                if not release.wait(3):
                    raise TimeoutError("not released")
                return super().execute(run_id, request)

            def observe(self, run_id, query):
                result = super().observe(run_id, query)
                return result.model_copy(
                    update={
                        "values": {
                            "phase": "intermediate"
                            if executing.is_set() and not release.is_set()
                            else "stable"
                        }
                    }
                )

        def respond(context):
            if context.agent_id == "switch-router@s1":
                return AgentResponse(
                    proposals=(
                        ActionProposal(
                            id="route",
                            capability="openflow.flow.install",
                            target="s1",
                            arguments={"match": "ip", "actions": "normal"},
                        ),
                    )
                )
            phases.extend(value["phase"] for value in context.observations.values())
            return AgentResponse(
                message="observed transition",
                proposals=(
                    ActionProposal(
                        id="observe",
                        capability="openflow.flows",
                        target="s1",
                        arguments={},
                    ),
                ),
            )

        plan = configured_plan({"message": "unused"})
        snapshot = copy.deepcopy(plan.snapshot)
        observation = copy.deepcopy(
            next(
                c
                for c in snapshot["capabilityDefinitions"]
                if c["metadata"]["name"] == "openflow.flow.install"
            )
        )
        observation.update(
            {
                "metadata": {"name": "openflow.flows"},
                "provider": "substrate.observation",
                "effects": [],
                "input-schema": {},
                "output-schema": {},
            }
        )
        snapshot["capabilityDefinitions"].append(observation)
        s1 = next(a for a in plan.agents if a.id == "switch-router@s1")
        plan = plan.model_copy(
            update={
                "snapshot": snapshot,
                "agents": tuple(
                    a.model_copy(
                        update={
                            "attachment": s1.attachment,
                            "capabilities": (*a.capabilities, "openflow.flows"),
                        }
                    )
                    if a.id == "switch-router@s2"
                    else a
                    for a in plan.agents
                ),
            }
        )
        substrate = TransitionalSubstrate()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            agent_factory=ResponseFactory(respond),
            invocation_listener=lambda r: (
                monitored.set() if r.result.agent_id == "switch-router@s2" else None
            ),
        )
        run = owner.start()
        try:
            owner.submit_intent("switch-router@s1", "route")
            self.assertTrue(executing.wait(2))
            owner.submit_intent("switch-router@s2", "monitor")
            self.assertTrue(monitored.wait(1))
            failed_stop = owner.stop(timeout_seconds=0.05)
            self.assertIsNone(failed_stop.teardown)
            self.assertEqual(substrate.inspect(run.id).run.state, RunState.RUNNING)
        finally:
            release.set()
            stopped = owner.stop()
        self.assertTrue(phases)
        self.assertEqual(set(phases), {"intermediate"})
        self.assertEqual(stopped.state, ExperimentRuntimeState.STOPPED)
        self.assertEqual(stopped.continuous.completed, 2)
        monitor = next(
            r.result
            for r in stopped.continuous.invocations
            if r.result.agent_id == "switch-router@s2"
        )
        self.assertEqual(monitor.action_results[0].output["phase"], "intermediate")

    def test_pending_conflicts_serialize_or_follow_priority_without_preemption(
        self,
    ) -> None:
        for policy, shared in (
            ("serialize", False),
            ("priority", False),
            ("serialize", True),
            ("priority", True),
        ):
            with self.subTest(policy=policy, shared=shared):
                executing = Event()
                release = Event()
                low_ready = Event()
                high_ready = Event()
                next_executing = Event()
                order = []

                class BlockingSubstrate(FakeSubstrateRuntime):
                    def execute(
                        self,
                        run_id,
                        request,
                        order=order,
                        executing=executing,
                        release=release,
                        next_executing=next_executing,
                    ):
                        order.append(request.id)
                        if request.id == "running":
                            executing.set()
                            if not release.wait(3):
                                raise TimeoutError("not released")
                        else:
                            next_executing.set()
                        return super().execute(run_id, request)

                def respond(
                    context, low_ready=low_ready, high_ready=high_ready, shared=shared
                ):
                    if context.intent == "low":
                        low_ready.set()
                    if context.intent == "high":
                        high_ready.set()
                    return AgentResponse(
                        sharedStateUpdates=(
                            SharedStateUpdate(
                                scope="run", key="route", value=context.intent
                            ),
                        )
                        if shared and context.intent != "running"
                        else (),
                        proposals=(
                            ActionProposal(
                                id=context.intent,
                                capability="openflow.flow.install",
                                target="s1",
                                arguments={"match": "ip", "actions": "normal"},
                            ),
                        ),
                    )

                plan = (configured_shared_plan if shared else configured_plan)(
                    {"message": "unused"}
                )
                s1 = next(a for a in plan.agents if a.id == "switch-router@s1")
                plan = plan.model_copy(
                    update={
                        "policies": plan.policies.model_copy(
                            update={"conflicting_actions": policy}
                        ),
                        "agents": tuple(
                            a.model_copy(
                                update={
                                    "attachment": s1.attachment,
                                    "capabilities": s1.capabilities,
                                    "privileges": s1.privileges,
                                    "priority": 10
                                    if a.id == "control-router@control-domain"
                                    else 0,
                                }
                            )
                            for a in plan.agents
                        ),
                    }
                )
                substrate = BlockingSubstrate()
                registries = ProviderRegistries()
                register_builtin_providers(registries, substrate)
                owner = ExperimentRuntime(
                    plan,
                    substrate,
                    registries,
                    agent_factory=ResponseFactory(respond),
                    shared_state=InMemorySharedStateStore() if shared else None,
                )
                owner.start()
                try:
                    owner.submit_intent("switch-router@s1", "running")
                    self.assertTrue(executing.wait(2))
                    owner.submit_intent("switch-router@s2", "low")
                    self.assertTrue(low_ready.wait(2))
                    self.assertFalse(next_executing.wait(0.05))
                    owner.submit_intent("control-router@control-domain", "high")
                    self.assertTrue(high_ready.wait(2))
                    self.assertFalse(next_executing.wait(0.05))
                finally:
                    release.set()
                    report = owner.stop()
                self.assertEqual(
                    report.continuous.failed,
                    1 if shared else 0,
                    [r.result.issue for r in report.continuous.invocations],
                )
                self.assertEqual(
                    order,
                    (
                        ["running", "high"]
                        if policy == "priority"
                        else ["running", "low"]
                    )
                    if shared
                    else (
                        ["running", "high", "low"]
                        if policy == "priority"
                        else ["running", "low", "high"]
                    ),
                )

    def test_one_slot_and_per_agent_one_remain_serial(self) -> None:
        for global_limit, same_agent in ((1, False), (4, True)):
            with self.subTest(global_limit=global_limit, same_agent=same_agent):
                started = Event()
                release = Event()
                second = Event()

                def respond(context, started=started, release=release, second=second):
                    if context.intent == "first":
                        started.set()
                        if not release.wait(3):
                            raise TimeoutError("not released")
                    else:
                        second.set()
                    return AgentResponse(message=context.intent)

                plan = configured_plan({"message": "unused"})
                plan = plan.model_copy(
                    update={
                        "resource_limits": plan.resource_limits.model_copy(
                            update={"max_concurrent_invocations": global_limit}
                        )
                    }
                )
                substrate = FakeSubstrateRuntime()
                registries = ProviderRegistries()
                register_builtin_providers(registries, substrate)
                owner = ExperimentRuntime(
                    plan, substrate, registries, agent_factory=ResponseFactory(respond)
                )
                owner.start()
                try:
                    owner.submit_intent("switch-router@s1", "first")
                    self.assertTrue(started.wait(2))
                    owner.submit_intent(
                        "switch-router@s1" if same_agent else "switch-router@s2",
                        "second",
                    )
                    self.assertFalse(second.wait(0.1))
                finally:
                    release.set()
                    report = owner.stop()
                self.assertTrue(second.is_set())
                self.assertEqual(report.continuous.failed, 0)

    def test_nested_delegates_obey_global_budget_and_see_parent_state(self) -> None:
        for limit in (1, 2):
            with self.subTest(limit=limit):
                lock = Lock()
                active = 0
                maximum = 0
                siblings = Barrier(2) if limit == 2 else None
                seen = []

                def respond(context, lock=lock, seen=seen, siblings=siblings):
                    nonlocal active, maximum
                    with lock:
                        active += 1
                        maximum = max(maximum, active)
                    try:
                        if context.agent_id == "global-router":
                            return AgentResponse(
                                sharedStateUpdates=(
                                    SharedStateUpdate(
                                        scope="run", key="parent", value="eligible"
                                    ),
                                ),
                                delegations=tuple(
                                    DelegationProposal(
                                        id=t, targetAgentId=t, intent="child"
                                    )
                                    for t in ("switch-router@s1", "switch-router@s2")
                                ),
                            )
                        seen.append(context.shared_state.run["parent"].value)
                        if context.agent_id.startswith("switch-router"):
                            if siblings is not None:
                                siblings.wait(timeout=2)
                            target = (
                                "host-router@h1"
                                if context.agent_id.endswith("s1")
                                else "host-router@h2"
                            )
                            return AgentResponse(
                                delegations=(
                                    DelegationProposal(
                                        id=target, targetAgentId=target, intent="nested"
                                    ),
                                )
                            )
                        return AgentResponse(message="nested done")
                    finally:
                        with lock:
                            active -= 1

                plan = configured_shared_plan({"message": "unused"})
                plan = plan.model_copy(
                    update={
                        "resource_limits": plan.resource_limits.model_copy(
                            update={"max_concurrent_invocations": limit}
                        ),
                        "coordination": CoordinationPlan(
                            mode=CoordinationMode.DISTRIBUTED,
                            edges=tuple(
                                CoordinationEdge(
                                    source=a.id, target=b.id, relationship="peer"
                                )
                                for a in plan.agents
                                for b in plan.agents
                                if a.id != b.id
                            ),
                        ),
                    }
                )
                substrate = FakeSubstrateRuntime()
                registries = ProviderRegistries()
                register_builtin_providers(registries, substrate)
                owner = ExperimentRuntime(
                    plan,
                    substrate,
                    registries,
                    shared_state=InMemorySharedStateStore(),
                    agent_factory=ResponseFactory(respond),
                )
                owner.start()
                owner.submit_intent("global-router", "parent")
                report = owner.stop()
                outcome = report.continuous.invocations[0].coordination
                assert outcome is not None
                self.assertEqual(len(outcome.invocations), 5)
                self.assertTrue(
                    all(
                        r.result.status.value == "succeeded"
                        for r in outcome.invocations
                    )
                )
                self.assertEqual(seen, ["eligible"] * 4)
                self.assertEqual(maximum, limit)

    def test_waiting_parent_does_not_reserve_an_agent_or_global_slot(self) -> None:
        delegated = Event()
        release = Event()
        next_parent = Event()

        def respond(context):
            if context.agent_id == "global-router":
                if context.intent == "second":
                    next_parent.set()
                    return AgentResponse(message="second parent")
                return AgentResponse(
                    delegations=(
                        DelegationProposal(
                            id="child",
                            targetAgentId="switch-router@s1",
                            intent="slow child",
                        ),
                    )
                )
            delegated.set()
            if not release.wait(3):
                raise TimeoutError("child not released")
            return AgentResponse(message="child")

        plan = configured_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.CENTRALIZED,
                    edges=tuple(
                        CoordinationEdge(
                            source="global-router",
                            target=a.id,
                            relationship="coordinates",
                        )
                        for a in plan.agents
                        if a.id != "global-router"
                    ),
                )
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan, substrate, registries, agent_factory=ResponseFactory(respond)
        )
        owner.start()
        try:
            owner.submit_intent("global-router", "first")
            self.assertTrue(delegated.wait(2))
            owner.submit_intent("global-router", "second")
            self.assertTrue(next_parent.wait(1))
        finally:
            release.set()
            report = owner.stop()
        self.assertEqual(report.continuous.completed, 2)

    def test_stale_shared_decision_is_rejected_before_network_action(self) -> None:
        reasoning = Barrier(2)
        executed = []

        class RecordingSubstrate(FakeSubstrateRuntime):
            def execute(self, run_id, request):
                executed.append(request.target)
                return super().execute(run_id, request)

        def respond(context):
            reasoning.wait(timeout=2)
            return AgentResponse(
                proposals=(
                    ActionProposal(
                        id=context.agent_id,
                        capability="openflow.flow.install",
                        target=context.targets[0],
                        arguments={"match": "ip", "actions": "normal"},
                    ),
                ),
                sharedStateUpdates=(
                    SharedStateUpdate(scope="run", key="route", value=context.agent_id),
                ),
            )

        plan = configured_shared_plan({"message": "unused"})
        substrate = RecordingSubstrate()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            shared_state=InMemorySharedStateStore(),
            agent_factory=ResponseFactory(respond),
        )
        owner.start()
        owner.submit_intent("switch-router@s1", "update")
        owner.submit_intent("switch-router@s2", "update")
        report = owner.stop()
        self.assertEqual(len(executed), 1)
        self.assertEqual(report.continuous.failed, 1)
        loser = next(
            r.result
            for r in report.continuous.invocations
            if r.result.status.value == "rejected"
        )
        assert loser.issue is not None
        self.assertEqual(loser.issue.code, "state.version.conflict")
        self.assertEqual(loser.shared_state_changes, ())

    def test_parallel_same_agent_can_complete_out_of_order(self) -> None:
        first_started = Event()
        release = Event()
        second_completed = Event()

        def respond(context):
            if context.intent == "slow":
                first_started.set()
                if not release.wait(3):
                    raise TimeoutError("not released")
            return AgentResponse(message=context.intent)

        plan = configured_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "agents": tuple(
                    a.model_copy(
                        update={
                            "execution": a.execution.model_copy(
                                update={"max_concurrency": 2}
                            )
                        }
                    )
                    for a in plan.agents
                )
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            agent_factory=ResponseFactory(respond),
            invocation_listener=lambda r: second_completed.set(),
        )
        owner.start()
        try:
            owner.submit_intent("switch-router@s1", "slow")
            self.assertTrue(first_started.wait(2))
            owner.submit_intent("switch-router@s1", "fast")
            self.assertTrue(second_completed.wait(1))
        finally:
            release.set()
            report = owner.stop()
        self.assertEqual(
            [
                r.result.response.message
                for r in report.continuous.invocations
                if r.result.response is not None
            ],
            ["fast", "slow"],
        )

    def test_failed_action_does_not_publish_shared_success(self) -> None:
        def respond(context):
            return AgentResponse(
                proposals=(
                    ActionProposal(
                        id="bad",
                        capability="openflow.flow.install",
                        target="s1",
                        arguments={},
                    ),
                ),
                sharedStateUpdates=(
                    SharedStateUpdate(scope="run", key="route-ready", value=True),
                ),
            )

        plan = configured_shared_plan({"message": "unused"})
        store = InMemorySharedStateStore()
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            shared_state=store,
            agent_factory=ResponseFactory(respond),
        )
        run = owner.start()
        owner.submit_intent("switch-router@s1", "invalid action")
        report = owner.stop()
        access = SharedStateAccess(
            run_id=run.id,
            deployment="switch-router",
            agent_id="observer",
            limits={"run": 4},
        )
        self.assertEqual(store.snapshot(access).run, {})
        self.assertEqual(
            report.continuous.invocations[0].result.shared_state_changes, ()
        )

    def test_cross_request_conflict_is_rejected_while_action_runs(self) -> None:
        executing = Event()
        release = Event()
        rejected = Event()

        class BlockingSubstrate(FakeSubstrateRuntime):
            def execute(self, run_id, request):
                executing.set()
                if not release.wait(3):
                    raise TimeoutError("action not released")
                return (
                    super()
                    .execute(run_id, request)
                    .model_copy(
                        update={
                            "status": ActionStatus.FAILED,
                            "issue": RuntimeIssue(
                                code="network.partial",
                                message="failed after admission",
                                target=request.target,
                            ),
                        }
                    )
                )

        def respond(context):
            return AgentResponse(
                proposals=(
                    ActionProposal(
                        id=context.intent,
                        capability="openflow.flow.install",
                        target="s1",
                        arguments={"match": "ip", "actions": "normal"},
                    ),
                ),
                sharedStateUpdates=(
                    SharedStateUpdate(scope="run", key="route", value=True),
                ),
            )

        plan = configured_shared_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "agents": tuple(
                    a.model_copy(
                        update={
                            "attachment": next(
                                x.attachment
                                for x in plan.agents
                                if x.id == "switch-router@s1"
                            )
                        }
                    )
                    if a.id == "switch-router@s2"
                    else a
                    for a in plan.agents
                )
            }
        )
        substrate = BlockingSubstrate()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            agent_factory=ResponseFactory(respond),
            shared_state=InMemorySharedStateStore(),
            invocation_listener=lambda r: (
                rejected.set() if r.result.status.value == "rejected" else None
            ),
        )
        owner.start()
        try:
            owner.submit_intent("switch-router@s1", "first")
            self.assertTrue(executing.wait(2))
            owner.submit_intent("switch-router@s2", "second")
            self.assertTrue(
                rejected.wait(1), "cross-request conflict escaped arbitration"
            )
        finally:
            release.set()
            report = owner.stop()
        self.assertEqual(report.continuous.failed, 2)
        assert report.continuous.invocations[0].result.issue is not None
        self.assertEqual(
            report.continuous.invocations[0].result.issue.code,
            "coordination.conflict.rejected",
        )

    def test_independent_sibling_delegates_overlap(self) -> None:
        siblings = Barrier(2)

        def respond(context):
            if context.agent_id == "global-router":
                return AgentResponse(
                    delegations=tuple(
                        DelegationProposal(
                            id=target, targetAgentId=target, intent="inspect"
                        )
                        for target in ("switch-router@s1", "switch-router@s2")
                    )
                )
            siblings.wait(timeout=2)
            return AgentResponse(message=context.agent_id)

        plan = configured_plan({"message": "unused"})
        plan = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.CENTRALIZED,
                    edges=tuple(
                        CoordinationEdge(
                            source="global-router",
                            target=a.id,
                            relationship="coordinates",
                        )
                        for a in plan.agents
                        if a.id != "global-router"
                    ),
                )
            }
        )
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan, substrate, registries, agent_factory=ResponseFactory(respond)
        )
        owner.start()
        owner.submit_intent("switch-router@s1", "delegate")
        report = owner.stop()
        outcome = report.continuous.invocations[0].coordination
        assert outcome is not None
        self.assertEqual(len(outcome.invocations), 3)
        self.assertTrue(
            all(r.result.status.value == "succeeded" for r in outcome.invocations)
        )

    def test_independent_monitor_finishes_during_blocked_routing(self) -> None:
        routing = Event()
        release = Event()
        monitored = Event()
        completed = Event()

        def respond(context):
            if context.agent_id == "switch-router@s1":
                routing.set()
                if not release.wait(3):
                    raise TimeoutError("routing was not released")
            else:
                monitored.set()
            return AgentResponse(message=context.intent)

        plan = configured_plan({"message": "unused"})
        substrate = FakeSubstrateRuntime()
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        sink = MemoryAuditSink()
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            agent_factory=ResponseFactory(respond),
            audit=AuditRecorder(sink),
            invocation_listener=lambda record: completed.set(),
        )
        owner.start()
        try:
            first = owner.submit_intent("switch-router@s1", "slow routing")
            self.assertTrue(routing.wait(2))
            second = owner.submit_intent("switch-router@s2", "live monitor")
            self.assertTrue(monitored.wait(1), "independent reasoning was serialized")
            self.assertTrue(completed.wait(1))
        finally:
            release.set()
            report = owner.stop()
        self.assertEqual(
            [r.event_id for r in report.continuous.invocations],
            [second.event_id, first.event_id],
        )
        for record in report.continuous.invocations:
            assert record.coordination is not None
            assert record.result.response is not None
            self.assertEqual(record.coordination.correlation_id, record.event_id)
            self.assertEqual(
                record.result.response.message,
                "live monitor"
                if record.event_id == second.event_id
                else "slow routing",
            )
            started = next(
                event
                for event in sink.events
                if event.type == AuditEventType.AGENT_STARTED
                and event.invocation_id == record.result.invocation_id
            )
            self.assertEqual(started.agent_id, record.result.agent_id)
            context = started.data["context"]
            assert isinstance(context, dict)
            coordination = context["coordination"]
            assert isinstance(coordination, dict)
            self.assertEqual(coordination["correlationId"], record.event_id)

    def test_owner_runs_intent_and_persists_complete_ordered_history(self) -> None:
        plan = configured_plan({"message": "handled continuously"})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "owner-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)

        with TemporaryDirectory() as temporary:
            ledger = SQLiteRunLedger(Path(temporary) / "runs.sqlite3")
            owner = ExperimentRuntime(
                plan,
                substrate,
                registries,
                audit=AuditRecorder(LedgerAuditSink(ledger)),
                ledger=ledger,
                event_id_factory=lambda: "manual-event-1",
            )

            run = owner.start()
            event = owner.submit_intent(
                "switch-router@s1",
                "inspect forwarding",
            )
            report = owner.stop()
            repeated = owner.stop()
            records = ledger.records(run.id)
            manifest = ledger.get_run(run.id)
            ledger.close()

        self.assertEqual(run.id, "owner-run")
        self.assertEqual(event.event_id, "manual-event-1")
        self.assertEqual(report.state, ExperimentRuntimeState.STOPPED)
        self.assertEqual(report.continuous.completed, 1)
        self.assertEqual(report.continuous.failed, 0)
        invocation = report.continuous.invocations[0]
        self.assertIsNotNone(invocation.coordination)
        assert invocation.coordination is not None
        self.assertEqual(invocation.coordination.correlation_id, event.event_id)
        self.assertEqual(
            invocation.coordination.requested_agent_id,
            "switch-router@s1",
        )
        self.assertEqual(
            invocation.coordination.entry_agent_id,
            "switch-router@s1",
        )
        self.assertEqual(
            invocation.coordination.messages[0].triggering_event_id,
            event.event_id,
        )
        self.assertEqual(
            invocation.result.invocation_id,
            invocation.coordination.invocations[0].result.invocation_id,
        )
        self.assertEqual(report, repeated)
        assert report.teardown is not None
        self.assertEqual(report.teardown.run.state, RunState.STOPPED)
        self.assertEqual(owner.state, ExperimentRuntimeState.STOPPED)
        self.assertEqual(manifest.plan_digest, plan.digest)
        self.assertEqual(
            tuple(record.sequence for record in records),
            tuple(range(1, len(records) + 1)),
        )
        self.assertEqual(records[0].type, "run.starting")
        self.assertIn("intent.manual", [record.type for record in records])
        self.assertIn(
            LedgerRecordCategory.INVOCATION,
            [record.category for record in records],
        )
        self.assertEqual(records[-1].type, "run.stopped")

    def test_owner_routes_manual_intent_through_central_coordinator(self) -> None:
        plan = configured_plan({"message": "coordinated"})
        coordinator = "global-router"
        plan = plan.model_copy(
            update={
                "coordination": CoordinationPlan(
                    mode=CoordinationMode.CENTRALIZED,
                    edges=tuple(
                        CoordinationEdge(
                            source=coordinator,
                            target=agent.id,
                            relationship="coordinates",
                        )
                        for agent in plan.agents
                        if agent.id != coordinator
                    ),
                )
            }
        )
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "central-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        owner = ExperimentRuntime(
            plan,
            substrate,
            registries,
            event_id_factory=lambda: "central-event-1",
        )

        owner.start()
        owner.submit_intent("switch-router@s1", "inspect forwarding")
        report = owner.stop()

        record = report.continuous.invocations[0]
        self.assertIsNotNone(record.coordination)
        assert record.coordination is not None
        self.assertEqual(record.coordination.entry_agent_id, coordinator)
        self.assertEqual(record.result.agent_id, coordinator)
        self.assertEqual(
            record.coordination.messages[0].target_agent_id,
            coordinator,
        )

    def test_manifest_failure_rolls_back_the_deployed_substrate(self) -> None:
        plan = configured_plan({"message": "unused"})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "duplicate-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)

        with TemporaryDirectory() as temporary:
            ledger = SQLiteRunLedger(Path(temporary) / "runs.sqlite3")
            ledger.create_run(
                RunManifest.from_plan(
                    "duplicate-run",
                    plan,
                    created_at=NOW,
                )
            )
            owner = ExperimentRuntime(
                plan,
                substrate,
                registries,
                ledger=ledger,
            )

            with self.assertRaises(LedgerError):
                owner.start()

            ledger.close()

        self.assertEqual(owner.state, ExperimentRuntimeState.FAILED)
        self.assertEqual(
            substrate.inspect("duplicate-run").run.state,
            RunState.STOPPED,
        )

    def test_stop_does_not_teardown_until_runtime_workers_have_stopped(self) -> None:
        class RetryableContinuous:
            def __init__(self) -> None:
                self.stop_calls = 0

            def start(self) -> None:
                pass

            def stop(self, **options) -> ContinuousRuntimeReport:
                del options
                self.stop_calls += 1
                if self.stop_calls == 1:
                    raise ContinuousRuntimeError(
                        "worker still active",
                        code="runtime.stop.timeout",
                    )
                return ContinuousRuntimeReport()

            def report(self) -> ContinuousRuntimeReport:
                return ContinuousRuntimeReport()

        class StoppableTelemetry:
            def start(self) -> None:
                pass

            def stop(self, **options) -> TelemetryPipelineReport:
                del options
                return TelemetryPipelineReport()

            def report(self) -> TelemetryPipelineReport:
                return TelemetryPipelineReport()

        plan = configured_plan({"message": "unused"})
        substrate = FakeSubstrateRuntime(run_id_factory=lambda: "retry-run")
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        continuous = RetryableContinuous()
        telemetry = StoppableTelemetry()
        owner = ExperimentRuntime(plan, substrate, registries)

        with (
            patch(
                "mininet_ai.experiment.ContinuousAgentRuntime",
                return_value=continuous,
            ),
            patch(
                "mininet_ai.experiment.TelemetryPipeline",
                return_value=telemetry,
            ),
        ):
            owner.start()
            failed = owner.stop(timeout_seconds=0.01)

            self.assertEqual(failed.state, ExperimentRuntimeState.FAILED)
            self.assertIsNone(failed.teardown)
            self.assertEqual(
                substrate.inspect("retry-run").run.state,
                RunState.RUNNING,
            )

            stopped = owner.stop(timeout_seconds=1)

        self.assertEqual(stopped.state, ExperimentRuntimeState.STOPPED)
        assert stopped.teardown is not None
        self.assertEqual(stopped.run.state, RunState.STOPPED)
        self.assertEqual(continuous.stop_calls, 2)


if __name__ == "__main__":
    unittest.main()
