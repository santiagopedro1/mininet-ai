# Keep the confidence gate in the unified runner, not the core decision runtime

The unified NOMS experiment needs one place where a locally capable System One
agent can either repair a randomly selected link or missing-flow fault, or hand
the incident to System Two when its confidence is below the configured threshold.
We keep that gate in the experiment runner, before local action preparation,
rather than adding a threshold policy to bounded-decision execution.

This is a deliberate deviation from the core rule that confidence remains
diagnostic-only. It keeps the engine free of scenario policy and avoids a schema
or evidence-version change. The trade-off is that the runner must retain both the
raw native response and the effective, gated response plus a gate record, so a
future reader can distinguish a model choice from a runner replacement. A failed
local repair does not automatically trigger System Two; escalation happens only
through an explicit `escalate` choice or the gate.

Rejected alternatives were a core runtime threshold (larger contract and evidence
change), a transport-only response rewrite (would hide the original model choice),
and automatic escalation after failed local repair (would obscure a wrong System
One decision). The unified example is additive; the focused recovery and
escalation examples remain separate maintained examples.

## Four-treatment campaign amendment (2026-11-06)

The paper's approved four-treatment recovery specification supersedes the pilot's
terminal-local-failure choice **for the new campaign**, not for these preserved
examples. Combined campaign treatments must escalate on inability, unsuccessful
action/service verification or S1 timeout, sharing a common deadline; the gated
variant additionally gates before local action. Direct S2 must not wait for S1.
Those combined/S2 extensions are not implemented by ticket 01.

Ticket 01 adds a separately maintained configuration-to-evidence campaign runner
at `examples/noms27/recovery_campaign/` for **S1-only**, one persistent `s1-s2`
outage and one frozen local bypass attempt. It has no confidence gate, escalation,
port-enable repair or retries. Service verification is independent sequenced UDP,
not the pilot's ping rounds. Policy stays in this runner and uses the existing
native S1 client; core bounded-decision contracts and older examples are unchanged.
See [implementation and limitations](../recovery-01-implementation.md). This is
not a claim that the pilot is compliant or that campaign feasibility is approved.

Ticket 02 adds **direct S2-only** to this separate campaign runner, without
changing the preserved unified example or core runtime. It starts from the same
local diagnostic snapshot and a generic operational alarm, not an S1 decision or
handoff. A separate Ollama-compatible structured-response path receives broader
switch telemetry and proposes typed, forwarding-only multi-switch repairs. The
runner permits at most three observe–act–verify attempts within the original
common deadline; it never enables an interface, changes traffic or sends evaluator
fault labels. The S1 stage cap is irrelevant to direct S2. Combined invocation and
confidence gating remain later work; no reusable escalated policy is claimed yet.

The controlled multi-switch fixture establishes the offline mechanism, not an
extended-diamond live scenario or scientific acceptance. Ticket 01's live blocker
and all feasibility/approval gates remain. See
[ticket 02 implementation and limitations](../recovery-02-implementation.md).

Ticket 03 adds **combined ungated** to the campaign runner. One local attempt
precedes the same bounded S2 policy used by direct invocation; unsuccessful local
execution or service verification now requests escalation, rather than preserving
the pilot's terminal-failure behavior. Explicit inability, unusable decisions and
S1 stage expiry also request escalation. The original deadline never resets, and
successful local recovery does not invoke S2. Confidence alone is not a trigger;
native prompts, criteria and output fields remain unchanged.

The runner retains local observations, decisions, attempted actions, operational
verification and the escalation cause in a handoff. Only operational S1 events,
not evaluator injection/reset metadata, enter S2 context. It refuses handoff when
prior work is still outstanding or provider/action termination is unknown, even
if nominal time remains: local cancellation alone cannot justify overlapping
inference or conflicting repairs. This safety boundary can leave an incident
unsuccessful; it must not be disguised as successful escalation. Independent
observation continues to the common deadline plus five seconds in either case.

This is offline mechanism coverage, not live acceptance or calibrated confidence
gating. Preserved examples and core runtime contracts remain unchanged. See
[ticket 03 implementation and limitations](../recovery-03-implementation.md).
