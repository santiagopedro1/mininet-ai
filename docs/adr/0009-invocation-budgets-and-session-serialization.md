---
status: accepted
---

# Budget invocations and serialize persistent Agno access

Count executing agent invocations, including delegates, against global and
per-agent concurrency limits rather than charging entire delegation trees.
Allow Independent Agent Invocations to overlap, while serializing access to a
cached persistent Agno instance because its session and learned state are mutable.
This uses capacity for active work without making waiting ancestors consume the
slots their descendants need.

This retrospective records behavior introduced in commit `b5fd534`. Invocation
budgeting and the shared-mutable-state reason for session locking are already
documented in the [authoring reference](../yaml-authoring-reference.md#invocation-concurrency).
The precise limits below and the rationale for retaining them were reaffirmed
during this documentation discussion; they are not new scheduling guarantees.

## Invocation lifetime

- An invocation holds its budget through preparation, action admission/execution,
  verification, and completion, not just model reasoning.
- A parent's lease is released before scheduling descendants. Children start
  after successful parent completion and can observe eligible committed shared
  state. Waiting for descendants does not retain the parent's slot.
- Independent requests and sibling delegates may overlap. One global slot stays
  serial. Stateless agents with `maxConcurrency > 1` use invocation-specific Agno
  identities and can complete out of order.
- Conflicting actions and declared shared-state dependencies retain their own
  reservations; available invocation capacity does not bypass authorization or
  conflict admission. See [ADR 0008](0008-shared-operational-state-and-action-commitment.md).

## Serialization is not a whole-invocation transaction

For local, conversation, or learned memory, the factory reuses an Agno instance
and locks its execution. It releases that lock before network actions and
reacquires it separately for local action-result persistence. Another reasoning
call can therefore occur before an earlier invocation finishes actions or records
their outcomes; context construction also precedes lock acquisition.

The lock is per factory, not cross-process or database-wide. Waiting for it
consumes invocation capacity; the reasoning timeout begins inside the lock.
An arbitrary Python factory returning the same singleton does not acquire object
isolation merely because Mininet supplies invocation-specific identities.

Do not infer FIFO execution, deterministic completion, guaranteed visibility of
the immediately preceding action outcome, or starvation freedom. Priority orders
pending conflicts without preempting running work. Observations can see
intermediate network state.

Whole-tree budgets would charge inactive parents and constrain delegation. A
global serial executor would sacrifice independent overlap. Whole-invocation
session locking would offer a stronger ordering model but is not implemented and
must not be promised by this retrospective. These alternatives describe today's
trade-off, not an asserted historical evaluation.

See [ADR 0010](0010-agent-sessions-learned-memory-and-run-evidence.md) for persistence
identities. Implementation references: [routing](../../mininet_ai/coordination/routing.py),
[continuous scheduling](../../mininet_ai/runtime/continuous.py), and
[Agno instance access](../../mininet_ai/agents/agno/provider.py).
