"""Bounded ADR-0001/0004 trial orchestration (persistence is a separate seam)."""

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
    def __init__(self, fn: Callable[[], Any], clock: Callable[[], int]):
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
        return ThreadJob(fn, self.clock)

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


@dataclass(frozen=True)
class TrialCleanup:
    status: Literal["verified", "failed", "unknown"]
    network: CleanupOutcome
    workers_stopped: bool
    providers_contained: bool
    ended_ns: int


@dataclass(frozen=True)
class TrialExecution:
    outcome: TrialOutcome
    cleanup: TrialCleanup
    slots: tuple[Slot, ...]
    jobs: tuple[Job, ...]


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
            name: str, phase: Phase, fn: Callable[[], Any], **identities: str
        ) -> Job:
            def recorded():
                with recording_scope(binding, **identities), Operation(phase):
                    return fn()

            job = runtime.start(name, recorded)
            jobs.append(job)
            return job

        def await_job(job: Job, deadline: int) -> bool:
            while not job.done and runtime.clock() < deadline:
                runtime.wait_until(deadline)
            return job.done and job.ended_ns is not None and job.ended_ns < deadline

        def round_probe(baseline=False):
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
                elif ready.error or ready.value is not True:
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
                        before, probes, after, expected = (
                            round.before,
                            round.probes,
                            round.after,
                            round.expected,
                        )
                        if (
                            before != expected
                            or after != expected
                            or probes != (True, True)
                        ):
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
                        gate.deadline(inject_deadline)
                        inject_job = launch(
                            "injection", Phase.INJECTION, session.inject
                        )
                        next_sample = next_probe = injection
                        sample_job = probe_job = None
                        probe_slot = None
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
                                        launch(
                                            "response",
                                            Phase.DETECTOR,
                                            lambda: session.respond(gate, ident),
                                            observation_id=ident,
                                            observation_source="global_harness",
                                        )
                            return FaultObservation(endpoints, completed)

                        streak = 0
                        status, reason = "injection_failed", "link-down not confirmed"
                        while True:
                            now = runtime.clock()
                            if not self.recorder.trustworthy(context):
                                status, reason = "invalid", "required evidence lost"
                                break
                            if sample_job is not None and sample_job.done:
                                assert sample_job.ended_ns is not None
                                if sample_job.error and sample_job.ended_ns < (
                                    response_deadline or inject_deadline
                                ):
                                    status, reason = "invalid", "fault observer failed"
                                    break
                                observation: FaultObservation = (
                                    sample_job.value
                                    or FaultObservation(
                                        (False, False), sample_job.ended_ns
                                    )
                                )
                                timely_sample = observation.completed_ns < (
                                    response_deadline or inject_deadline
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
                                sample_job = None
                            if probe_job is not None and probe_job.done:
                                assert probe_job.ended_ns is not None
                                if probe_job.error and probe_job.ended_ns < (
                                    response_deadline or inject_deadline
                                ):
                                    status, reason = "invalid", "probe observer failed"
                                    break
                                end = probe_job.ended_ns
                                assert end is not None
                                if probe_job.error:
                                    probe_job = None
                                    continue
                                round = probe_job.value
                                before, probes, after, expected = (
                                    round.before,
                                    round.probes,
                                    round.after,
                                    round.expected,
                                )
                                timely_probe = (
                                    response_deadline is None or end < response_deadline
                                )
                                if (
                                    onset is not None
                                    and timely_probe
                                    and (before != expected or after != expected)
                                ):
                                    status, reason = "invalid", "failed link restored"
                                    break
                                if end < inject_deadline and probes != (True, True):
                                    disrupted = True
                                assert probe_slot is not None
                                timely = (
                                    response_deadline is not None
                                    and end < response_deadline
                                    and end
                                    < probe_slot.scheduled_ns + ns(config.probe_seconds)
                                )
                                if (
                                    disrupted
                                    and probes == (True, True)
                                    and before == after == expected
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
                                probe_job = None
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
                                status, reason = "timeout", "response deadline"
                                break
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
                            # Independent fixed slots: never overlap a collection or backfill.
                            for kind, period in (
                                ("sample", ns(config.sample_seconds)),
                                ("probe", ns(config.probe_seconds)),
                            ):
                                due = next_sample if kind == "sample" else next_probe
                                active = sample_job if kind == "sample" else probe_job
                                while due <= now:
                                    ident = str(uuid4())
                                    if active is not None or due + period <= now:
                                        slots.append(Slot(kind, due, None, ident))
                                        if kind == "probe":
                                            streak = 0
                                    else:
                                        active = (
                                            launch(
                                                kind,
                                                Phase.OBSERVATION,
                                                lambda ident=ident: sample(ident),
                                                observation_id=ident,
                                                observation_source="global_harness",
                                            )
                                            if kind == "sample"
                                            else launch(
                                                kind,
                                                Phase.OBSERVATION,
                                                round_probe,
                                                observation_id=ident,
                                                observation_source="global_harness",
                                            )
                                        )
                                        slot = Slot(kind, due, active, ident)
                                        slots.append(slot)
                                        if kind == "sample":
                                            sample_job = active
                                        else:
                                            probe_job, probe_slot = active, slot
                                    due += period
                                if kind == "sample":
                                    next_sample = due
                                else:
                                    next_probe = due
                            pending_deadline = (
                                min(
                                    inject_deadline,
                                    response_deadline or inject_deadline,
                                )
                                if not disrupted or onset is None
                                else response_deadline
                            )
                            if any(
                                j is not None and j.done
                                for j in (sample_job, probe_job)
                            ):
                                continue
                            assert pending_deadline is not None
                            runtime.wait_until(
                                min(next_sample, next_probe, pending_deadline)
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
            cleanup_status, network, workers_stopped, contained, runtime.clock()
        )
        return TrialExecution(outcome, cleanup, tuple(slots), tuple(jobs))
