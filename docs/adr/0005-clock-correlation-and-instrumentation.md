# Clock, correlation, and instrumentation contracts

Use one experiment-owned monotonic clock and additive phase recording to measure
fault response without changing existing SDK metric meanings. Explicit operation
identities, sampled recovery evidence, and Provider-terminal evidence prevent
timestamp proximity or local cancellation from being mistaken for causation or
verified trial isolation.

Decision discussion: [#34 — Choose clock, correlation, and instrumentation contracts](https://github.com/santiagopedro1/mininet-ai/issues/34), part of
[#29 — Plan the link-failure latency experiment](https://github.com/santiagopedro1/mininet-ai/issues/29).
The user confirmed this planning contract; no instrumentation code accompanies it.
It implements the measurement intent of the
[recovery criterion](0001-link-failure-connectivity-recovery.md),
[phase boundaries](0002-link-failure-latency-boundaries.md),
[seeded scenario](0003-seeded-topology-and-failure-scenario.md), and
[trial lifecycle](0004-repeated-failure-trial-lifecycle.md) at the contract level only.

## Clock authority and validation

- Inject one monotonic nanosecond clock into the coordinator and its local workers
  for experiment phase timestamps, scheduling, and deadlines. Identify its clock
  domain explicitly; calculate durations only from timestamps in that same domain.
- Retain UTC wall time as human-readable provenance, not elapsed-time arithmetic.
  Remote model timestamps and native model durations are supplementary; never
  subtract them from coordinator timestamps.
- Capture ticks at the actual transitions, before delivering records to a collector.
  Collector receipt, event-bus delivery, and final-result callbacks are not
  substitutes for observation recognition or earlier phase starts.
- Preserve operation starts, completions, failures, cancellations, and incomplete
  operations. Do not infer a missing start or end from an aggregate SDK duration.
- Validate within-phase timestamp ordering and clock-domain agreement. Malformed
  intervals are unavailable rather than clamped or corrected. Preserve the agreed
  recognition-before-confirmed-onset and action-after-recovery cross-phase anomalies
  explicitly, retaining their raw timestamps and unavailable derived durations.
- A genuinely measured zero interval is valid; absent data is not zero. Nanosecond
  storage precision is not a claim of nanosecond measurement accuracy.

## Execution identities and correlation

- Associate observations, events, invocations, actions, probes, and Provider requests
  with explicit experiment run, trial, and fault identities. These execution
  identities are distinct from seed and scenario identity: repeated identical
  scenarios still have different trial identities.
- Reuse existing run, agent, event, invocation, proposal, correlation, and causation
  identities where available. Add observation, probe-round, and operation identities
  where missing; scope proposal identities by their invocation.
- Propagate the explicit trial/fault context through each operation, rather than
  matching by timestamp proximity or a mutable current-fault pointer. Preserve
  existing observation-event-to-detector-event and event-to-invocation links.
- Shared recovery belongs to the trial/fault. Correlation identifies relevant work
  but does not establish that an individual agent caused recovery.

## Observation readiness, failure onset, and recognition

- Require explicit observation-path readiness acknowledgement before injection.
  Record readiness and any failure to become ready as setup evidence.
- Default failure-observation sampling to a configurable 100 ms period, independent
  of the one-second traffic-probe cadence. Use fixed monotonic schedule slots with
  no overlapping collections or catch-up bursts. Retain missed slots and scheduled,
  actual collection-start, and collection-completion times.
- Stamp authoritative failure onset when a fresh substrate observation finishes
  confirming both endpoint interfaces of the selected link are down. Retain the
  disable-command submission/completion and confirming observation start/end.
  This is observed administrative link-down, not the exact physical forwarding
  failure instant; successful command completion alone is insufficient.
- Detection requires fresh evidence collected after injection submission that
  identifies the selected failed link. Stamp recognition when the detector accepts
  that evidence, retaining its observation/event identity and source. Do not
  reassign stale queued samples to the fault or use later bus delivery as recognition.
- Recognition may precede the coordinator's confirmed-onset tick. Preserve that
  anomaly as required by the phase contract. Label global/harness observations
  explicitly rather than claiming autonomous local-switch detection.

## Additive instrumentation interface and seams

Use a small opt-in clock/recording interface at the existing runtime seams. Phase
records carry operation identity, execution context, clock-domain tick, transition,
and outcome/evidence. Recording is thread-safe; timestamps are captured at the
transition rather than assigned later by the collector. Normal execution remains
unchanged when instrumentation is disabled.

| Existing seam | Required additive evidence |
| --- | --- |
| Experiment coordinator and `examples/link-failure-latency/run.py` | Setup/readiness, injection submission/completion, confirmed onset, probe schedule/results, recovery confirmation, admission closure, stopping/cleanup, and late completions |
| `mininet_ai/runtime/telemetry.py` and substrate observation calls | Collection start/end, observation identity/source, detector evaluation and accepted recognition, readiness, and missed sampling slots |
| `mininet_ai/runtime/events.py` and `runtime/continuous.py` | Preserve event correlation/causation and invocation linkage; event publication and completed-result listeners are supplementary, not earlier phase timestamps |
| `mininet_ai/agents/runtime.py` | Invocation admission/scheduling, context start/end, Provider-construction start and decision/failure end, policy acceptance, action attempt, and completion/failure |
| `mininet_ai/capabilities/engine.py` and `capabilities/verification.py` | Capability admission, actual execution start/end, verification start/end and outcomes, separately from the action request |
| `mininet_ai/agents/agno/provider.py` and Provider-specific model/request adapters | Request identity, start, terminal evidence, cancellation status, and outstanding-request accounting |

- Record the first action attempt after exact supplied-policy acceptance and
  immediately before requesting capability execution. Rejected proposals are
  not attempts. An attempted request can still fail admission before changing the
  network; record admission and actual execution separately. Audit capability-start
  events alone do not prove network execution.
- Preserve verification timing independently from action execution and network
  recovery. Record scheduling, context construction, decision-to-action delay,
  failed attempts, and incomplete work rather than only final durations.
- Lost records or recorder failure make affected measurements unavailable. If
  required baseline, fault-state, or recovery evidence is no longer trustworthy,
  invalidate the trial; never silently drop evidence and report success.

## Probe rounds and sampled recovery evidence

- Use fixed one-second monotonic schedule slots for traffic-probe rounds. Retain
  scheduled time, actual starts, completions, both designated directional results,
  and their observation identities. Avoid overlapping rounds and catch-up bursts.
- A missed or incomplete scheduled slot breaks the consecutive-success streak.
  Retain late results, but do not let them silently fill missed slots or establish
  recovery after the response deadline.
- Check both failed-link endpoints before and after the traffic probes in every
  recovery-confirmation round. Any observed restoration invalidates the trial;
  missing, late, or contradictory required evidence prevents confirmation.
- Stamp recovery only after all required evidence for the third consecutive
  all-success round is available, including its final link-down confirmation.
  Preserve the first successful round's timestamp without backdating confirmation.
- These checks provide sampled evidence bounding each confirmation round. They
  are not atomic observations or proof of uninterrupted physical state between
  samples; keep that limitation explicit.

## Admission closure and Provider containment

- Use one synchronized admission/closure mechanism. Record admission, actual phase
  starts, and closure separately. After closure, grant no new invocation/action
  admissions; cancel pending work and contain already-admitted in-flight work
  under the single shared cleanup budget. Preserve late completions without
  revising the frozen trial outcome.
- Maintain a Provider-specific ledger of every request: local request identity,
  optional Provider request identity, start, and trustworthy terminal evidence.
  Keep linkage to the trial and invocation even if completion occurs during cleanup.
- A terminal response or confirmed remote termination can clear outstanding work.
  A local exception, timeout, closed connection, cancellation request, completed
  local worker, or closed factory cannot establish remote termination by itself.
- Record unknown or unsupported containment evidence explicitly. Abort when the
  shared cleanup budget expires without proof that previous requests have finished
  or terminated, as required by the lifecycle contract. Timing callbacks alone do
  not provide that proof. Persistent model caches are not outstanding requests.

## SDK compatibility and implementation gaps

- Keep `eventDetectionSeconds` as the producer's occurrence-to-observation interval
  (sample-to-emission for telemetry), not full injected-fault detection latency.
- Keep `reasoningSeconds` as Provider construction/run duration. Derive experiment
  reasoning from recorded monotonic phase ticks and check the corresponding SDK
  interval for consistency; do not synthesize missing ticks from the SDK field.
- Keep `actionEffectSeconds` as postcondition-verification timing. Retain it
  separately from first-action-attempt-to-shared-connectivity-recovery timing.
- Native model queueing/durations and verification metrics remain separate
  compatibility data. Experiment phase durations overlap and do not add up to
  trial recovery.

Existing runtime contracts have event/invocation/proposal identities, injectable
clocks in several modules, and completed metrics/listeners. They lack a fully
propagated experiment clock, raw phase callbacks, readiness acknowledgements,
general probe-round evidence, and Provider remote-terminal evidence. Existing
telemetry cadence waits after collection, and the example collector cannot retain
all cycles or pre-onset recognition. These are implementation gaps, not assertions
that the new contract is supported. No runtime code changes accompany this decision.

Exact machine-readable field names, serialization, statuses, record relationships,
and summary populations remain for
[#35 — Define raw latency records and trustworthy summaries](https://github.com/santiagopedro1/mininet-ai/issues/35).
