---
status: accepted
---

# Agno runtime and Mininet action authority

Use Agno as the only supported production AI runtime through v1, while Mininet AI
owns experiment execution and network action authority. Maintaining a parallel,
framework-neutral AI stack duplicates Agno without a concrete second runtime;
reconsider that abstraction only when a real second production runtime requires it.

This is a retrospective record of the implemented architecture, reaffirmed during
the documentation discussion. It recovers the runtime choice from historical
`0001-use-agno-as-v1-agent-runtime.md`, introduced in commit `a2158eb` and deleted
in `0e57544`. That historical filename is not the current
[ADR 0001](0001-link-failure-connectivity-recovery.md). No existing ADR is renumbered.

## Ownership and alternatives

- Agno owns agent/model execution, Agent Sessions, conversation history, local
  state, Learned Memory, summaries, and AI usage metrics. Declarative agents and
  trusted Python factories use Agno; model selection is not selection of another
  agent runtime.
- Mininet owns specifications, compilation, Substrate lifecycle, event delivery,
  scheduling, capability authorization, action execution, Shared Operational State,
  and Run Evidence. Agno responses become structured proposals; the capability
  engine checks identity, scope, assigned capability, target, and schemas before
  executing supported actions.
- Agno objects stay behind the runtime seam. Mininet normalizes identities,
  responses, usage, and outcomes into its own records rather than making those
  contracts depend on Agno objects.

The rejected alternative is a speculative framework-neutral agent/model execution
layer maintained alongside Agno. Existing deprecated protocols and registries do
not constitute a second supported production path. Capability extensibility
remains supported and does not require reviving that AI abstraction.

## Consequences and limits

This accepts Agno coupling in exchange for one maintained AI execution stack.
The supported proposal path preserves Mininet's action authority, but it is an
architectural boundary, not a security sandbox. Trusted Python factories and
their tools execute code in the orchestrator; placement declarations do not
provide process isolation, and timeouts do not universally terminate arbitrary
Python code. Do not interpret this record as hostile-code containment.

The historical record's later state-before-action wording is obsolete. Current
commitment ordering is recorded in
[ADR 0008](0008-shared-operational-state-and-action-commitment.md), concurrency in
[ADR 0009](0009-invocation-budgets-and-session-serialization.md), and persistence
boundaries in [ADR 0010](0010-agent-sessions-learned-memory-and-run-evidence.md).

Implementation references: [agent runtime](../../mininet_ai/agents/runtime.py),
[Agno integration](../../mininet_ai/agents/agno/provider.py), and
[capability engine](../../mininet_ai/capabilities/engine.py).
See [compatibility and versioning](../compatibility.md) for deprecated API removal.
