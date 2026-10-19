# Opt-in latency evidence

`mininet_ai.runtime.instrumentation` implements the runtime recording seam for
ADRs 0002/0005/0006. It does not implement trial scheduling, recovery confirmation,
cleanup orchestration, artifact writing, or summary populations.

```python
from mininet_ai.runtime.instrumentation import (
    ExecutionContext, PhaseRecorder, RecordingBinding, recording_scope,
)

recorder = PhaseRecorder("experiment-process-1", clock=experiment_monotonic_ns)
context = ExecutionContext(run_id, trial_id, fault_id)
with recording_scope(RecordingBinding(recorder, context)):
    # Construct per-trial telemetry and continuous runtimes in this scope.
    # Direct OneShotAgentRuntime.prepare/invoke calls inherit it as well.
    result = agents.invoke(run_id, agent_id, intent)
```

The injected clock returns nonnegative integer nanoseconds. Use the same recorder
and domain in the coordinator and local workers. `Stamp.tick_ns` retains decimal
strings, including a real `"0"`; clock failures retain a missing tick and explicit
loss. UTC provenance and SDK/remote metrics are separate from this clock.

## Context and transitions

Bindings contain a recorder and frozen execution identities. Telemetry captures
its binding at construction, attaches context to in-process runtime events, and
stamps detector acceptance before payload construction or publication. Event buses
also capture an explicit publisher scope when an event has no context. This local
field is excluded from existing runtime-event wire serialization: replay consumers
must restore experiment context explicitly, not infer a fault from receipt time.

Continuous queues use event context; they never assign contextless events to a
worker's current trial. Coordinator executor submissions copy the execution
context. Prepared invocations retain their binding for later action workers,
including when a different trial scope is active on that worker. A trial should
construct its own telemetry pipeline, not mutate a shared current-fault pointer.

Operations retain distinct admission, scheduling, context, reasoning,
decision-to-action, action-attempt, capability execution, and verification phases.
Each action has its invocation-scoped proposal identity. Actual network execution
starts at the capability provider call, not at an audit event. The example's exact
supplied-policy gate records `policy_accepted`/`policy_rejected` admission outcomes;
rejected proposals do not call the action-attempt seam. Ordinary capability
admission is not evidence of exact experiment-policy acceptance.

Transitions use the ADR-0006 vocabulary: `operation_started`,
`operation_completed`, `operation_failed`, `operation_cancelled`. A start without
a terminal transition remains incomplete at finalization; no terminal event is
invented. Normalized failures and unsatisfied verification are recorded as failed
operations even when the runtime returns rather than raises. Collector sequence
is delivery order, not timestamp order. A synchronous optional sink receives the
already-stamped phase record; sink overhead can perturb the measured runtime.

`recorder.measurement(context, start, end)` checks integrity, domain, ticks, and
ordering without clamping or substituting SDK durations. Use the explicit
`reversed_reason` for recognition-before-onset or action-after-recovery. These
intervals are unavailable while their original stamps remain intact. Failed
operations can still have available elapsed durations. Lateness and trial outcomes
remain orchestration responsibilities, independent of operation outcome.

## Loss and Provider containment

`records`, `losses`, and `requests.requests` are thread-safe immutable snapshots.
`requests.history` additionally retains each request-state transition, including
repeated cancellations and local interruptions, rather than overwriting evidence.
Sink failures and malformed/failing clocks are sticky losses. An external collector
can report dropped evidence with `report_loss`. Measurements for the affected trial
then become unavailable through `measurement`; orchestration **must** inspect
`trustworthy(context)` and invalidate a trial if required observation evidence was
lost. Do not report a successful measurement merely because execution succeeded.

The Ollama adapter records every `chat` request, including retries and streaming
requests, at the client protocol seam. It retains local identity, optional remote
identity (currently absent for Ollama), execution context, start, local interruption,
cancellation, and terminal evidence. Only an Ollama response with `done=True`
clears a request; this occurs before response parsing, so a later local parsing
error does not erase genuine remote completion. Stream exhaustion without a
terminal marker is insufficient.

Local exceptions, timeouts, cancellation, stream/connection close, completed
workers, and closed agent factories do **not** prove remote termination. Check
`requests.outstanding` and `requests.contained` during bounded cleanup. If requests
remain uncontained at the shared cleanup deadline, abort before the next trial.
The current Ollama adapter implements **no remote termination protocol**. The
generic ledger accepts authoritative evidence from future adapters, but callers
must not fabricate it from local cancellation.

The built-in Ollama factories use the tracked adapter; deterministic models have
no remote requests. Opt-in invocation rejects other model adapters before sending
requests rather than silently claiming containment without request accounting.
Without a recording scope, clients and runtime metric meanings remain ordinary:
`eventDetectionSeconds` is occurrence-to-observation, `reasoningSeconds` includes
Provider construction/run, and `actionEffectSeconds` is postcondition verification.
