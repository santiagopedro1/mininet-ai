# Confidence-gated combined recovery: offline implementation

## Configuration and policy

`CampaignConfig` and `run_campaign` support `combined_gated`, the fourth declared
treatment slot. Both combined slots use precisely the same S1 instructions,
criteria, local catalogue, native request/schema and returned confidence fields.
The existing S2 adapter, broader permissions and three-attempt bound are shared
with direct S2 and combined ungated. There is still at most one S1 repair attempt.
No core bounded-decision or preserved pilot behavior changes.

- `confidence_gate_enabled` defaults to `false`, including for the fourth slot.
  Retaining the slot does not imply a calibrated threshold exists.
- Enabling requires `treatment=combined_gated` and an explicit finite numeric
  `confidence_threshold` in [0,1]. Ungated and single-stage treatments cannot enable
  it. Boolean/string thresholds and coercive enablement strings are rejected.
- For a usable decision, an enabled gate escalates **before local execution** if
  confidence is strictly below the threshold. Equality proceeds: threshold 0
  permits every valid confidence; threshold 1 permits confidence 1.
- Confidence is valid only for finite numeric [0,1], excluding booleans and numeric
  strings. Missing/invalid confidence alone escalates only with the gate enabled.
- Unusable decisions and explicit inability take shared failure precedence over
  the gate, preserving their actual cause. A confident unsuccessful action,
  unsuccessful service verification or S1 stage timeout still requests escalation.
  Healthy `no_action` still uses operational verification; gate-disabled never
  means unconditional execution of both stages.

Gate-disabled fourth-slot runs have the same behavior as combined ungated, except
the retained treatment/configuration identity. A disabled configuration can retain
a threshold for provenance; it is not consulted for escalation. This implements
the mechanism only, not calibration, threshold selection or campaign allocation.

## Evidence and CLI

`confidence_gate` events retain enabled/threshold, reported confidence and presence,
validation status, selected choice, proceed/escalate/shared-failure decision,
cause, skipped local execution and remaining common budget. Handoff includes the
gate event, S1 decision/observations, empty attempted-action list when gated,
original deadline and inherited remaining time. The original decision is never
rewritten. Inability/unusable decisions are not relabelled confidence failures.

Configuration and provenance retain gate semantics (`strictly_below`, validity and
failure precedence). Existing evidence retains action results, independent service
outcomes, full observation, stage token usage/latency and handoff message accounting.
Nonfinite adapter values use `{"nonfinite_float": "nan"}` (or `inf`/`-inf`) in
strict-JSON events/responses instead of crashing export. Native wire journals
remain original; the shared HTTP parser rejects non-JSON NaN/Infinity before a
usable decision exists, under the same failure/containment rules in both slots.

Network-free validation only:

```bash
python -m examples.noms27.recovery_campaign \
  --config examples/noms27/recovery_campaign/development-gated.json --check
```

The development threshold 0.5 and budgets are **unapproved placeholders**, not
calibration findings. Purpose remains `mechanism_validation` and models remain
unconfigured. `--check` reports the gate plus both stages' authority without model
or network access. To validate gate-disabled in the fourth slot, copy that JSON
and set `confidence_gate_enabled` to `false`; no separate prompt is required.

## Acceptance remains blocked

Ticket 03's safety limitation is intentionally preserved: stage timeout requests
escalation, but outstanding/unknown provider or action work denies actual S2
admission (`escalation_uncontained`), even with time remaining. Local cancellation
is not remote termination. Contained verification timeout can hand off. This is
not full acceptance of the timeout-trigger criterion; human review must accept
the safety boundary or require a containment mechanism.

All prerequisite live acceptance, measurement validation, six-class feasibility,
approved numerical budgets/resources, separate calibration/freezing, and Pedro's
held-out execution authorization remain outstanding. Controlled output tests do
not establish confidence effectiveness or select a threshold. No live networks,
VM/model-machine access, provider calls, dependency downloads, pushes, GitHub
writes or main merges are authorized by this implementation. The paper tracker
is owned by the parent session and is not modified here.
