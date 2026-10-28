# Bounded-decision execution (incremental)

Bounded decisions are a distinct execution kind, not a model-provider alias.
They use native llama.cpp `POST /v1/systemone` choice inference and map exact
scenario-owned choice IDs to fixed proposals. They do not use chat completion,
token generation, Agno, or hidden conversation memory. Mininet remains responsible
for capability authorization and all network mutation.

The first runtime tracer bullet is intentionally limited to successful
`no_action`. A selected `install_recovery` currently fails with
`agent.bounded-decision.choice-not-supported` before any proposal reaches the
capability executor. This is a hard failure, never a successful no-action result.
Do not claim cadence/deadline safety, recovery support, or live-model/network
validation on the basis of this incremental implementation.

The native request contains the retained canonical context string in `state`
and exactly one `questions.next_action` choice question. Choice IDs and criteria
must be in the same stable order. The response parser rejects duplicate JSON
keys, wrong question/type, and unknown choices; diagnostics are optional but
validated when present. A successful no-action result has no proposals.

Bounded evidence uses `mininet-ai/bounded-decision/v1alpha1` and normalized
execution records use `mininet-ai/agent-execution/v1alpha1`. Agno run/session IDs
and metrics are represented as unavailable, not synthesized. Model/server
provenance that cannot be observed must remain unverified; offline mechanics
tests do not establish a model-quality or latency claim.
