# Bounded-decision offline acceptance

## Verdict

The bounded-decision path is **offline-contract tested**, not live-supported.
Tests use the native client's injected transport and Fake Substrate while
entering the continuous `ExperimentRuntime`; they do not substitute a second
executor. The oracle fixture is a set of expected decision labels and retained
round inputs, not a deterministic production gate. Passing scripted-choice
tests establishes request/response and runtime mechanics only; it says nothing
about the pinned candidate's decision quality or latency.

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
| Focused bounded/example/compatibility tests | `147 passed` |
| Full `uv run pytest` | `998 passed, 3 skipped`; skips are opt-in Mininet/OVS integration checks |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | Not clean: 46 existing files report formatting differences; changed Python test file passes format check |
| `uv run pyright` | Not clean: 181 diagnostics across the configured `mininet_ai` and `tests` scope |
| `uv run pyright mininet_ai` | Not clean: 8 diagnostics in compiler/experiment source |
| Maintained Example CLI validation/planning | iperf-throughput valid (11 resources, 2 agent instances); diamond-recovery valid (25 resources, 1 agent instance); both planned successfully |

## Explicitly unverified live gaps

The complete live-network handoff and blocking verdict are recorded in the
[live-network evaluation report](bounded-decision-live-network-evaluation.md).

- Pinned llama.cpp build/server behavior, full artifact SHA-256 verification,
  native choice correctness, generated-token behavior, and inference latency.
- Live Mininet/OVS action behavior, recovery for either failed primary link,
  five-second bidirectional active-path recovery, reset and cleanup verification.
- Remote termination of inference after local timeout/cancellation and isolation
  of a still-running remote request.
- Hardware, server launch/build identity and other provenance not observable
  from the service.

These gaps block live-support claims. Do not label scripted inference as model
quality, offline checks as live validation, or local cancellation as remote
termination.

The pinned live-candidate attempt is separately recorded as
[UNVERIFIED / BLOCKED](bounded-decision-live-model-evaluation.md); no live samples
or latency results were collected.
