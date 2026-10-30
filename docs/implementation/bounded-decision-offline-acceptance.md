# Bounded-decision offline acceptance

> **Latest offline repair (2026-10-30):** The CLI/results integration mismatch
> is resolved on `fix/cli-results-integration`. Final full suite: **1178 passed,
> 3 opt-in skips, 179 subtests passed**. Lint, formatting, Pyright and
> diamond/iperf validation/planning pass. See the
> [repair report](cli-results-integration.md); the prior failing refresh below
> is historical. Native-model correctness still fails, and live network
> acceptance remains blocked.

> **2026-10-30 refresh:** The merged tree no longer has a clean full-regression
> result: 137 failed, 1032 passed, 3 opt-in skips. A focused current diagnosis,
> cleanup, guard and evidence run passed 57 tests; lint, formatting, Pyright and
> diamond/iperf validation and planning passed. All 12 retained execution
> records in the two committed diagnosis archives pass strict schema validation.
> Earlier counts below remain historical. See the
> [acceptance handoff](bounded-decision-acceptance-handoff.md) for the reproducible
> CLI results-contract regression and remaining live gates.

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
| Full `uv run pytest -q` (2026-10-29, follow-up fixes) | `1049 passed, 3 skipped, 179 subtests passed`; skips are opt-in Mininet/OVS integration checks |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Passed; repository formatting normalized in a separate commit |
| `uv run pyright` | Passed: 0 errors, 0 warnings |
| Maintained Example CLI validation/planning | iperf-throughput valid (11 resources, 2 agent instances); diamond-recovery valid (25 resources, 4 agent instances); both planned successfully |

The earlier cleanup found two lint issues, 48 formatting differences and 177
type errors. The follow-up resolves them without disabling checks. Runtime and
schema fixes have additional red/green regressions; see the final review.

## Live results and remaining acceptance gaps

The complete live-network handoff and blocking verdict are recorded in the
[live-network evaluation report](bounded-decision-live-network-evaluation.md).

- Native runtime-shaped constructed-fixture evaluation recorded 12/12 correct;
  a direct-client live-telemetry run recorded 16/16 matches against port-state
  labels, warm latency 1.173–1.486 s, zero output tokens, and verified reset/cleanup.
  Cold first inference was recorded separately (0.834 s, n=1).
- The replacement runner now traverses the real four-agent continuous runtime,
  uses `ovs.port-state` and exact target-local expected flows, and retains
  normalized Run Evidence. Offline tests cover down/missing/unknown inputs and
  missing/wrong flows; they do not establish live model quality.
- Historical explicit execution/decision fields (including empty fields),
  proposal identifiers, startup inference budgets, invalid-response retention,
  and published evidence-schema completeness now have regression coverage.
- A fresh native-service run of the corrected runtime with constructed healthy
  observations **failed the declared operational workload**: two correct native
  choices and four timeouts across six invocations. The one-slot service was
  observed idle before the run and eventually idle afterward; no inference was
  retried. This is not a universal latency benchmark failure, nor OVS evidence.
  See the live-model report for the exact conditions and retained artifacts.
- Cancellation/late-result containment under live deployed-runtime conditions,
  concurrent load, and provenance not observable from the service remain
  unverified. Earlier terminal cancellation evidence is retained separately.

The remaining native workload and privileged live-evaluation gaps block
completion of #116; they do not reopen pinned
model or network-recovery requirements superseded by its amendments. Do not
label scripted inference as model quality, direct-client telemetry as deployed
runtime acceptance, or local cancellation as remote termination. No privileged
network trial was performed during this follow-up.
