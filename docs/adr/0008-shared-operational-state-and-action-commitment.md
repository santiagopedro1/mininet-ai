---
status: accepted
---

# Shared Operational State follows successful actions

Use versioned, declared dependencies to reject stale coordinated decisions, and
publish a decision's Shared Operational State updates atomically only after all
associated actions and enabled postconditions succeed. Retain concurrency between
independent decisions rather than globally serializing reasoning and execution.
This avoids publishing coordination state for actions that have not succeeded,
without pretending network effects and a state database form one transaction.

This retrospective documents current behavior. Commit `b5fd534` recorded the
action-before-state ordering and the absence of arbitrary external rollback; the
trade-off for retaining that design was reaffirmed during this documentation
discussion. It replaces the state-before-action description added in `5f8d016` to
the deleted historical Agno record identified in [ADR 0007](0007-agno-runtime-and-action-authority.md).

## Decision boundary

- Shared Operational State belongs to Mininet, with run/deployment namespaces,
  not to Agno's Agent Sessions or Learned Memory.
- Updated keys are dependencies automatically. Agents must declare other snapshot
  keys they rely on in `sharedStateReads`, including dependencies on absence.
  Omitted update versions bind to the invocation snapshot; newer state cannot be
  silently adopted by supplying a different expected version.
- Coordinated execution validates dependencies and reserves incompatible declared
  reads/writes after reasoning, retaining reservations through action execution,
  verification, and completion. Unrelated keys can proceed concurrently.
- Failed or rejected actions publish no shared updates. A state-only decision can
  commit without network actions. The update batch is atomic in the state store.

## Accepted limits and alternatives

These guarantees cover declared dependencies within one coordinator, not a
serializable transaction across arbitrary callers or processes. Reservations are
in-process coordination, not database locks held during network execution.
Standalone one-shot invocation checks versions but does not itself supply the
coordinator's reservations. Undeclared reads and external writers are outside
that protection; complete read-set validation is not part of the store's atomic
update transaction.

Successful network effects may remain if another action fails or the final state
commit fails, for example because of capacity, storage failure, or an outside
writer. Preserve action results and report the failed/rejected commitment; any
reconciliation requires subsequent explicit work. Configured capability rollback
for certain failed postconditions is not general compensation for these cases.
Agent Session and audit writes are separate operations and can fail after actions
or shared updates succeed.

Global serialization would reduce overlap without making external effects atomic.
Last-writer-wins would hide stale decisions. General compensation would require
undo semantics that arbitrary capabilities do not provide. These alternatives
explain today's retained design; they are not claimed as a recovered historical
shortlist.

See [ADR 0009](0009-invocation-budgets-and-session-serialization.md) for invocation
lifetimes and [ADR 0010](0010-agent-sessions-learned-memory-and-run-evidence.md) for
the separate persistence boundaries. Implementation references:
[decision admission](../../mininet_ai/coordination/admission.py),
[coordinated execution](../../mininet_ai/coordination/routing.py),
[agent completion](../../mininet_ai/agents/runtime.py), and
[state store](../../mininet_ai/runtime/state.py).
