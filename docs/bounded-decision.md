# Bounded-decision execution (incremental)

Bounded decisions are a distinct execution kind, not a model-provider alias.
They use native llama.cpp `POST /v1/systemone` choice inference and map exact
scenario-owned choice IDs to fixed proposals. They do not use chat completion,
token generation, Agno, or hidden conversation memory. Mininet remains responsible
for capability authorization and all network mutation.

The initial runtime tracer bullet was limited to successful `no_action`.
`install_recovery` now maps only to the compiled scenario's exact ordered
proposals and runs through Mininet's normal capability authorization, conflict
arbitration, admission, execution, and completion lifecycle. Native output can
select a choice only; supplied model action arguments are rejected. Low
probability/confidence values are retained as diagnostics and never gate a
valid recovery choice.

The fixed bundle stops at the first rejection or execution failure. Evidence
retains ordered action outcomes and mutation effects; partial or unknown effects
are not reported as successful recovery or rolled back. Reconciliation and safe
retry are not implemented. Scheduled bounded decisions carry one run-owned
monotonic 800 ms deadline from their original interval tick through inference
and each action-start check. Ticks received while that bounded lifecycle is
active are dropped/coalesced rather than queued for catch-up. Already-started
actions keep their configured action timeout and are not rolled back.

Deadline expiry prevents a late local response from becoming an admitted action
and prevents later bundle steps from starting. Local cancellation/discard does
not establish cancellation or termination at llama.cpp: remote terminal status
is unknown unless independently verified. No general transport cancellation,
live-model/network validation, or accepted live-support claim is provided by
this incremental implementation.

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

## Authoring and saved plans

Use `mininet-ai/v1alpha5` for the experiment and blueprint. Select the execution
kind on the blueprint; a `llamacpp-systemone` model alone does not select it:

```yaml
apiVersion: mininet-ai/v1alpha5
kind: AgentBlueprint
metadata: {name: decision-maker}
implementation: {type: declarative}
execution: {kind: bounded-decision, scenario: diamond-policy}
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

The named `DecisionScenario` supplies `instructions`, exactly ordered
`no_action` / `install_recovery` choices, paths, observation declarations,
fixed timing, and inspectable policy. It can be inline under
`decisionScenarios` or referenced from a separate versioned document. The agent
deployment declares one singleton instance, one 1-second interval trigger, the
required capabilities, and `decisionInputs` mapping `pathProbes`, `linkStates`,
`policyState`, and `actionOutcomes` to observations it declares in `observe`.
Compilation resolves those references and embeds the scenario and effective
model configuration in the immutable plan snapshot.

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
