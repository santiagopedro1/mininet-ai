# Bounded-decision execution (incremental)

Bounded decisions are a distinct execution kind, not a model-provider alias.
They use native llama.cpp `POST /v1/systemone` choice inference and map exact
scenario-owned choice IDs to fixed proposals. They do not use chat completion,
token generation, Agno, or hidden conversation memory. Mininet remains responsible
for capability authorization and all network mutation.

**Initial slice (amended #116):** one current-only local diagnosis agent per
switch, `healthy` / `degraded` choices, and no action proposals. The Maintained
Example declares a 5-second operational deadline, not a universal model-latency
gate. Recovery/history behavior described below is historical regression
coverage, not the initial live acceptance target. Deployed-runtime acceptance
remains blocked; see the [final review](implementation/bounded-decision-final-review.md).

The initial runtime tracer bullet was limited to successful `no_action`.
`install_recovery` now maps only to the compiled scenario's exact ordered
proposals and runs through Mininet's normal capability authorization, conflict
arbitration, admission, execution, and completion lifecycle. Native output can
select a choice only; supplied model action arguments are rejected. Low
probability/confidence values are retained as diagnostics and never gate a
valid recovery choice.

The fixed bundle stops at the first rejection or execution failure. Evidence
retains ordered action outcomes and mutation effects; partial or unknown effects
are not reported as successful recovery or rolled back. Before a retry, runtime
reconciliation verifies the exact scenario-owned rules and retries only safely
known missing steps under fresh recovery eligibility and authorization. Unknown,
conflicting, unreadable, or otherwise unsafe state is not retried. Scheduled
bounded decisions carry the scenario-declared positive operational deadline,
measured from their original interval tick through inference and each
action-start check. This deadline is not a universal model-latency acceptance
threshold. Ticks received while that bounded lifecycle is active are
dropped/coalesced rather than queued for catch-up. Already-started actions keep
their configured action timeout and are not rolled back.

Deadline expiry prevents a late local response from becoming an admitted action
and prevents later bundle steps from starting. Call
`ExperimentRuntime.cancel_bounded_decision(agent_id)` to revoke local action
authority. The HTTP transport makes a best-effort local client-close request;
custom transports may provide their own `cancel_request` hook. A late response
may still arrive and is retained as discarded evidence, and already-started
actions continue under their action timeout with effects recorded. These local
operations do not establish cancellation or termination at llama.cpp: remote
terminal status remains unknown unless independently verified. Live-model/network
validation and accepted live-support claims remain outside this implementation.

The native request contains the retained canonical context string in `state`
and exactly one `questions.next_action` choice question. Choice IDs and criteria
must be in the same stable order. The response parser rejects duplicate JSON
keys, wrong question/type, and unknown choices; diagnostics are optional but
validated when present. `no_action` has no proposals; `install_recovery` has
only the scenario-owned fixed bundle.

Bounded evidence uses `mininet-ai/bounded-decision/v1alpha1` and normalized
execution records use `mininet-ai/agent-execution/v1alpha1`. Agno run/session IDs
and metrics are represented as unavailable, not synthesized. Model/server
provenance that cannot be observed must remain unverified; offline mechanics
tests do not establish a model-quality or latency claim.

Published evidence schemas require nested scenario/configuration, exact request,
provenance status, phase timing, proposal mapping and admission/authorization
fields. Bounded execution records require a `runtime.boundedDecision` payload;
empty successful records are invalid. Failed records retain invalid native
responses or bounded HTTP body diagnostics rather than coercing them to no
action. An absent response requires explicit evidence gaps. The execution
schema references `urn:mininet-ai:schema:bounded-decision:v1alpha1`; register both
packaged schemas with a local JSON Schema registry rather than attempting a
network lookup. Historical saved plans/evidence are not rewritten by validation.

## Authoring and saved plans

Use `mininet-ai/v1alpha5` for the experiment and blueprint. Select the execution
kind on the blueprint; a `llamacpp-systemone` model alone does not select it:

```yaml
apiVersion: mininet-ai/v1alpha5
kind: AgentBlueprint
metadata: {name: decision-maker}
implementation: {type: declarative}
execution: {kind: bounded-decision, scenario: switch-diagnosis}
model:
  provider: llamacpp-systemone
  name: expected-served-model
  parameters:
    endpoint: http://127.0.0.1:8080
    expectedIdentity:
      repository: ggml-org/Kev-4B-GGUF
      revision: f4624b6bb7072f1bab726a622c1cad3f96030b9e
      artifact: Kev-4B-Q8_0.gguf
      sha256: 7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da
```

The named `DecisionScenario` can be inline under `decisionScenarios` or
referenced from a separate versioned document. Current-only diagnosis uses
`inputMode: current-only`, instructions, two named diagnosis choices with empty
proposal lists, named current observation bindings, empty `paths`/`policy`, and
positive scenario timing. Per-target deployment instantiates one agent per
switch with a 1-second interval trigger and concurrency one per agent.
`decisionInputs` maps logical inputs to observations declared in `observe`.
Compilation embeds the resolved scenario and effective model configuration in
the immutable plan snapshot. The example binds `portState` to `ovs.port-state`
(up/down/unknown, not traffic counters). Its `localExpectations` declares each
switch's required local ports and exact forwarding entries; only the current
target's expectations enter a model request. Expected labels are evaluator-side
analysis, not a deterministic replacement for inference.

Historical recovery scenarios instead use `no_action` / `install_recovery`,
fixed proposals, paths/policy, and the three-round `pathProbes`, `linkStates`,
`policyState`, and `actionOutcomes` bindings. They are not the amended initial
example or live-support target.

Compile and save with `mininet-ai plan experiment.yaml --format json --output
plan.json`; `mininet-ai run plan.json` loads the saved DeploymentPlan directly,
without rereading scenario source. Python callers can use
`compile_experiment`, `save_deployment_plan`, and `load_deployment_plan` from
`mininet_ai.compiler`. `ExperimentRuntime` accepts an injectable
`decision_transport` for offline execution tests; production uses the bounded
HTTP transport.

Successful no-action evidence is retained in the normalized response and the
existing audit/ledger lifecycle. It identifies the bounded execution kind and
choice, retains the exact canonical request and native response, records
identity verification and timing, and explicitly lists unavailable scheduled
round history, timestamps, artifact verification, or server/hardware provenance.
Those gaps are not acceptance passes. Historical v1alpha3/v1alpha4 source
continues to validate under its existing rules and newly compiled plans are
v1alpha5; already-saved historical plans are loaded without snapshot or digest
rewrites.

## Offline acceptance boundary

The integrated offline acceptance and its exact check results are tracked in
[`implementation/bounded-decision-offline-acceptance.md`](implementation/bounded-decision-offline-acceptance.md).
Scripted native transport and the Fake Substrate establish the execution
contract, not candidate-model quality. No live llama.cpp inference, live network
recovery, latency target, or remote inference termination is established by
these tests.
