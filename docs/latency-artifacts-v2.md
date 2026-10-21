# Link-failure latency v2 artifact contract

This is the concrete serialization and offline validation contract for ADRs
0001–0006. It does **not** switch the maintained runner to v2, implement a trial
scheduler, certify live recovery, or implement a Provider termination protocol.
The existing v1 single-JSONL output is not v2 input.

## Public boundaries

```python
from mininet_ai.experiments.latency import validate_artifact, validate_record

issues = validate_record("event", parsed_json_record)
report = validate_artifact("path/to/output-directory")
print(report.state, report.integrity)
for issue in report.issues:
    print(issue.severity, issue.code, issue.location, issue.message)
```

Record families are `run`, `event`, `trial`, `agent`, and `summary`. Unknown family
names raise `ValueError`; invalid evidence returns diagnostics, without coercion.
The directory API does not create, repair, truncate, or otherwise modify evidence.
It returns retained schema-valid records in collector order. Records rejected by
schema validation remain on disk and have diagnostics, rather than being passed
to cross-file checks as if well formed.

`state` is `completed`, `aborted`, or `incomplete`. `integrity` independently is
`valid` or `invalid`. A faithfully recorded abort is valid evidence; valid evidence
does not mean successful recovery or verified live acceptance. Always inspect both
fields. Missing terminal evidence, summary, projections, or a recoverable torn tail
prevents a completed claim. Complete records before a truncated final JSONL line
remain available; malformed interior lines, duplicate JSON members, non-finite
numbers, and schema violations are integrity errors. No missing operation, outcome,
or projection is invented.

The bundled Draft 2020-12 schemas are published in
`mininet_ai/experiments/latency/schemas/` and included in distributions. Their
canonical `$id` base is
`https://mininet-ai.org/schemas/link-failure-latency/v2/`; these are identifiers,
not a promise of hosted documents. The validator registers local resources and
does not fetch schemas from the network. Other consumers should register all six
documents (`common` plus the five families) before resolving relative `$ref`s.

## Common envelope and references

All five families require:

| Field | Meaning |
| --- | --- |
| `schema` | Exactly `mininet-ai/link-failure-latency/v2` |
| `record_id` | Globally unique nonempty emitted-record identity |
| `collector_seq` | Global, contiguous integer sequence starting at zero; manifest is zero |
| `run_id` | Experiment execution identity, not scenario identity |
| `clock_domain_id` | Experiment-owned monotonic clock domain |
| `tick_ns` | Exact nonnegative raw nanoseconds, canonical decimal **string** |
| `evidence_ids` | Unique referenced record identities; empty when none |

Collector order is not timestamp order: a delayed operation start can appear
after its terminal record. Within-phase arithmetic uses the retained transition
ticks, never sequence numbers or collector delivery time. Every file's records
share the manifest's run and authoritative clock domain.

Fields ending in `_record_id`, `probe_record_ids`, and `evidence_ids` refer to
emitted records. References are resolved against the whole artifact, including
projections emitted later. Execution identities (`trial_id`, `fault_id`, agent,
invocation, operation/action, invocation-scoped proposal, observation, probe-round,
and Provider-request IDs) are distinct from record references. References cannot
cross known run/trial/fault/agent scope.

## Manifest: `run.json`

The immutable manifest includes UTC creation metadata, explicit unsigned 64-bit
seed, `sha256-counter-v1` generator identity, scenario identity, ten agent/switch
assignments, requested counts, four separate stage budgets, and sampling/probe
periods. `scenario` records the actual graph, port map, host attachments/addressing,
both designated directions, eligible failures and warm-up/measured selection
orders, healthy paths, and supplied healthy/recovery policies and paths. A policy
rule names its switch, destination host, ARP/IPv4 type, and output port. Recovery
policies additionally identify the excluded link.

`provenance` requires code revision, Provider/model configuration, environment,
and `resolved_model_identity`: an explicit null means unknown. A mutable model tag
must not be substituted for that resolved identity. Recorded replay data does not
replace the generator's separate golden-vector conformance test.

## Raw evidence: `events.jsonl`

Every line is one event, discriminated by `type`, with a closed typed `data`
payload. `trial_id` and `fault_id` carry context; run terminal events have null
trial/fault context. The exact required payload fields are in the schema.

| Variant | Payload responsibility |
| --- | --- |
| `trial_started`, `trial_ended` | Kind/ordinal/scenario/selected link; frozen outcome/stage/reason |
| `operation_started`, `operation_completed`, `operation_failed`, `operation_cancelled` | Operation/phase, agent/invocation/proposal context, outcome |
| `observation_ready` | Observation source and assigned agent readiness |
| `observation`, `recognition`, `fault_confirmed` | Sample schedule/start/end, both endpoint states, observation identity/source, accepted recognition and confirming-observation reference |
| `probe_round`, `slot_missed` | Purpose/slot/schedule, both directional results and observation IDs, start/end, before/after endpoint checks, lateness; missed slot reason |
| `recovery_confirmed` | Three probe-record references and first-success reference |
| `admission`, `admission_closed` | Invocation/action context and accepted/rejected gate decision; closure reason |
| `provider_request_started`, `provider_request_terminal`, `provider_cancel_requested` | Request/invocation/agent/Provider identities; supported protocol-terminal evidence/source; local cancellation reason |
| `cleanup_completed` | Independent cleanup outcome, network absence, worker stop and Provider containment evidence, reason |
| `recorder_loss` | Explicit loss count and reason, always an integrity failure |
| `run_finished`, `run_aborted` | Terminal reason, never an inferred success |

`operation_incomplete` and `run_incomplete` are not emitted transitions. Missing
terminal operation evidence derives an incomplete projection; absence of run
terminal evidence derives incomplete artifacts. Provider-terminal evidence is
`normal_response` or `remote_termination`; neither local cancellation nor a closed
factory qualifies. The validator checks persisted claims and correlations; it
cannot authenticate a remote protocol or prove a host resource is actually absent.

## Projections: `trials.jsonl` and `agents.jsonl`

Trial projections retain kind/ordinal/scenario/fault/link and healthy/recovery
paths, frozen outcome/stage/reason, references to start/end/cleanup evidence,
nullable confirmed-fault/recovery references and response deadline, and separate
cleanup outcome. The named `setup`, `injection`, `response`, `recovery`, `stopping`,
and `total` measurements never overload a single elapsed field.

Each normally finalized trial has exactly its ten manifest-assigned agent
projections. Each agent projection names its switch, invocation/action identities,
primary detection/reasoning/action-effect measurements, individual operation
projections, retained SDK metrics, and independent late/incomplete flags.
Operation projections retain phase, lifecycle state, outcome, lateness, duration,
and raw references. Preserve individual execution and verification operations for
each rule action, rather than substituting a policy-pass total.

Duration objects always contain `availability`, `value_seconds`, `reason`,
`start_record_id`, `end_record_id`, and `flags`. `available` requires a finite
nonnegative value, null reason, and both raw boundary references. Other availability
states require a null value and nonempty missing/not-applicable reason; boundaries
may still be present. A real zero is available. Preserve anomalies and raw evidence
instead of negative durations or clamping. Operation outcome and lateness do not
change the availability vocabulary. SDK metric names retain their existing SDK
meanings; `native` data is supplementary and never authoritative clock evidence.

## Summary: `summary.json`

`counts` separates `warmup` and `measured`, each with requested, started, frozen
outcome-finalized, cleanup-stage-completed, unknown, unstarted, and every closed
trial-outcome count. Cleanup-stage completion does not imply verified cleanup.
`state` and `terminal_reason` retain run completion independently of trial outcomes.

`distributions` name metric, observational population (`trial`, `agent_trial`, or
individual `action`), seconds unit, measured trial kind, grouping and completeness.
Each records availability/reason counts, included and late-excluded counts, count,
mean/median/min/max in seconds. Empty statistics are null. Groups use nullable
`agent_id`, `failed_link`, `trial_outcome`, and `operation_outcome` filters; all null
denotes run totals. Warm-up never contributes. Available late durations remain
available but are excluded from primary statistics. Recovery timings are conditional
on observed recovery; pooled agent-trial values are descriptive **correlated**
observations, not independent repetitions.

`rates` separately names `recovery_all_started`, `recovery_valid_disrupted`,
`detection_participation`, or `action_participation`, with group/completeness and
explicit numerator/denominator/value. Empty denominators produce null values.
Complete and aborted summaries require six timing totals and four rates. Grouped
rows are reconciled against their filtered persisted population. The v2 writer's
integration acceptance additionally requires the agreed per-agent/per-failed-link
and outcome breakdowns; passing local record validation alone is not that acceptance.

## Conformance fixtures and checks

`docs/fixtures/link-failure-latency-v2/` contains valid/invalid record fixtures for
every family and event variant, plus actual artifact directories indexed by
`cases.json`. Cases include recovery, setup failure with ten missing-phase agents,
failed phases, normal Provider completion, unknown-containment abort, failed
warm-up, crash-interrupted starts, torn tails, and corrupted cross-file relationships.
All are synthetic evidence, not recorded experiments.

```bash
.venv/bin/python -m pytest tests/experiments/test_latency_artifacts.py -q
# Explicit developer regeneration; expectations remain independently tested:
PYTHONPATH=. .venv/bin/python scripts/generate-latency-fixtures.py
```

The schemas and validator are usable without root, Mininet, a model service, or
changes to ordinary uninstrumented runtime execution. Live acceptance remains a
separate human verification gate.
