from concurrent.futures import ThreadPoolExecutor

import pytest

from mininet_ai.experiments.latency.trials import (
    AdmissionGate,
    TrialConfig,
    TrialCoordinator,
)
from mininet_ai.outcomes import CleanupOutcome, CleanupStatus
from mininet_ai.runtime.instrumentation import ExecutionContext, PhaseRecorder


class FakeJob:
    def __init__(self, runtime, name, fn):
        self.runtime, self.name, self.fn = runtime, name, fn
        self.started_ns = runtime.now
        self.due = runtime.now + runtime.delays.get(name, 0)
        self.ended_ns = None
        self.value = None
        self.error = None

    @property
    def done(self):
        if self.ended_ns is None and self.runtime.now >= self.due:
            try:
                self.value = self.fn()
            except Exception as error:
                self.error = error
            self.ended_ns = self.runtime.now
        return self.ended_ns is not None


class FakeRuntime:
    def __init__(self, **delays):
        self.now = 0
        self.delays = delays
        self.jobs = []

    def clock(self):
        return self.now

    def start(self, name, fn):
        job = FakeJob(self, name, fn)
        self.jobs.append(job)
        if job.due == self.now:
            assert job.done
        return job

    def wait_until(self, tick):
        pending = [j.due for j in self.jobs if j.ended_ns is None and j.due > self.now]
        self.now = max(self.now, min([tick, *pending]))
        for job in tuple(self.jobs):
            if job.ended_ns is None and job.due <= self.now:
                assert job.done


class Session:
    def __init__(self):
        self.injected = False
        self.probe_count = 0
        self.responses = []
        self.removed = False
        self.ready_ack = False

    def setup(self):
        pass

    def ready(self):
        self.ready_ack = True
        return True

    def inject(self):
        assert self.ready_ack
        self.injected = True

    def endpoints_down(self):
        return (self.injected, self.injected)

    def probe(self):
        if self.injected:
            self.probe_count += 1
            return (self.probe_count > 1, self.probe_count > 1)
        return (True, True)

    def respond(self, gate, observation_id):
        self.responses.append(observation_id)

    def cleanup(self):
        self.removed = True
        return CleanupOutcome(
            status=CleanupStatus.VERIFIED, evidence=("absent",), evidence_gaps=()
        )


def coordinate(session=None, runtime=None, **config):
    runtime = runtime or FakeRuntime()
    session = session or Session()
    recorder = PhaseRecorder("test", clock=runtime.clock)
    coordinator = TrialCoordinator(runtime, recorder, TrialConfig(warmup=0, **config))
    result = coordinator.trial(session, ExecutionContext("run", "trial", "fault"))
    return result, runtime, session, recorder


def test_trial_confirms_three_bidirectional_rounds_from_observed_onset():
    result, _runtime, session, recorder = coordinate()
    assert result.outcome.status == "recovered"
    assert result.outcome.onset_ns == 2_000_000_000
    assert result.outcome.recovery_ns == 5_000_000_000
    assert result.outcome.first_success_ns == 3_000_000_000
    assert len(session.responses) == 1
    assert result.cleanup.status == "verified"
    assert session.removed
    assert recorder.trustworthy(ExecutionContext("run", "trial", "fault"))


def test_gate_closes_invocations_and_actions_together_and_never_retries():
    gate = AdmissionGate()
    assert gate.invocation("a")
    assert not gate.invocation("a")
    assert gate.policy("a")
    assert not gate.policy("a")
    assert gate.action("a", "rule-1")
    assert not gate.action("a", "rule-1")
    with ThreadPoolExecutor() as workers:
        list(workers.map(lambda _: gate.close(), range(20)))
        assert not any(workers.map(lambda i: gate.invocation(str(i)), range(20)))
        assert not any(workers.map(lambda i: gate.action("a", str(i)), range(20)))


def test_trial_defaults_keep_distinct_budgets_and_populations():
    config = TrialConfig()
    assert (config.measured, config.warmup) == (10, 1)
    assert (config.setup_seconds, config.injection_seconds) == (120, 10)
    assert (config.response_seconds, config.cleanup_seconds) == (120, 30)
    assert (config.sample_seconds, config.probe_seconds) == (0.1, 1)


@pytest.mark.parametrize(
    "case, expected",
    [
        ("setup", "setup_failed"),
        ("baseline", "invalid"),
        ("injection", "injection_failed"),
        ("unconfirmed", "injection_failed"),
        ("no_disruption", "no_observed_disruption"),
        ("timeout", "timeout"),
        ("observer", "invalid"),
        ("restored", "invalid"),
    ],
)
def test_closed_trial_outcomes_and_valid_negative_probes(case, expected):
    class FaultSession(Session):
        def setup(self):
            if case == "setup":
                raise RuntimeError("deployment failed")

        def inject(self):
            if case == "injection":
                raise RuntimeError("injection failed")
            super().inject()

        def endpoints_down(self):
            if self.injected and case == "observer":
                raise RuntimeError("broken observer")
            if case == "unconfirmed" or (case == "restored" and self.probe_count >= 2):
                return (False, False)
            return super().endpoints_down()

        def probe(self):
            if case == "baseline":
                return (False, True)
            if self.injected and case == "timeout":
                return (False, False)
            if case == "no_disruption":
                return (True, True)
            return super().probe()

    result, *_ = coordinate(FaultSession(), response_seconds=6, injection_seconds=3)
    assert result.outcome.status == expected
    assert result.outcome.recovery_ns is None
    assert result.cleanup.status == "verified"


def test_slow_probe_misses_slots_and_cannot_backdate_recovery():
    result, *_ = coordinate(
        runtime=FakeRuntime(probe=1_500_000_000), response_seconds=5
    )
    assert result.outcome.status == "timeout"
    assert any(s.kind == "probe" and s.job is None for s in result.slots)
    assert result.outcome.recovery_ns is None


def test_late_probe_is_retained_without_revising_frozen_outcome():
    class LateRecovery(FakeRuntime):
        def start(self, name, fn):
            if name == "probe":
                self.delays[name] = (
                    5_000_000_000 if any(j.name == "probe" for j in self.jobs) else 0
                )
            return super().start(name, fn)

    result, _runtime, *_ = coordinate(runtime=LateRecovery(), response_seconds=2)
    assert result.outcome.status == "timeout"
    assert result.outcome.ended_ns == result.outcome.response_deadline_ns
    assert any(
        s.job
        and s.job.ended_ns is not None
        and s.job.ended_ns > result.outcome.ended_ns
        for s in result.slots
    )
    assert result.cleanup.status == "verified"


def test_unknown_remote_containment_is_not_cleared_by_local_cancellation():
    runtime = FakeRuntime()
    recorder = PhaseRecorder("test", clock=runtime.clock)
    context = ExecutionContext("run", "trial", "fault")
    request = recorder.requests.start(context, "ollama")
    recorder.requests.interrupted(request, "local timeout", cancelled=True)
    result = TrialCoordinator(runtime, recorder, TrialConfig(cleanup_seconds=2)).trial(
        Session(), context
    )
    assert result.outcome.status == "recovered"
    assert result.cleanup.status == "unknown"
    assert not result.cleanup.providers_contained
    assert result.cleanup.ended_ns - result.outcome.ended_ns == 2_000_000_000


def test_setup_timeout_and_cleanup_share_a_bound_without_claiming_rollback():
    result, *_ = coordinate(runtime=FakeRuntime(setup=300_000_000_000))
    assert result.outcome.status == "setup_failed"
    assert result.cleanup.status == "unknown"
    assert not result.cleanup.workers_stopped
    assert result.cleanup.ended_ns == 150_000_000_000


def test_run_aborts_after_failed_warmup_and_never_starts_measured_attempts():
    from tests.experiments import example

    class Unhealthy(Session):
        def probe(self):
            return (False, False)

    created = []

    def factory(scenario, trial_id):
        created.append(trial_id)
        return Unhealthy()

    runtime = FakeRuntime()
    run = TrialCoordinator(
        runtime, PhaseRecorder("test", clock=runtime.clock), TrialConfig()
    ).run(example.build_scenario(seed=0), factory, run_id="run")
    assert run.status == "aborted"
    assert created == ["warmup-1"]
    assert (run.requested_measured, run.started_measured, run.started_warmup) == (
        10,
        0,
        1,
    )
    assert run.counts("measured").requested == 10
    assert run.counts("measured").outcome_finalized == 0
    assert run.counts("warmup").cleanup_completed == 1


def test_agents_can_recognize_before_onset_and_act_before_disruption_probe_finishes():
    from mininet_ai.runtime.instrumentation import Operation, Phase, current_binding

    runtime = FakeRuntime(probe=500_000_000)

    class EarlyResponse(Session):
        def respond(self, gate, observation_id):
            binding = current_binding()
            assert binding is not None
            with Operation(Phase.DETECTOR):
                assert gate.invocation("a")
            self.responses.append(runtime.clock())

    result, _, session, _recorder = coordinate(EarlyResponse(), runtime)
    assert session.responses[0] <= result.outcome.onset_ns
    disruption = next(s for s in result.slots if s.kind == "probe" and s.job)
    assert disruption.job is not None
    assert session.responses[0] < disruption.job.ended_ns


def test_gate_rejects_actions_at_deadline_even_before_coordinator_wakes():
    runtime = FakeRuntime()
    gate = AdmissionGate(runtime.clock)
    gate.deadline(100)
    assert gate.invocation("a") and gate.policy("a")
    runtime.now = 100
    assert not gate.action("a", "late")
    assert not gate.invocation("b")


@pytest.mark.parametrize(
    "mismatch", [None, "missing", "extra", "wrong_target", "wrong_capability"]
)
def test_substrate_adapter_requires_exact_full_policy_and_one_pass(mismatch):
    from types import SimpleNamespace

    from mininet_ai.experiments.latency.session import SubstrateTrialSession
    from mininet_ai.sdk import ActionProposal, AgentResponse
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.experiments import example

    scenario = example.build_scenario(seed=7)
    switch = scenario.switches[0]
    agent_id = next(
        a.id for a in scenario.plan.agents if a.attachment.targets[0] == switch
    )
    proposals = [
        ActionProposal(
            id=str(i), capability="openflow.flow.install", target=switch, arguments=rule
        )
        for i, rule in enumerate(scenario.rules(switch, failed=True))
    ]
    if mismatch == "missing":
        proposals.pop()
    if mismatch == "extra":
        proposals.append(proposals[0].model_copy(update={"id": "extra"}))
    if mismatch in ("wrong_target", "wrong_capability"):
        field = "target" if mismatch == "wrong_target" else "capability"
        proposals[0] = proposals[0].model_copy(update={field: "link.enable"})

    class Agents:
        def __init__(self, *args, **kwargs):
            self.invocations = self.executions = 0

        def prepare(self, *args):
            self.invocations += 1
            return SimpleNamespace(
                context=SimpleNamespace(invocation_id="invoke"),
                response=AgentResponse(proposals=tuple(proposals)),
            )

        def execute(self, *args):
            self.executions += 1

        def reject(self, *args, **kwargs):
            pass

        def complete(self, *args):
            return "done"

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

    agents = Agents()
    session = SubstrateTrialSession(
        scenario, "trial", Substrate(), agents_factory=lambda *a, **kw: agents
    )
    session.setup()
    gate = AdmissionGate()
    # Public response uses every assigned agent; provide only one assigned agent here.
    from dataclasses import replace

    session.scenario = replace(
        scenario,
        plan=scenario.plan.model_copy(
            update={
                "agents": tuple(a for a in scenario.plan.agents if a.id == agent_id)
            }
        ),
    )
    session.respond(gate, "observation-1")
    session.respond(gate, "observation-2")
    assert not session.agent_errors
    assert agents.invocations == 1
    assert agents.executions == (4 if mismatch is None else 0)
    assert session.cleanup().verified


def test_a_single_late_round_cannot_fill_a_missed_slot_in_recovery_streak():
    class SlowSecondRound(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.probes = 0

        def start(self, name, fn):
            if name == "probe":
                self.probes += 1
                self.delays[name] = 1_500_000_000 if self.probes == 2 else 0
            return super().start(name, fn)

    result, *_ = coordinate(runtime=SlowSecondRound(), response_seconds=4.5)
    # Disruption t=2; late success t=4.5 cannot count, only t=5,6 remain timely.
    assert result.outcome.status == "timeout"


@pytest.mark.parametrize("warmup", [0, 2])
def test_measured_selection_is_independent_of_warmup_and_failed_attempts_consume_positions(
    warmup,
):
    from mininet_ai.experiments.scenarios import FailurePasses
    from tests.experiments import example

    scenario = example.build_scenario(seed=0)
    sessions = []

    class FailingSetup(Session):
        def setup(self):
            raise RuntimeError("setup failed")

    def factory(scenario, trial_id):
        session = Session() if trial_id.startswith("warmup") else FailingSetup()
        sessions.append(session)
        return session

    runtime = FakeRuntime()
    result = TrialCoordinator(
        runtime,
        PhaseRecorder("test", clock=runtime.clock),
        TrialConfig(measured=4, warmup=warmup),
    ).run(scenario, factory, run_id="run")
    assert result.status == "completed"
    assert len({id(s) for s in sessions}) == warmup + 4
    stream = FailurePasses(0, scenario.eligible_failures, kind="measured")
    expected = [scenario.select_failure(next(stream)).failed_link for _ in range(4)]
    measured = [a for a in result.attempts if a.kind == "measured"]
    assert [a.failed_link for a in measured] == expected
    assert all(a.execution.outcome.status == "setup_failed" for a in measured)


def test_run_aborts_on_unknown_rollback_without_starting_another_deployment():
    from mininet_ai.errors import RuntimeOperationError
    from tests.experiments import example

    calls = []

    def factory(*args):
        calls.append(args)
        raise RuntimeOperationError("rollback uncertain", code="runtime.deploy.failed")

    runtime = FakeRuntime()
    result = TrialCoordinator(
        runtime, PhaseRecorder("test", clock=runtime.clock), TrialConfig(warmup=0)
    ).run(example.build_scenario(seed=0), factory, run_id="run")
    assert result.status == "aborted"
    assert len(calls) == 1
    assert result.attempts[0].execution.outcome.status == "setup_failed"
    assert result.attempts[0].execution.cleanup.status == "unknown"


@pytest.mark.parametrize("cleanup_status", ["unknown", "failed"])
def test_cleanup_never_revises_a_recovered_trial(cleanup_status):
    class Unsafe(Session):
        def cleanup(self):
            if cleanup_status == "failed":
                return CleanupOutcome(
                    status=CleanupStatus.INCOMPLETE, remaining_resources=("s1",)
                )
            raise RuntimeError("teardown unverifiable")

    result, *_ = coordinate(Unsafe())
    assert result.outcome.status == "recovered"
    assert result.cleanup.status == cleanup_status


def test_coordinator_runtime_error_is_a_closed_error_outcome():
    class BrokenScheduler(FakeRuntime):
        def start(self, name, fn):
            if name == "sample":
                raise RuntimeError("scheduler unavailable")
            return super().start(name, fn)

    result, *_ = coordinate(runtime=BrokenScheduler())
    assert result.outcome.status == "error"
    assert result.cleanup.status == "verified"


def test_normal_provider_terminal_evidence_during_cleanup_does_not_revise_timeout():
    from mininet_ai.runtime.instrumentation import current_binding

    runtime = FakeRuntime(response=8_000_000_000)
    recorder = PhaseRecorder("test", clock=runtime.clock)
    context = ExecutionContext("run", "trial", "fault")

    class LateProvider(Session):
        def setup(self):
            self.request = recorder.requests.start(context, "ollama")

        def respond(self, gate, observation_id):
            assert current_binding() is not None
            assert gate.closed
            assert not gate.invocation("late-agent")
            recorder.requests.terminal(
                self.request, evidence="normal_response", source="adapter"
            )

        def probe(self):
            return (False, False) if self.injected else (True, True)

    result = TrialCoordinator(runtime, recorder, TrialConfig(response_seconds=3)).trial(
        LateProvider(), context
    )
    assert result.outcome.status == "timeout"
    assert result.outcome.ended_ns == 5_000_000_000
    assert result.cleanup.status == "verified"
    terminal = recorder.requests.requests[0].terminal
    assert terminal is not None and terminal.tick_ns == "10000000000"


def test_lost_required_evidence_prevents_injection_and_never_claims_cleanup_proof():
    runtime = FakeRuntime()

    def broken_sink(record):
        raise RuntimeError("collector lost evidence")

    recorder = PhaseRecorder("test", clock=runtime.clock, sink=broken_sink)
    session = Session()
    result = TrialCoordinator(runtime, recorder, TrialConfig()).trial(
        session, ExecutionContext("run", "trial", "fault")
    )
    assert result.outcome.status == "invalid"
    assert not session.injected
    assert result.cleanup.status == "unknown"


@pytest.mark.parametrize(
    "field,value",
    [
        ("measured", 0),
        ("warmup", -1),
        ("setup_seconds", 0),
        ("response_seconds", float("inf")),
        ("sample_seconds", float("nan")),
        ("probe_seconds", 0.5),
    ],
)
def test_configuration_rejects_out_of_contract_bounds(field, value):
    with pytest.raises(ValueError):
        TrialConfig(**{field: value})


def test_real_agent_runtime_keeps_experiment_and_deployment_identities_correlated():
    from copy import deepcopy
    from dataclasses import replace

    from mininet_ai.experiments.latency.session import SubstrateTrialSession
    from mininet_ai.runtime.instrumentation import (
        Phase,
        RecordingBinding,
        recording_scope,
    )
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.experiments import example

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

    scenario = example.build_scenario(seed=7)
    snapshot = deepcopy(scenario.plan.snapshot)
    snapshot["blueprints"][0]["implementation"]["entrypoint"] = (
        "tests.experiments.factories:create_recovery_agent"
    )
    scenario = replace(
        scenario, plan=scenario.plan.model_copy(update={"snapshot": snapshot})
    )
    session = SubstrateTrialSession(scenario, "trial", Substrate())
    recorder = PhaseRecorder("test", clock=lambda: 100)
    context = ExecutionContext("experiment-run", "trial", "fault")
    with recording_scope(RecordingBinding(recorder, context)):
        session.setup()
        session.respond(AdmissionGate(), "observation-1")
    assert not session.agent_errors
    assert len(session.agent_results) == 10
    assert all(result.status.value == "succeeded" for result in session.agent_results)
    recognized = [
        r
        for r in recorder.records
        if r.phase == Phase.DETECTOR and r.outcome == "relevant_failure_recognized"
    ]
    assert {r.context.agent_id for r in recognized} == {
        a.id for a in scenario.plan.agents
    }
    assert all(r.context.run_id == "experiment-run" for r in recorder.records)
    assert all(r.context.substrate_run_id == session.run_id for r in recognized)
    assert session.cleanup().verified


def test_readiness_observer_failure_is_invalid_not_operational_setup_failure():
    class BrokenReadiness(Session):
        def ready(self):
            raise RuntimeError("observer is broken")

    result, *_ = coordinate(BrokenReadiness())
    assert result.outcome.status == "invalid"


def test_probe_round_straddling_fault_onset_is_not_link_restoration():
    from mininet_ai.experiments.latency.trials import ProbeRound

    class Straddled(FakeRuntime):
        def start(self, name, fn):
            if name == "probe" and not any(j.name == "probe" for j in self.jobs):

                def straddle():
                    return ProbeRound(
                        (False, False),
                        (False, False),
                        (True, True),
                        (True, True),
                        self.now - 500_000_000,
                        self.now - 500_000_000,
                        self.now,
                        self.now,
                        self.now - 500_000_000,
                    )

                self.delays[name] = 500_000_000
                return super().start(name, straddle)
            if name == "probe":
                self.delays[name] = 0
            return super().start(name, fn)

    result, *_ = coordinate(runtime=Straddled(injection=200_000_000))
    assert result.outcome.status == "recovered"


def test_real_agent_runtime_and_public_substrate_seams_complete_a_bounded_trial():
    from copy import deepcopy
    from dataclasses import replace

    from mininet_ai.experiments.latency.session import SubstrateTrialSession
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.experiments import example

    class Substrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

        def __init__(self):
            super().__init__()
            self.down = False
            self.rules = 0

        def execute(self, run_id, request):
            result = super().execute(run_id, request)
            if request.name == "link.disable":
                self.down = True
                self.rules = 0
            elif request.name == "openflow.flow.install":
                self.rules += 1
            return result

        def observe(self, run_id, query):
            observed = super().observe(run_id, query)
            if query.name == "topology.resources":
                values = {
                    target: {
                        "state": "down" if self.down else "up",
                        "attributes": {"operState": "DOWN" if self.down else "UP"},
                    }
                    for target in query.targets
                }
            elif query.name == "host.reachability":
                source = query.targets[0]
                destination = query.parameters["destinations"][0]
                values = {
                    source: {
                        "probes": [
                            {
                                "source": source,
                                "destination": destination,
                                "reachable": not self.down or self.rules == 40,
                            }
                        ]
                    }
                }
            else:
                return observed
            return observed.model_copy(update={"values": values})

    scenario = example.build_scenario(seed=7)
    snapshot = deepcopy(scenario.plan.snapshot)
    snapshot["blueprints"][0]["implementation"]["entrypoint"] = (
        "tests.experiments.factories:create_recovery_agent"
    )
    scenario = replace(
        scenario, plan=scenario.plan.model_copy(update={"snapshot": snapshot})
    )
    session = SubstrateTrialSession(scenario, "trial", Substrate())
    result, _, _, recorder = coordinate(session, FakeRuntime(response=200_000_000))
    assert result.outcome.status == "recovered"
    assert result.cleanup.status == "verified"
    assert result.context.run_id == "run"
    assert result.clock_domain_id == recorder.clock_domain_id
    assert len(session.agent_results) == 10
    assert not session.agent_errors


def test_response_budget_starts_on_fresh_observation_not_command_submission():
    result, *_ = coordinate(runtime=FakeRuntime(injection=3_000_000_000))
    assert result.outcome.injection_ns == 2_000_000_000
    assert result.outcome.onset_ns == 5_000_000_000
    assert result.outcome.response_deadline_ns == 125_000_000_000


def test_verified_public_deployment_rollback_permits_the_next_measured_attempt():
    from mininet_ai.errors import RuntimeOperationError
    from mininet_ai.experiments.latency.session import SubstrateTrialSession
    from mininet_ai.substrates import FakeSubstrateRuntime
    from tests.experiments import example

    class RolledBack(FakeSubstrateRuntime):
        def deploy(self, plan):
            raise RuntimeOperationError(
                "deploy failed; rollback verified",
                code="runtime.deploy.failed",
                cleanup=CleanupOutcome(
                    status=CleanupStatus.VERIFIED,
                    evidence=("owned resources absent",),
                    evidence_gaps=(),
                ),
            )

    runtime = FakeRuntime()
    run = TrialCoordinator(
        runtime,
        PhaseRecorder("test", clock=runtime.clock),
        TrialConfig(warmup=0, measured=2),
    ).run(
        example.build_scenario(seed=0),
        lambda scenario, trial_id: SubstrateTrialSession(
            scenario, trial_id, RolledBack()
        ),
        run_id="run",
    )
    assert run.status == "completed"
    assert run.counts("measured").cleanup_completed == 2
    assert all(a.execution.outcome.status == "setup_failed" for a in run.attempts)


@pytest.mark.parametrize(
    "response_seconds,expected", [(3, "timeout"), (3.1, "recovered")]
)
def test_third_round_must_complete_strictly_before_response_deadline(
    response_seconds, expected
):
    result, *_ = coordinate(response_seconds=response_seconds)
    assert result.outcome.status == expected
    assert result.outcome.first_success_ns == 3_000_000_000
    assert result.outcome.recovery_ns == (
        5_000_000_000 if expected == "recovered" else None
    )


def test_all_pending_workers_share_one_cleanup_budget_and_block_next_trial():
    from tests.experiments import example

    runtime = FakeRuntime(response=100_000_000_000)
    created = []

    def factory(*args):
        created.append(args)
        return Session()

    run = TrialCoordinator(
        runtime,
        PhaseRecorder("test", clock=runtime.clock),
        TrialConfig(warmup=0, measured=2, cleanup_seconds=3),
    ).run(example.build_scenario(seed=0), factory, run_id="run")
    assert len(created) == 1
    assert run.status == "aborted"
    attempt = run.attempts[0].execution
    assert attempt.outcome.status == "recovered"
    assert attempt.cleanup.status == "unknown"
    assert not attempt.cleanup.workers_stopped
    assert attempt.cleanup.ended_ns - attempt.outcome.ended_ns == 3_000_000_000
    assert run.counts("measured").cleanup_completed == 0


def test_response_deadline_is_published_before_early_agent_admission():
    runtime = FakeRuntime()
    admissions = []

    class Early(Session):
        def respond(self, gate, observation_id):
            runtime.now += 200_000_000
            admissions.append(gate.invocation("a"))

    result, *_ = coordinate(Early(), runtime, response_seconds=0.1)
    assert admissions == [False]
    assert result.outcome.status == "timeout"


def test_short_response_budget_does_not_shrink_injection_disruption_window():
    class Undisrupted(Session):
        def probe(self):
            return (True, True)

    result, *_ = coordinate(Undisrupted(), response_seconds=2, injection_seconds=10)
    assert result.outcome.status == "no_observed_disruption"
    assert result.outcome.response_deadline_ns == 4_000_000_000
    assert result.outcome.ended_ns == 12_000_000_000
