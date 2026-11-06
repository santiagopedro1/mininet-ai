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
