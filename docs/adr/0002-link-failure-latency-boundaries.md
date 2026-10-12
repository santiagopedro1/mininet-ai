# Link-failure latency boundaries

Measure fault response with explicit observation, Provider, action, and recovery
boundaries rather than treating similarly named SDK and experiment metrics as
interchangeable. Retain supporting intervals and raw cycles because concurrent
agent behavior and recovery confirmation prevent the phase durations from being
an additive decomposition of trial recovery.

Decision discussion: [#31 — Define detection, reasoning, and action-effect timing boundaries](https://github.com/santiagopedro1/mininet-ai/issues/31), part of
[#29 — Plan the link-failure latency experiment](https://github.com/santiagopedro1/mininet-ai/issues/29).
This contract was confirmed by the user.

This is an agreed planning contract, not implemented instrumentation. It builds
on [the connectivity recovery contract](0001-link-failure-connectivity-recovery.md).

## Primary measurement boundaries

| Measurement | Start | End |
| --- | --- | --- |
| Per-agent detection | Injected link failure confirmed active (link down) | Agent's first relevant failure recognition through its observation path |
| Per-invocation reasoning | Immediately before Provider construction | Provider returns its decision or fails |
| Per-agent experiment action-effect | Agent's first action attempt | Confirmed shared Connectivity Recovery |
| Trial recovery | Injected link failure confirmed active (link down) | Completion of the third consecutive all-success probe round and failed-link-down confirmation |

- Confirmed link-down is the observable failure onset, not a claim about the
  exact physical instant the link failed. Retain injection-command duration
  separately; do not use command submission as the response-time origin.
- Detection includes waiting for the next observation, collecting it, and
  evaluating it. It ends at recognition, not invocation start. Keep subsequent
  invocation scheduling delay separate from detection and reasoning.
- Observation paths must already be running before injection. Merely receiving
  a harness-generated instruction announcing the fault is not detection. A
  harness/global observation is acceptable if its source is explicit; do not
  describe it as autonomous local-switch detection.
- Reasoning includes Provider construction and execution, including Provider/model
  queueing within that interval. It excludes preceding context construction and
  subsequent action execution. Narrower model timings may be retained separately.
- Agent action-effect begins at the first attempted action, not its completion
  or first successful configuration change. It includes execution and recovery
  confirmation and overlaps action-execution timing. It does not establish that
  the agent caused the shared recovery.
- Trial recovery includes probing and the full three-round confirmation delay.
  Do not backdate recovery to the first successful round or equate it with the
  unknown instant forwarding was restored. Also retain the first successful
  round's timestamp without treating it as confirmed recovery.

## Supporting intervals and unsuccessful phases

- Retain every relevant agent invocation and action timing, rather than replacing
  the raw cycles with one aggregate. The primary detection and agent action-effect
  intervals use first relevant recognition and first action attempt respectively.
- Preserve separately identifiable scheduling, context construction,
  decision-to-action delay, action execution, and postcondition verification,
  alongside injection duration. This keeps preparation and admission delays
  outside reasoning visible rather than silently omitting them.
- Failed Provider calls retain their elapsed reasoning durations and failure
  status. Attempted but failed actions retain execution durations and failure
  status. These durations do not establish successful decisions, successful
  actions, or Connectivity Recovery.
- Phases that never begin remain missing. Incomplete phases are not completed
  measurements; do not substitute zero for absent detection, reasoning, actions,
  or recovery.
- Without an attempted action or confirmed recovery, there is no completed
  agent action-effect measurement. Retain the available timestamps and outcomes.
- Recognition can precede the harness's confirmed-onset timestamp. Retain both
  timestamps, leave detection duration unavailable, and flag “recognition
  precedes confirmed onset”; do not report a negative duration or clamp to zero.
- If an agent starts acting after confirmed recovery, retain both timestamps,
  leave its action-effect duration unavailable, and flag “action starts after
  confirmed recovery”; do not report a negative duration or clamp to zero.
- Shared recovery does not establish individual causal contribution. Overlapping
  phases and concurrent agents mean phase durations must not be summed to
  compute trial recovery; measure that interval independently.

## Compatibility and remaining decisions

Keep existing SDK metric meanings distinct:

- `eventDetectionSeconds` describes the event producer's occurrence-to-observation
  interval; telemetry events use a sample timestamp, not injected failure onset.
  It is not the full experiment detection interval.
- `reasoningSeconds` already wraps Provider construction and execution, excluding
  context building, and supplies the agreed reasoning boundary.
- `actionEffectSeconds` describes postcondition-verification time after action
  execution, not restored network connectivity. Preserve it separately from the
  experiment's first-action-to-shared-recovery interval.

The existing `examples/link-failure-latency/run.py` instead starts response timing
before the disable command, starts agent workers after disruption confirmation,
and accepts one successful recovery probe. Its similarly named action-effect
field is a first-action-to-shared-observation interval. These existing behaviors
are not evidence that the new contract is implemented.

Exact clock sources, correlation, observation instrumentation, and timestamp
ordering validation remain for
[#34 — Choose clock, correlation, and instrumentation contracts](https://github.com/santiagopedro1/mininet-ai/issues/34).
Deadline duration and lifecycle details remain for
[#33 — Define repeated failure trials and their lifecycle](https://github.com/santiagopedro1/mininet-ai/issues/33).
Machine-readable records, statuses, and aggregation remain for
[#35 — Define raw latency records and trustworthy summaries](https://github.com/santiagopedro1/mininet-ai/issues/35).
