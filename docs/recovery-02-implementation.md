# Ticket 02: direct S2-only, offline implementation

## Status and boundary

Based on ticket 01 integration `bb980111958997b1c01dbe4c556c0de5fc5c50d2`.
Implements the direct baseline only, through the approved `CampaignConfig` →
`run_campaign` → exported events/evidence boundary. The S1 path and older pilot
examples retain their behavior. No combined policy, calibration or paper-tracker
changes are included.

**Offline mechanism validation is not live/campaign evidence.** Ticket 01 remains
open and blocks live acceptance. No VM startup, model-machine request, live fault,
dependency download or push was performed for ticket 02. Do not contact the
occupied model computer without renewed explicit authorization. The passing
mechanism checks cannot resolve either ticket's live criterion.

## Mechanism and permissions

- Select `treatment: s2_only`; the currently supported live-adapter scenario
  remains `diamond_s1_s2_link`. S2 first receives the same fresh affected-local
  switch snapshot used by S1, a generic directional-service alarm and broader
  network-wide port/flow observations. There is no S1 decision, local repair,
  confidence gate or handoff. Detection remains harness-triggered, not autonomous.
- Direct S2 ignores `s1_cap_seconds` (retained for configuration compatibility).
  Injection submission starts the common deadline. Injection, shared snapshot,
  wider telemetry, inference, action and verification all consume it. Attempts
  never reset it. New work is denied at expiry; cancellation/late results use
  ticket 01's accounting and containment contract.
- At most three observe–act–verify attempts, each with at most 16 typed forwarding
  entries. Inability/no-action stops early. Malformed or forbidden decisions use
  an attempt and can be followed by fresh observation. Failed execution or service
  verification also permits another attempt only within the common deadline.
  A missing terminal Provider response or an incomplete response (`done != true`)
  stops the policy rather than overlapping another possibly outstanding request,
  and leaves Provider containment unconfirmed.
- Forwarding entries contain only `switch`, integer `in_port` and `out_port`.
  Switches/ports must belong to the owned diamond and be observed up. The adapter
  rechecks them before execution, fails closed on missing/unknown port state,
  and admits no new forwarding command after deadline expiry. A fixed `add-flow`
  rule matches IPv4 h1→h2 addresses and ingress and outputs the declared port.
  No arbitrary command, port enablement, outage reversal, failed-resource revival,
  offered-traffic setting or evaluator fault/regime label is available to S2.
- Multi-switch execution is sequential, not atomic; failures/cancellation can
  leave partial forwarding effects. Per-command starts, terminal results and
  unknown cancelled executions are retained in `s2-actions.jsonl`; no rollback
  is claimed. Port observations are sampled, not continuous survival proof.

## Adapter and evidence

S2 uses a separate Ollama-compatible `/api/chat` structured JSON path, not the
native `/v1/systemone` client and not a protocol fallback. It has fresh messages
per attempt, with explicit bounded prior-attempt observations/actions/outcomes.
`think: false`, temperature 0, `num_predict: 1024`, non-streaming and keep-alive -1
are recorded. These development settings are not model determinism or feasibility
claims. A tiny warm-up runs before traffic/injection and is retained separately;
it does not count as an incident attempt or incident token usage.

Input/output counts come separately from `prompt_eval_count` and `eval_count`;
missing, boolean or negative counts remain null. Reasoning usage is unavailable,
not an invented zero. All attempt calls/latencies and known late completions are
included. `model-wire.jsonl` retains original requests/responses and transport
errors. Immutable model identity verification is unavailable for this adapter;
non-null `expected_identity` is rejected rather than pretending to verify it.
The observed model name is not an immutable revision.

S2 exports `schema_version: recovery-s2/v1`, with the same independent packet,
validity, action/service outcome, timing, observation, reset, cleanup, containment
and provenance fields as S1, plus permissions and per-attempt stage/repair data.
Prompt/schema/model settings and source hashes are retained; no S1 catalogue is
misrepresented as S2 authority. Failed/malformed/budget-exhausted treatments remain
valid outcomes unless independently demonstrated experimental failure occurs.

Independent sequence-numbered 1,200-byte UDP measurement, 100 ms delivery/binning,
one-second sustained verification, disruption, relapse and censoring remain the
[ticket 01 contract](recovery-01-implementation.md). Observation continues through
the original deadline + five seconds; collection drains another 100 ms. Early
policy termination never shortens measurement. Action exit zero is not service
recovery. Provider cancellation is local only; unresolved termination/cleanup
blocks another trial. JSON message byte counts are not wire bytes.

## Network-free configuration check

```bash
python -m examples.noms27.recovery_campaign \
  --config examples/noms27/recovery_campaign/development-s2.json --check
```

This validates and prints permissions and the repair schema without starting a
network or contacting a Provider. The sample is deliberately `mechanism_validation`
with an unconfigured model. Do not execute it as live evidence. A future authorized
single-incident operator would need `purpose: live_feasibility`, a fresh trial ID
and evidence directory, a local compatible S2 endpoint/model, null identity, and
explicit authorization of the VM/model resource. The existing CLI refuses live
execution without that purpose. Inspect validity, independent outcomes, cleanup
and containment separately, following the ticket 01 runbook, before any next trial.
The sample's numerical deadline/cap/capacity remain **development_unapproved**.

## Controlled broader-repair fixture and remaining limits

The explicitly declared `controlled_broader_repair` scenario is restricted to
S2 mechanism-validation configuration and an offline runtime; live execution is
rejected before setup or model access. It is not an additional campaign class.
The offline test runs the real S2 HTTP/schema/telemetry/forwarding adapter against
an in-process HTTP transport and controlled OVS command boundary. A forwarding
graph starts with healthy primary rules, an injected persistent primary-link
outage, and no active downstream bypass rules. The first local redirect drops
at the next switch. The next attempt observes the failed service result and
installs the two missing downstream forwarding rules. Independently walking the
forwarding graph determines the packet-restoration timeline, which the unchanged
evaluator verifies. This demonstrates broader multi-switch capability; it is
**not** an approved extended-diamond scenario class, a live recovery, held-out
evidence, a matched treatment comparison or an effectiveness estimate.

The current live adapter still has complete preinstalled downstream forwarding,
exactly as ticket 01. Extending live topology/placement coverage and validating
structural/live repairability of all six classes remain campaign prerequisites.
Ticket 01's corrected valid live recovery, S2 live feasibility, approved numerical
budgets/resources, calibrated/frozen policies, and Pedro's scientific execution
approval remain outstanding. This branch cannot close ticket 02's live acceptance.
The [implementer validation record](recovery-02-validation.md) records offline
checks, retained artifacts and the remaining merger/review handoff.
