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

These invocation rules belong to this workflow. System One and System Two name
decision-making approaches, not placement or authority; both agents in this
example are scoped to `s1`. See [decision-making approaches](../decision-making.md).

Rejected alternatives were a core runtime threshold (larger contract and evidence
change), a transport-only response rewrite (would hide the original model choice),
and automatic escalation after failed local repair (would obscure a wrong System
One decision). The unified example is additive; the focused recovery and
escalation examples remain separate maintained examples.
