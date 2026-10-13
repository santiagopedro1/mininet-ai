# Raw latency records and trustworthy summaries

Preserve versioned raw evidence and explicit trial/agent projections rather than
replace unsuccessful or missing measurements with zeros. Summaries describe named
populations with visible denominators, not unconditional recovery estimates or
independent samples inferred from agents sharing a trial.

Decision discussion: [#35 — Define raw latency records and trustworthy summaries](https://github.com/santiagopedro1/mininet-ai/issues/35), part of
[#29 — Plan the link-failure latency experiment](https://github.com/santiagopedro1/mininet-ai/issues/29).
The user confirmed this planning contract. It does not implement a writer or change
existing output. It builds on ADRs
[0001](0001-link-failure-connectivity-recovery.md),
[0002](0002-link-failure-latency-boundaries.md),
[0003](0003-seeded-topology-and-failure-scenario.md),
[0004](0004-repeated-failure-trial-lifecycle.md), and
[0005](0005-clock-correlation-and-instrumentation.md).

## Versioned output layout

Use schema identity `mininet-ai/link-failure-latency/v2` in a new output directory.
Do not label this layout as the existing v1 JSONL format or overwrite another run.

Before implementation is accepted, publish JSON Schema Draft 2020-12 schemas for
`run.json`, each `events.jsonl` event variant, `trials.jsonl`, `agents.jsonl`, and
`summary.json`. Each JSONL line validates as one record. Event variants use an
explicit discriminator. Add valid and invalid fixtures for every schema family,
plus artifact-level valid/invalid fixtures for rules that span files. JSON Schema
validates local record structure; a separate artifact validator checks references,
sequences, timings, and reconciled counts.

| File | Responsibility |
| --- | --- |
| `run.json` | Immutable run manifest: identities, requested configuration, scenario replay data, measurement environment, and schema/generator versions |
| `events.jsonl` | Append-only raw operation transitions, observations, probe evidence, admission/closure, containment evidence, and run lifecycle events |
| `trials.jsonl` | Finalized trial projections with frozen outcome, measurements, evidence references, and separate cleanup outcome |
| `agents.jsonl` | Finalized per-agent/per-trial projections, including missing phases and late/incomplete work |
| `summary.json` | Derived statistics, explicitly labelled populations, count reconciliation, and run/artifact completion state |

- Emit explicit run-finished or run-aborted events. A run lacking a terminal event
  is incomplete, not implicitly successful. Missing summaries do not erase raw
  evidence or prove completion.
- Warm-up records use the same relationships and phase evidence but an explicit
  `trial_kind` of `warmup`, distinct from `measured`. Keep their counts/outcomes
  separate; exclude them from measured timing summaries and rates.

## Units and measurement representation

- Encode exact raw monotonic nanosecond ticks as decimal strings, with explicit
  `clock_domain_id`. Retain UTC metadata independently. Integer precision in a
  downstream JSON reader must not corrupt the raw ticks.
- Use explicitly seconds-named derived fields, with finite numeric values. Do not
  serialize NaN or infinity. Preserve raw starts/ends so durations are reproducible.
- Represent each duration with availability, value, and reason, independently of
  operation outcome. A representative object is:

  ```json
  {"availability": "unavailable", "value_seconds": null, "reason": "no_action_attempt"}
  ```

- Availability is `available`, `unavailable`, or `not_applicable`. Available values
  are finite numbers with no missing reason. Unavailable and not-applicable values
  are null with an explicit reason. A genuinely measured zero is available, not a
  substitute for absent evidence.
- Distinguish such reasons as phase not started, no detection before trial end,
  no action attempt, incomplete phase, no confirmed recovery, missing evidence,
  clock/order invalidity, recognition before onset, and action after recovery.
  Preserve additional anomaly/quality flags rather than hiding simultaneous issues.
- An operation can fail while its elapsed duration is available. Successful
  recognition can likewise have an unavailable detection duration when it precedes
  confirmed onset. Keep lifecycle state, success/failure, availability, and lateness
  separate rather than encode all of them in one status.
- If injection never establishes a confirmed fault, expected fault-response
  measurements are not applicable. Once a fault is established, agents that never
  detect or act have unavailable measurements with reasons, not zero durations.

## Record relationships and required content

- Give emitted records unique `record_id` identities and collector sequence
  numbers. Link them with explicit parent/evidence references. Keep collector order
  distinct from timestamp order; a delayed record must retain its original tick.
- Carry the execution identities required by ADR-0005: experiment `run_id`,
  `trial_id`, `fault_id`, agent, observation/event, invocation, action/operation,
  invocation-scoped proposal, probe-round, and Provider-request identities as
  applicable. Keep scenario identity and seed distinct from execution identity.
- The immutable manifest preserves generator/schema versions, seed/configuration,
  actual graph and port mapping, host attachments, designated traffic, eligible
  failure ordering, and supplied policies/paths needed for replay. Identify each
  selected trial fault and its healthy/recovery paths in trial evidence.
- Manifest provenance also includes requested measured/warm-up counts, stage
  budgets, sampling/probe periods, code revision, and Provider/model configuration.
  Record resolved model identity when available, otherwise explicitly unknown;
  a mutable model tag is not a resolved artifact identity.
- Raw events preserve all phase starts/ends, failures, cancellations, and incomplete
  operations; injection submission/completion and confirmed-onset evidence;
  recognition source and readiness; both probe directions, slots and link checks;
  first successful round and confirmed recovery; admission closure; and Provider
  containment/cleanup evidence. Do not substitute SDK aggregate durations for raw
  phase transitions. Retain SDK compatibility metrics separately with their native
  meanings and label supplementary remote/native timings.
- Trial projections identify kind, ordinal, scenario/fault, selected link, phase
  evidence, response deadline, frozen outcome and reason, recovery measurement,
  lifecycle timestamps, cleanup result, and relevant record references. Setup,
  response, stopping, and total wall elapsed intervals must not share an ambiguous
  elapsed-duration field with different origins.
- Normally finalize one trial record and ten assigned-agent records for every
  started attempt, even if setup fails or agents never detect, reason, or act.
  Agent projections identify agent/switch, trial/fault, detection, invocation/action
  references, per-phase state/outcome/availability, raw timing/evidence references,
  SDK compatibility data, and late/incomplete work. Preserve individual rule-action
  records rather than replacing a policy pass with one aggregate.
- Finalize projections after bounded cleanup, retaining the earlier frozen trial
  outcome. Work completed after trial end is marked late; it cannot revise that
  outcome. Outstanding operations at cleanup expiry remain explicitly incomplete.
- A crash can leave started-trial events without finalized projections. Preserve
  this evidence and disclose incomplete artifacts; do not fabricate completed trial
  or agent records. Unstarted trials have no invented records.

## Separate trial, cleanup, and run outcomes

- Trial outcomes distinguish `recovered`, `timeout`, `invalid`, `setup_failed`,
  `injection_failed`, `no_observed_disruption`, and `error`, with stage and reason.
  An unhealthy baseline, restored failed link, or unreliable required observer
  is invalid, not ordinary packet loss; operational setup failure and unconfirmed
  injection remain distinguishable. Outcomes follow the earlier lifecycle ADR.
- Cleanup outcome is independently `verified`, `failed`, or `unknown`, supported
  by network-removal, worker, and Provider-containment evidence. Record completed
  teardown, not just cleanup exceptions.
- Run state distinguishes completed, aborted, and incomplete artifacts, with a
  terminal reason when available. A recovered trial with unsafe cleanup remains
  recovered while the run aborts; cleanup cannot erase a valid response result.
- Preserve requested, started, trial-outcome-finalized, and cleanup-completed counts
  separately for measured and warm-up attempts. Unknown outcomes or missing
  projections in incomplete artifacts are visible, not silently dropped.

### Closed v2 status and transition values

- Availability is exactly `available`, `unavailable`, or `not_applicable`, as
  defined above. Cleanup outcome is exactly `verified`, `failed`, or `unknown`.
- Trial outcome is exactly `recovered`, `timeout`, `invalid`, `setup_failed`,
  `injection_failed`, `no_observed_disruption`, or `error`.
- Run terminal events are `run_finished` and `run_aborted`; absence of either
  terminal event in persisted evidence is interpreted as an incomplete artifact,
  not as a third emitted terminal event.
- Operation transitions are `operation_started`, `operation_completed`,
  `operation_failed`, and `operation_cancelled`. Operation projection state is
  exactly `not_started`, `in_progress`, `completed`, `failed`, `cancelled`, or
  `incomplete`. `incomplete` is derived when an operation lacks a terminal
  transition in a finalized artifact; it is not an emitted terminal transition.
- Lateness and duration availability are independent of operation state. Unknown
  values for closed v2 enums fail validation. Adding or changing an enum value
  requires a new schema version and updated conformance fixtures; consumers must
  not guess the meaning of an unknown value.

## Summary populations and statistics

- Summaries are derived outputs checked against persisted raw evidence and
  projections, never replacements for them. Label population, unit, trial kind,
  grouping, and completeness explicitly. Incomplete-run summaries must disclose
  unknown outcomes and partial data; do not present partial rates as final results.
- For each timing distribution report count, arithmetic mean, median, minimum,
  and maximum. For an even count, median is the mean of the two middle sorted
  values. Empty distributions have count zero and null statistics, not zeros.
  Do not emit percentiles, confidence intervals, or survival estimates in this
  first contract.
- Show available, unavailable, and not-applicable population counts, with reason
  counts for both unavailable and not-applicable measurements in every grouping.
  Also identify included and late-excluded counts: availability
  describes the record, while inclusion describes the primary summary population.
  Do not relabel an available late duration as missing merely because it is excluded.
- Report every measured trial-outcome count, including unsuccessful and invalid
  attempts. Requested attempts and unstarted remainder must remain visible.
- Recovery-duration statistics contain only valid confirmed recovery measurements.
  They are conditional on measured recovery, not an estimate for all attempts.
  Timeout recovery durations remain unavailable; retain the response deadline
  separately rather than substitute it as recovery time.
- Report recovery frequency over all started measured attempts, and a separately
  labelled rate over valid disrupted trials ending in recovery or response timeout.
  Exclude invalid/unobservable trials from the latter denominator but show their
  counts. Verified trial results remain eligible despite later cleanup failure.
  Empty denominators produce null rates. Incomplete artifacts expose their unknown
  outcomes instead of treating them as failures or silently removing them.
- Detection, reasoning, and action-effect distributions use agent-trial measurement
  populations. Execution and verification distributions use individual action
  populations. Label pooled agent values as descriptive correlated observations,
  not independent trial repetitions.
- Include valid elapsed measurements from failed Provider/action attempts when
  completed within the trial window, with operation-outcome and trial-outcome
  breakdowns. Exclude late completions from primary timing distributions, retain
  their raw/projected data, and report separate late-work counts.
- Report detection/action participation over applicable agent-trial opportunities.
  Retain all ten assigned agents where a confirmed fault exists, including those
  never detecting or acting. Trials without a confirmed fault contribute explicit
  not-applicable counts, not zero-latency successes.
- Provide run totals and labelled per-agent and per-failed-link breakdowns, each
  with its own population and missing-reason counts. Do not pool different runs,
  topology/model configurations, or warm-up and measured trials. No charts,
  dashboards, model comparisons, or rankings accompany these summaries.

## Persistence and integrity

- Create a fresh output directory and write JSON documents atomically. Keep JSONL
  evidence append-only. Persist required evidence before presenting its final
  projections or a completed summary.
- Validate schema versions, unique identities, required parent references, sequence
  gaps, timing domains/order, and final projection consistency. Required references
  may point to projections finalized later; check the complete artifact set rather
  than rejecting a valid in-progress stream solely for that delay.
- Expose duplicates, broken references, detected gaps, or recorder loss as integrity
  failures. A truncated final JSONL line can be recovered as an incomplete tail;
  never silently accept it as proof of a complete run. Complete preceding evidence
  remains recoverable without inventing missing operations or outcomes.

## Existing implementation gap

The current `examples/link-failure-latency/run.py` writes a v1 single JSONL stream
with a manifest, trials containing embedded agents, cleanup errors, and a summary.
It retains nullable durations but lacks the new phase records, warm-up separation,
clock/correlation provenance, late-work classification, and completeness accounting.
Its pooled summaries use nearest-rank p95, which is effectively the maximum in
small populations, without the population/outcome labelling agreed here. Existing
audit and ledger envelopes/storage can be reused, but do not themselves supply the
new measurements. This ADR specifies the replacement contract, not implemented
v2 support; no writer, runtime, schema files, or summary code changes accompany it.
The handoff acceptance checklist is in
[ADR-0005](0005-clock-correlation-and-instrumentation.md). Concrete Draft 2020-12
schemas and valid/invalid fixtures remain required implementation-handoff artifacts.
