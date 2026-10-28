---
status: accepted
---

# Explicit bounded-decision execution

Supersede ADR 0007's Agno-only restriction with an explicitly selected,
stateless bounded-decision execution path alongside Agno. Omitted execution
selection continues to mean Agno. Model identity or provider configuration must
never silently choose execution semantics.

Mininet continues to own scheduling, observations, capability authorization,
action execution, Shared Operational State, and Run Evidence. Bounded inference
uses the native llama.cpp decision interface and returns only a named choice
that Mininet maps to scenario-owned capability proposals. It never creates an
Agno object, session, or fabricated Agno metrics. Agno factories and hooks remain
Agno-only. ADRs 0008–0010 remain in force without change.

This decision authorizes an incremental implementation. The initial tracer
bullet supports the complete author/validate/compile/save/reload/run path for a
successful `no_action` choice. The `install_recovery` branch is explicitly
rejected before mutation until its complete admission, reconciliation, and
partial-effect lifecycle is implemented. This is not a live-model or live-
network support claim; skipped or unverified evaluation remains unverified.

## Consequences

- New execution behavior requires an explicit bounded-decision discriminator;
  historical and omitted selection retain Agno behavior.
- Bounded request and normalized execution records are versioned separately
  from Agno-specific records and do not manufacture Agno identities.
- Existing scheduling and capability/action ownership are reused. No chat
  completion, token generation, automatic fallback, or model substitution is
  introduced.
