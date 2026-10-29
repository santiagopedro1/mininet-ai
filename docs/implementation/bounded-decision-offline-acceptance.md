# Bounded-decision offline acceptance

## Verdict

The bounded-decision path is **offline-contract tested**, not accepted as a
deployed live-diagnosis integration. The initial scope is current-only local
switch diagnosis, with no action proposals and a scenario-declared deadline
(5 seconds in the Maintained Example). Historical recovery fixtures remain
regression coverage, not current live acceptance requirements.
Tests use the native client's injected transport and Fake Substrate while
entering the continuous `ExperimentRuntime`; they do not substitute a second
executor. The oracle fixture is a set of expected decision labels and retained
round inputs, not a deterministic production gate. Passing scripted-choice
tests establishes request/response and runtime mechanics only; it says nothing
about live-model decision quality or latency.

The Maintained Example can be validated and planned without contacting a model
server or deploying Mininet. The opt-in live harness has not been run as part of
this offline acceptance.

## Contract and consumer inventory

| Contract or record | Current bounded-decision use | Compatibility/consumer behavior |
| --- | --- | --- |
| `mininet-ai/v1alpha5` / `urn:mininet-ai:schema:v1alpha5:deployment-plan` | New source compilation, resolved scenarios/settings, deployment inputs | `v1alpha3` and `v1alpha4` sources validate under their versioned rules; saved historical plans retain their original plan, snapshot and digest. |
| `mininet-ai/agent-runtime/v1alpha4` | Bounded runtime adapter and normalized response | Agno remains the default for omitted `execution`; bounded mode does not construct Agno sessions or memory. |
| `mininet-ai/bounded-decision/v1alpha1` | Strict retained decision payload | Carries unavailable provenance and explicit gaps; replay is an attempt, not bit-for-bit inference reproduction. |
| `mininet-ai/agent-execution/v1alpha1` | Execution-kind discriminator and runtime-specific record | Agno-only run/session/metrics fields remain unavailable for bounded execution rather than fabricated. |
| `mininet-ai/runtime-event/v1alpha1`, `mininet-ai/coordination-message/v1alpha1`, `mininet-ai/coordination-outcome/v1alpha1` | Trigger, coordination routing and normalized completion | Existing envelope versions remain unchanged; the execution record is nested in the existing invocation/result metadata. |
| `mininet-ai/audit/v1alpha1`, `mininet-ai/run-ledger/v1alpha1` | Lifecycle audit, durable ledger and export | Existing envelopes remain unchanged; bounded payload is retained in the existing metadata slot and ledger export. Missing or incomplete evidence is not success. |
| `mininet-ai/substrate-runtime/v1alpha3`, capability and action contracts | Authorized action admission, effects, inspection and cleanup | No bounded-specific serialized extension; normal authorization and explicit partial/unknown effects apply. |

Primary consumer guidance is in [Compatibility and versioning](../compatibility.md),
[Run logs](../run-logs.md), and the [Maintained Example](../../examples/diamond-recovery/README.md).
Consumers must dispatch on the embedded contract/version and execution kind,
preserve unknown fields only where their contract allows it, and must not
interpret absent model/server provenance, action outcomes, or exported records as
positive evidence.

## Offline coverage and result record

The acceptance suite covers:

- native `/v1/systemone` request shape, exact choice mapping, malformed and
  duplicate output, optional diagnostic validation, startup/interface and
  identity distinctions;
- scheduled runtime execution, coordinator/capability/action lifecycle,
  no-action versus failure, retained completion evidence, and ledger export;
- both individual primary-link failure labels and a three-round directional
  oracle fixture for healthy/startup, transient loss, stale, missing,
  conflicting, failed recovery probes, partial/unknown policy, and post-recovery
  restraint;
- original-tick deadlines, expiry at the exact boundary, cancellation, queue and
  admission waits, coalescing, no late admission, already-started effects,
  reconciliation, safe remaining-step retries and refusal of unsafe state;
- strict schema/compiler behavior, reference/inline plans, immutable saved
  plans, historical source/plan compatibility, Agno factory/session/Learned
  Memory regressions, and mixed Agno/bounded runtime concurrency.

Record command results for the current branch before publishing a new acceptance
claim:

| Check | Result |
| --- | --- |
| Full `uv run pytest` (2026-10-29, `dfe26ff`) | `1004 passed, 3 skipped`; skips are opt-in Mininet/OVS integration checks |
| Post-cleanup example/native-client/HTTP tests | `56 passed` |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Not clean: 48 files would be reformatted; includes branch-touched files, not classified as all pre-existing |
| `uv run pyright` | Not clean: 177 errors across the configured `mininet_ai` and `tests` scope; not waived or classified as all pre-existing |
| `uv run pyright mininet_ai` | Not clean: 4 errors in compiler and continuous runtime |
| Maintained Example CLI validation/planning | iperf-throughput valid (11 resources, 2 agent instances); diamond-recovery valid (25 resources, 4 agent instances); both planned successfully |

The first refreshed Ruff lint run found two issues in `live_diagnosis.py`
(non-executable shebang and import spacing); those were corrected in the
handoff cleanup and lint was rerun. No runtime behavior changed during cleanup.

## Live results and remaining acceptance gaps

The complete live-network handoff and blocking verdict are recorded in the
[live-network evaluation report](bounded-decision-live-network-evaluation.md).

- Native runtime-shaped constructed-fixture evaluation recorded 12/12 correct;
  a direct-client live-telemetry run recorded 16/16 matches against port-state
  labels, warm latency 1.173–1.486 s, zero output tokens, and verified reset/cleanup.
  Cold first inference was recorded separately (0.834 s, n=1).
- The live runner bypasses the compiled four-agent continuous runtime. The
  example's real `ovs.port-counters` collector lacks operational port-state
  inputs, and the expected-flow completeness oracle is not defined/tested.
- Historical source validation admits explicit `execution: {kind: agno}` under
  old versions despite the documented new-field boundary. Review also found
  incomplete fixed-proposal identifier validation.
- Formatting/type-check failures remain undisposed. See the
  [final review](bounded-decision-final-review.md) for concrete findings.
- Cancellation/late-result containment under live deployed-runtime conditions,
  concurrent load, and provenance not observable from the service remain
  unverified. Earlier terminal cancellation evidence is retained separately.

These integration gaps block completion of #116; they do not reopen pinned
model or network-recovery requirements superseded by its amendments. Do not
label scripted inference as model quality, direct-client telemetry as deployed
runtime acceptance, or local cancellation as remote termination. No new live
inference or raw external evidence audit was performed during this refresh.
