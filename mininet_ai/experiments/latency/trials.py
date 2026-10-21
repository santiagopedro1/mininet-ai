"""Bounded ADR-0001/0004 trial orchestration (persistence is a separate seam)."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import copy_context
from dataclasses import dataclass
from math import isfinite
from threading import Event, RLock, Thread
from time import monotonic_ns, sleep
from typing import Any, Literal, Protocol
from uuid import uuid4

from mininet_ai.errors import describe_failure
from mininet_ai.experiments.scenarios import FailurePasses
from mininet_ai.outcomes import CleanupOutcome
from mininet_ai.runtime.instrumentation import (
    ExecutionContext,
    Operation,
    Phase,
    PhaseRecorder,
    RecordingBinding,
    recording_scope,
)

TrialStatus = Literal[
    "recovered",
    "timeout",
    "invalid",
    "setup_failed",
    "injection_failed",
    "no_observed_disruption",
    "error",
]


@dataclass(frozen=True)
class TrialConfig:
    measured: int = 10
    warmup: int = 1
    setup_seconds: float = 120
    injection_seconds: float = 10
    response_seconds: float = 120
    cleanup_seconds: float = 30
    sample_seconds: float = 0.1
    probe_seconds: float = 1

    def __post_init__(self):
        if self.probe_seconds != 1:
            raise ValueError("designated probe slots are fixed at one second")
        if type(self.measured) is not int or self.measured < 1:
            raise ValueError("measured attempts must be a positive integer")
        if type(self.warmup) is not int or self.warmup < 0:
            raise ValueError("warm-up attempts must be a nonnegative integer")
        for value in (
            self.setup_seconds,
            self.injection_seconds,
            self.response_seconds,
            self.cleanup_seconds,
            self.sample_seconds,
            self.probe_seconds,
        ):
            if not isfinite(value) or value <= 0:
                raise ValueError("budgets and periods must be finite and positive")


class AdmissionGate:
    """One lock linearizes trial closure and every invocation/action admission.

    Admission is not execution. Already admitted work is contained during cleanup.
    Policy acceptance is one pass; individual rule admissions remain distinguishable.
    """

    def __init__(self, clock: Callable[[], int] | None = None):
        self._lock = RLock()
        self._closed = False
        self._invocations: set[str] = set()
        self._policies: set[str] = set()
        self._actions: set[tuple[str, str]] = set()
        self._clock = clock
        self._deadline: int | None = None

    def deadline(self, tick: int) -> None:
        with self._lock:
            self._deadline = tick

    def _expired(self) -> bool:
        if (
            self._clock is not None
            and self._deadline is not None
            and self._clock() >= self._deadline
        ):
            self._closed = True
        return self._closed

    def invocation(self, agent: str) -> bool:
        with self._lock:
            if self._expired() or agent in self._invocations:
                return False
            self._invocations.add(agent)
            return True

    def policy(self, agent: str) -> bool:
        with self._lock:
            if (
                self._expired()
                or agent not in self._invocations
                or agent in self._policies
            ):
                return False
            self._policies.add(agent)
            return True

    def action(self, agent: str, proposal: str) -> bool:
        with self._lock:
            key = (agent, proposal)
            if self._expired() or agent not in self._policies or key in self._actions:
                return False
            self._actions.add(key)
            return True

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._expired()


class Job(Protocol):
    name: str
    started_ns: int | None
    ended_ns: int | None
    value: Any
    error: Exception | None

    @property
    def done(self) -> bool: ...


class Runtime(Protocol):
    def clock(self) -> int: ...
    def start(self, name: str, fn: Callable[[], Any]) -> Job: ...
    def wait_until(self, tick: int) -> None: ...


class ThreadJob:
    def __init__(self, name: str, fn: Callable[[], Any], clock: Callable[[], int]):
        self.name = name
        self.started_ns: int | None = None
        self.ended_ns: int | None = None
        self.value: Any = None
        self.error: Exception | None = None
        self._done = Event()
        context = copy_context()

        def run():
            self.started_ns = clock()
            try:
                self.value = context.run(fn)
            except Exception as error:
                self.error = error
            finally:
                self.ended_ns = clock()
                self._done.set()

        # Never use executor.__exit__: it can wait without a bound for a Provider.
        Thread(target=run, daemon=True, name="latency-operation").start()

    @property
    def done(self) -> bool:
        return self._done.is_set()


class ThreadRuntime:
    def clock(self) -> int:
        return monotonic_ns()

    def start(self, name: str, fn: Callable[[], Any]) -> Job:
        return ThreadJob(name, fn, self.clock)

    def wait_until(self, tick: int) -> None:
        sleep(max(0, min((tick - self.clock()) / 1e9, 0.001)))


class TrialSession(Protocol):
    """Fresh attempt resources. Cleanup uses the shared Substrate cleanup evidence.

    Observation methods raise on broken/missing observers, return False only for
    valid negative evidence. ``respond`` must use the supplied gate for each
    invocation, policy pass and action; it must not retry Provider calls.
    """

    def setup(self) -> None: ...
    def ready(self) -> bool: ...
    def inject(self) -> None: ...
    def endpoints_down(self) -> tuple[bool, bool]: ...
    def probe(self) -> tuple[bool, bool]: ...
    def respond(self, gate: AdmissionGate, observation_id: str) -> None: ...
    def cleanup(self) -> CleanupOutcome: ...


@dataclass(frozen=True)
class TrialOutcome:
    status: TrialStatus
    reason: str
    started_ns: int
    ended_ns: int
    injection_ns: int | None = None
    onset_ns: int | None = None
    response_deadline_ns: int | None = None
    first_success_ns: int | None = None
    recovery_ns: int | None = None
    stage: Literal["setup", "injection", "response"] = "setup"


@dataclass(frozen=True)
class Slot:
    kind: str
    scheduled_ns: int
    job: Job | None
    observation_id: str


@dataclass(frozen=True)
class FaultObservation:
    endpoints: tuple[bool, bool]
    completed_ns: int


@dataclass(frozen=True)
class ProbeRound:
    before: tuple[bool, bool]
    probes: tuple[bool, bool]
    after: tuple[bool, bool]
    expected: tuple[bool, bool]
    before_ns: int
    probes_started_ns: int
    probes_ended_ns: int
    after_ns: int
    started_ns: int

    @property
    def all_success(self) -> bool:
        return self.probes == (True, True)

    @property
    def endpoints_match(self) -> bool:
        return self.before == self.after == self.expected

    def restored_after(self, onset: int) -> bool:
        return (self.before != self.expected and self.started_ns >= onset) or (
            self.after != self.expected and self.probes_ended_ns >= onset
        )


@dataclass
class FixedSlots:
    """One non-overlapping collection stream; missed slots are never backfilled."""

    kind: str
    next_ns: int
    period_ns: int
    collect: Callable[[str], Any]
    job: Job | None = None
    slot: Slot | None = None

    def admit(
        self,
        now: int,
        launch: Callable[[str, Callable[[], Any], str], Job],
        slots: list[Slot],
    ) -> bool:
        missed = False
        while self.next_ns <= now:
            ident = str(uuid4())
            if self.job is not None or self.next_ns + self.period_ns <= now:
                slots.append(Slot(self.kind, self.next_ns, None, ident))
                missed = True
            else:
                self.job = launch(
                    self.kind, lambda ident=ident: self.collect(ident), ident
                )
                self.slot = Slot(self.kind, self.next_ns, self.job, ident)
                slots.append(self.slot)
            self.next_ns += self.period_ns
        return missed


@dataclass(frozen=True)
class TrialCleanup:
    status: Literal["verified", "failed", "unknown"]
    network: CleanupOutcome
    workers_stopped: bool
    providers_contained: bool
    ended_ns: int
    deadline_ns: int


@dataclass(frozen=True)
class TrialExecution:
    outcome: TrialOutcome
    cleanup: TrialCleanup
    slots: tuple[Slot, ...]
    jobs: tuple[Job, ...]
    context: ExecutionContext
    clock_domain_id: str


@dataclass(frozen=True)
class RunAttempt:
    kind: Literal["warmup", "measured"]
    ordinal: int
    trial_id: str
    failed_link: str
    execution: TrialExecution


@dataclass(frozen=True)
class TrialRun:
    status: Literal["completed", "aborted"]
    reason: str | None
    requested_measured: int
    requested_warmup: int
    attempts: tuple[RunAttempt, ...]

    @property
    def started_measured(self) -> int:
        return sum(a.kind == "measured" for a in self.attempts)

    @property
    def started_warmup(self) -> int:
        return sum(a.kind == "warmup" for a in self.attempts)

    def counts(self, kind: Literal["warmup", "measured"]) -> AttemptCounts:
        attempts = [a for a in self.attempts if a.kind == kind]
        cleanup_completed = sum(
            any(
                j.name == "cleanup"
                and j.done
                and j.ended_ns is not None
                and j.ended_ns < a.execution.cleanup.deadline_ns
                for j in a.execution.jobs
            )
            for a in attempts
        )
        return AttemptCounts(
            self.requested_warmup if kind == "warmup" else self.requested_measured,
            len(attempts),
            len(attempts),
            cleanup_completed,
        )


@dataclass(frozen=True)
class AttemptCounts:
    requested: int
    started: int
    outcome_finalized: int
    cleanup_completed: int


class DeferredSession:
    """Construct resources inside the setup budget, including factory failures."""

    def __init__(self, factory: Callable[[], TrialSession]):
        self.factory = factory
        self.session: TrialSession | None = None
        self.failure: Exception | None = None

    def setup(self):
        try:
            self.session = self.factory()
            self.session.setup()
        except Exception as error:
            self.failure = error
            raise

    def ready(self):
        assert self.session is not None
        return self.session.ready()

    def inject(self):
        assert self.session is not None
        return self.session.inject()

    def endpoints_down(self):
        assert self.session is not None
        return self.session.endpoints_down()

    def probe(self):
        assert self.session is not None
        return self.session.probe()

    def respond(self, gate, observation_id):
        assert self.session is not None
        return self.session.respond(gate, observation_id)

    def cleanup(self):
        if self.session is not None:
            return self.session.cleanup()
        return (
            describe_failure(self.failure).cleanup if self.failure else CleanupOutcome()
        )


def ns(seconds: float) -> int:
    return max(1, int(seconds * 1_000_000_000))


class TrialCoordinator:
    def __init__(self, runtime: Runtime, recorder: PhaseRecorder, config: TrialConfig):
        self.runtime, self.recorder, self.config = runtime, recorder, config

    def run(
        self, scenario: Any, factory: Callable[[Any, str], TrialSession], *, run_id: str
    ) -> TrialRun:
        attempts: list[RunAttempt] = []
        populations: tuple[tuple[Literal["warmup", "measured"], int], ...] = (
            ("warmup", self.config.warmup),
            ("measured", self.config.measured),
        )
        for kind, count in populations:
            failures = FailurePasses(
                scenario.seed, scenario.eligible_failures, kind=kind
            )
            for ordinal in range(1, count + 1):
                selected = scenario.select_failure(next(failures))
                trial_id = f"{kind}-{ordinal}"
                context = ExecutionContext(
                    run_id, trial_id, f"{trial_id}:{selected.failed_link}"
                )
                execution = self.trial(
                    DeferredSession(
                        lambda selected=selected, trial_id=trial_id: factory(
                            selected, trial_id
                        )
                    ),
                    context,
                )
                attempts.append(
                    RunAttempt(kind, ordinal, trial_id, selected.failed_link, execution)
                )
                reason = None
                if execution.cleanup.status != "verified":
                    reason = "prior attempt containment/cleanup unverified"
                elif kind == "warmup" and execution.outcome.status != "recovered":
                    reason = "warm-up unsuccessful"
                if reason:
                    return TrialRun(
                        "aborted",
                        reason,
                        self.config.measured,
                        self.config.warmup,
                        tuple(attempts),
                    )
        return TrialRun(
            "completed", None, self.config.measured, self.config.warmup, tuple(attempts)
        )

    def trial(self, session: TrialSession, context: ExecutionContext) -> TrialExecution:
        runtime, config = self.runtime, self.config
        gate = AdmissionGate(runtime.clock)
        jobs: list[Job] = []
        slots: list[Slot] = []
        binding = RecordingBinding(self.recorder, context)
        started = runtime.clock()
        injection = onset = response_deadline = first_success = recovery = None
        status: TrialStatus = "error"
        reason = "unexpected coordinator failure"

        def launch(
            name: str, phase: Phase | None, fn: Callable[[], Any], **identities: str
        ) -> Job:
            def recorded():
                with recording_scope(binding, **identities):
                    if phase is None:
                        return fn()
                    with Operation(phase):
                        return fn()

            job = runtime.start(name, recorded)
            jobs.append(job)
            return job

        def await_job(job: Job, deadline: int) -> bool:
            while not job.done and runtime.clock() < deadline:
                runtime.wait_until(deadline)
            return job.done and job.ended_ns is not None and job.ended_ns < deadline

        def round_probe(baseline=False):
            round_started = runtime.clock()
            before = session.endpoints_down()
            before_ns = runtime.clock()
            probes_started = runtime.clock()
            probes = session.probe()
            probes_ended = runtime.clock()
            after = session.endpoints_down()
            after_ns = runtime.clock()
            expected = (False, False) if baseline else (True, True)
            return ProbeRound(
                before,
                probes,
                after,
                expected,
                before_ns,
                probes_started,
                probes_ended,
                after_ns,
                round_started,
            )

        try:
            setup_deadline = started + ns(config.setup_seconds)
            setup = launch("setup", Phase.SETUP, session.setup)
            if not await_job(setup, setup_deadline):
                status, reason = "setup_failed", "setup deadline"
            elif setup.error:
                status, reason = "setup_failed", str(setup.error)
            else:
                ready = launch("ready", Phase.OBSERVATION, session.ready)
                if not await_job(ready, setup_deadline):
                    status, reason = "setup_failed", "readiness deadline"
                elif ready.error:
                    status, reason = "invalid", "readiness observer failed"
                elif ready.value is not True:
                    status, reason = "setup_failed", "observation path not ready"
                else:
                    status, reason = "invalid", "unhealthy baseline"
                    baseline_ok = False
                    baseline_streak = 0
                    next_probe = runtime.clock()
                    while runtime.clock() < setup_deadline:
                        while runtime.clock() < next_probe:
                            runtime.wait_until(min(next_probe, setup_deadline))
                            if runtime.clock() >= setup_deadline:
                                break
                        if runtime.clock() >= setup_deadline:
                            baseline_ok = False
                            break
                        ident = str(uuid4())
                        job = launch(
                            "baseline",
                            Phase.OBSERVATION,
                            lambda: round_probe(True),
                            observation_id=ident,
                        )
                        slots.append(Slot("baseline", next_probe, job, ident))
                        if not await_job(job, setup_deadline) or job.error:
                            baseline_ok = False
                            break
                        round: ProbeRound = job.value
                        if not round.endpoints_match or not round.all_success:
                            baseline_ok = False
                            break
                        next_probe += ns(config.probe_seconds)
                        if runtime.clock() >= next_probe:
                            baseline_streak = 0
                            while next_probe <= runtime.clock():
                                slots.append(
                                    Slot("baseline", next_probe, None, str(uuid4()))
                                )
                                next_probe += ns(config.probe_seconds)
                        else:
                            baseline_streak += 1
                        if baseline_streak == 3:
                            baseline_ok = True
                            break
                    baseline_ok = baseline_ok and self.recorder.trustworthy(context)
                    if baseline_ok:
                        injection = runtime.clock()
                        inject_deadline = injection + ns(config.injection_seconds)
                        inject_job = launch(
                            "injection", Phase.INJECTION, session.inject
                        )
                        responded = disrupted = False
                        recognition_lock = RLock()

                        def sample(ident: str):
                            nonlocal responded
                            endpoints = session.endpoints_down()
                            completed = runtime.clock()
                            if (
                                endpoints == (True, True)
                                and completed < inject_deadline
                            ):
                                with recognition_lock:
                                    if not responded and not gate.closed:
                                        responded = True
                                        # Response workers may run before sample delivery.
                                        # Publish the authoritative limit before admission.
                                        gate.deadline(
                                            completed + ns(config.response_seconds)
                                        )
                                        with Operation(Phase.DETECTOR) as recognition:
                                            recognition.finish(
                                                "relevant_failure_recognized"
                                            )
                                        launch(
                                            "response",
                                            None,
                                            lambda: session.respond(gate, ident),
                                            observation_id=ident,
                                            observation_source="global_harness",
                                        )
                            return FaultObservation(endpoints, completed)

                        sampler = FixedSlots(
                            "sample", injection, ns(config.sample_seconds), sample
                        )
                        probe_stream = FixedSlots(
                            "probe",
                            injection,
                            ns(config.probe_seconds),
                            lambda _: round_probe(),
                        )

                        def launch_observation(
                            kind: str, collect: Callable[[], Any], ident: str
                        ):
                            return launch(
                                kind,
                                Phase.OBSERVATION,
                                collect,
                                observation_id=ident,
                                observation_source="global_harness",
                            )

                        streak = 0
                        response_closed = False

                        def evidence_deadline():
                            return (
                                inject_deadline
                                if not disrupted or response_deadline is None
                                else response_deadline
                            )

                        status, reason = "injection_failed", "link-down not confirmed"
                        while True:
                            now = runtime.clock()
                            if not self.recorder.trustworthy(context):
                                status, reason = "invalid", "required evidence lost"
                                break
                            if sampler.job is not None and sampler.job.done:
                                assert sampler.job.ended_ns is not None
                                if (
                                    sampler.job.error
                                    and sampler.job.ended_ns < evidence_deadline()
                                ):
                                    status, reason = "invalid", "fault observer failed"
                                    break
                                if sampler.job.error:
                                    sampler.job = None
                                    continue
                                observation: FaultObservation = sampler.job.value
                                timely_sample = (
                                    observation.completed_ns < evidence_deadline()
                                )
                                if (
                                    observation.endpoints == (True, True)
                                    and timely_sample
                                ):
                                    if onset is None:
                                        onset = observation.completed_ns
                                        response_deadline = onset + ns(
                                            config.response_seconds
                                        )
                                        gate.deadline(response_deadline)
                                elif onset is not None and timely_sample:
                                    status, reason = "invalid", "failed link restored"
                                    break
                                sampler.job = None
                            if probe_stream.job is not None and probe_stream.job.done:
                                assert probe_stream.job.ended_ns is not None
                                if (
                                    probe_stream.job.error
                                    and probe_stream.job.ended_ns < evidence_deadline()
                                ):
                                    status, reason = "invalid", "probe observer failed"
                                    break
                                end = probe_stream.job.ended_ns
                                assert end is not None
                                if probe_stream.job.error:
                                    probe_stream.job = None
                                    continue
                                round = probe_stream.job.value
                                timely_probe = end < evidence_deadline()
                                if (
                                    onset is not None
                                    and timely_probe
                                    and round.restored_after(onset)
                                ):
                                    status, reason = "invalid", "failed link restored"
                                    break
                                if end < inject_deadline and not round.all_success:
                                    disrupted = True
                                assert probe_stream.slot is not None
                                timely = (
                                    response_deadline is not None
                                    and end < response_deadline
                                    and end
                                    < probe_stream.slot.scheduled_ns
                                    + ns(config.probe_seconds)
                                    and onset is not None
                                    and round.started_ns >= onset
                                )
                                if (
                                    disrupted
                                    and round.all_success
                                    and round.endpoints_match
                                    and timely
                                ):
                                    streak += 1
                                    if first_success is None:
                                        first_success = end
                                    if streak == 3:
                                        recovery = end
                                        status, reason = (
                                            "recovered",
                                            "three confirmed recovery rounds",
                                        )
                                        break
                                else:
                                    streak = 0
                                probe_stream.job = None
                            if inject_job.done and inject_job.error:
                                status, reason = (
                                    "injection_failed",
                                    str(inject_job.error),
                                )
                                break
                            if (
                                response_deadline is not None
                                and now >= response_deadline
                            ):
                                if not response_closed:
                                    gate.close()
                                    response_closed = True
                                    with (
                                        recording_scope(binding),
                                        Operation(Phase.STOPPING) as closure,
                                    ):
                                        closure.finish("response_admission_closed")
                                if disrupted:
                                    status, reason = "timeout", "response deadline"
                                    break
                                # Injection confirmation retains its own window even
                                # after response admission closes. Without disruption
                                # the population is no_observed_disruption, not timeout.
                            if now >= inject_deadline and (
                                onset is None or not disrupted
                            ):
                                status = (
                                    "injection_failed"
                                    if onset is None
                                    else "no_observed_disruption"
                                )
                                reason = "injection confirmation deadline"
                                break
                            sampler.admit(now, launch_observation, slots)
                            if probe_stream.admit(now, launch_observation, slots):
                                streak = 0
                            pending_deadline = (
                                inject_deadline
                                if response_closed
                                else min(
                                    inject_deadline,
                                    response_deadline or inject_deadline,
                                )
                                if not disrupted or onset is None
                                else response_deadline
                            )
                            if any(
                                j is not None and j.done
                                for j in (sampler.job, probe_stream.job)
                            ):
                                continue
                            assert pending_deadline is not None
                            runtime.wait_until(
                                min(
                                    sampler.next_ns,
                                    probe_stream.next_ns,
                                    pending_deadline,
                                )
                            )
        except Exception as error:
            status, reason = "error", str(error)
        if status == "recovered" and not self.recorder.trustworthy(context):
            status, reason, recovery = "invalid", "required evidence lost", None
        # Linearize closure before stopping. The immutable outcome never consults jobs again.
        gate.close()
        ended = runtime.clock()
        outcome = TrialOutcome(
            status,
            reason,
            started,
            ended,
            injection,
            onset,
            response_deadline,
            first_success,
            recovery,
            "setup"
            if injection is None
            else (
                "injection"
                if onset is None or status == "no_observed_disruption"
                else "response"
            ),
        )
        with recording_scope(binding), Operation(Phase.STOPPING) as stopping:
            stopping.finish("admission_closed")
        cleanup_deadline = ended + ns(config.cleanup_seconds)
        while not all(job.done for job in jobs) and runtime.clock() < cleanup_deadline:
            runtime.wait_until(cleanup_deadline)
        workers_stopped = all(job.done for job in jobs)
        network = CleanupOutcome()
        if workers_stopped and runtime.clock() < cleanup_deadline:
            cleanup_job = launch("cleanup", Phase.CLEANUP, session.cleanup)
            if await_job(cleanup_job, cleanup_deadline) and cleanup_job.error is None:
                network = cleanup_job.value
            workers_stopped = all(job.done for job in jobs)
        contained = self.recorder.requests.contained
        while not contained and runtime.clock() < cleanup_deadline:
            runtime.wait_until(cleanup_deadline)
            contained = self.recorder.requests.contained
        cleanup_status = (
            "verified"
            if network.verified and workers_stopped and contained
            else "unknown"
        )
        if network.remaining_resources:
            cleanup_status = "failed"
        cleanup = TrialCleanup(
            cleanup_status,
            network,
            workers_stopped,
            contained,
            runtime.clock(),
            cleanup_deadline,
        )
        return TrialExecution(
            outcome,
            cleanup,
            tuple(slots),
            tuple(jobs),
            context,
            self.recorder.clock_domain_id,
        )
