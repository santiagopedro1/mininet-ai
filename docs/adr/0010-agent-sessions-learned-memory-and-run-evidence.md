---
status: accepted
---

# Separate Agent Sessions, Learned Memory, and Run Evidence

Keep run-qualified Agent Sessions, explicitly scoped Learned Memory, and retained
Run Evidence as distinct concepts. Reuse learning across runs only when requested,
and record execution identities and effective memory settings without treating
the mutable Agno database as the experimental record. This permits deliberate
learning reuse without silently making every experiment share agent history.

This retrospective recovers the opt-in cross-run rationale from commit `a2158eb`
and the mutable-memory/evidence distinction from `c9bd220`, both in the historical
Agno record identified in [ADR 0007](0007-agno-runtime-and-action-authority.md).
Current identity and storage consequences, including operator responsibility for
isolation, were reaffirmed during this documentation discussion.

## Identity and storage boundaries

| Concept | Identity or boundary |
| --- | --- |
| Agent Session | `<run-id>:<compiled-agent-id>` |
| Agno execution | Mininet invocation ID |
| Run-Scoped Memory | Learned-memory user identity equals the session ID |
| Agent-Scoped Learned Memory | Learned-memory user identity equals the compiled agent ID in the selected store |
| Shared Operational State | Separate Mininet-owned run/deployment namespaces |
| Run Evidence | Retained inputs, provenance, activity, measurements, outcomes, and known gaps |

Compiled agent IDs derive from deployment and placement identities, not model,
instructions, experiment name, or plan digest. With the same store and compiled
ID, Agent-Scoped Learned Memory can survive model/instruction changes and cross
experiment boundaries. Operators must select a separate store when independent
learning populations are required; changing a model is not a memory reset.

Agno uses one database for sessions and learning. By default, any request for
Agent-Scoped Learned Memory selects `<artifact-root>/memory/agno.sqlite3` for the
Agno store; otherwise it is run-local. Sessions remain run-qualified even in the
shared store. Explicit database selection overrides this default; other storage
roots are not automatically searched or merged, and legacy stores require
explicit selection. Shared Operational State and ledgers remain separate.

## Evidence and accepted trade-offs

Record effective memory settings, session identities, and normalized Agno version
and execution measurements in Mininet's records. Run Evidence is not a promise of
deterministic replay, a tamper-proof record, or exact reconstruction of mutable
learned-memory inputs. Exported evidence excludes Agno stores and raw SQLite;
export completion, evidence completeness, and experiment success are distinct.
See [run storage and export](../run-logs.md#bundle-format-version-1).

Failed actions or state commits do not rewind conversation history or learning
already updated during reasoning. Neither those updates nor evidence writes form
one transaction with network actions; see
[ADR 0008](0008-shared-operational-state-and-action-commitment.md). Access to the
cached Agno instance has the narrower serialization limits in
[ADR 0009](0009-invocation-budgets-and-session-serialization.md).

Always sharing learning would contaminate independent runs; always isolating it
would prevent deliberate cross-run learning. Versioning learned identity by every
model/instruction change or splitting Agno session and learning stores would be a
different design, not a guarantee supplied here. These comparisons explain the
retained design without claiming a historical alternatives discussion.

Use the [glossary](../../GLOSSARY.md) for domain meanings. Implementation references:
[Agno identities](../../mininet_ai/agents/agno/provider.py),
[compiled identities](../../mininet_ai/compiler/compiler.py),
[storage defaults](../../mininet_ai/artifacts.py), and
[CLI store selection](../../mininet_ai/cli.py).
