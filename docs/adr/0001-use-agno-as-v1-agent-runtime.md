---
status: accepted
---

# Use Agno as the v1 agent runtime

Mininet AI will be Agno-centric through its alpha releases and planned v1: Agno owns agent and model execution, sessions, conversation history, agent-local state, learned memory, summaries, and AI usage metrics, while Mininet AI owns experiment specifications, topology and substrate lifecycle, event delivery, scheduling, capability authorization, network action execution, shared operational state, and the reproducibility ledger. The framework-neutral agent and model provider layer introduced in Phase 3 is deprecated and will be removed before v1 because maintaining a second AI stack duplicates Agno without serving a concrete second runtime.

## Consequences

- Agno is the only supported production AI runtime for v1. Python-authored agents expose Agno agents or factories rather than implementing a Mininet-specific agent provider protocol.
- Agno objects stay inside the agent-runtime module. Mininet contracts such as invocation context, structured action proposals, action results, events, and normalized ledger measurements remain independent of Agno.
- Agents never mutate the network directly. Agno produces structured proposals, and Mininet's capability engine remains the sole authorization and execution seam.
- Agno is the source of model token, cost, and timing measurements. Mininet normalizes those measurements into its ledger so they can be correlated with network events and action outcomes.
- Conversation history, summaries, local session state, and learned memory use Agno. Cross-agent operational state remains Mininet-owned, and memory shared across experiment runs is opt-in to avoid contaminating independent experiments.
- Framework neutrality will be reconsidered only when a concrete second production runtime is required. That implementation, rather than a speculative abstraction, will determine the future interface.
