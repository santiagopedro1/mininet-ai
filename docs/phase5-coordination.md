# Phase 5 coordination runtime

## Status

Implemented on `feature/v1alpha2-coordination-runtime`. The architecture,
ownership seams, compatibility gates, and acceptance matrix below describe the
canonical Phase 5 runtime delivered by this branch. Phase 5 is closed; process,
namespace, container, and sidecar placement remain Phase 6 work.

## Objective

Execute the canonical coordination graph already present in a deployment plan.
Mininet AI routes intents and messages, bounds delivery, invokes agents, and
arbitrates concurrent network actions. Agno remains responsible for agent and
model execution, sessions, memory, and usage metrics.

The implementation must support these compiled graph shapes:

- **independent:** a submitted intent invokes its named agent directly;
- **centralized:** the singleton coordinator receives external intents and may
  delegate work along `coordinates` edges;
- **hierarchical:** a parent may delegate work along directed `parent` edges;
- **distributed:** a peer may send work along directed `peer` edges.

An invocation result travels back to its caller as a correlated result. Return
delivery is not a new graph edge and does not grant the callee permission to
initiate unrelated work against the caller.

## Ownership

| Concern | Owner |
| --- | --- |
| Canonical graph, routing authorization, message delivery, conflict policy | Mininet AI |
| Network scope, capability authorization, effects, verification, rollback | Mininet AI |
| Agent construction, reasoning, sessions, history, memory, model metrics | Agno |
| Serializable invocation, message, decision, action, audit, and ledger records | Mininet AI |
| Agno team or workflow objects | Agno adapter implementation only |

Agents continue to propose structured work. They never receive a substrate
handle or execute a network mutation directly.

## Module shape

The coordination runtime is a deep module between continuous event scheduling
and agent execution. Its external seam accepts one normalized input and returns
one normalized outcome:

```python
class Coordinator(Protocol):
    def coordinate(self, request: CoordinationRequest) -> CoordinationOutcome: ...
```

`CoordinationRequest` identifies the run, entry agent, intent, triggering event,
and correlation identity. `CoordinationOutcome` contains the ordered invocation,
message-delivery, arbitration, and action results for that request.

Callers do not traverse edges, manage per-agent message queues, select an Agno
team, or apply conflict policy. Those details stay inside the module. Lifecycle
and global resource ownership remain with the enclosing experiment runtime.

Internal seams are justified only where behavior already varies:

- an **agent executor** adapter invokes one compiled agent and returns structured
  proposals without committing network effects;
- a **message channel** adapter provides bounded delivery; the first adapter is
  deterministic and in-memory, while a persistent adapter may be added when
  process isolation requires it;
- an **action executor** adapter authorizes and commits an admitted proposal by
  reusing the capability engine.

Graph lookup, route validation, duplicate suppression, hop limits, arbitration,
and result assembly are implementation details of the coordination runtime.

## Invocation and action split

The current one-shot runtime reasons and commits actions in one method. Phase 5
must separate those steps without weakening the capability seam:

1. Build the scoped `AgentContext` and invoke Agno.
2. Apply authorized shared-state updates using their existing optimistic
   concurrency rules.
3. Return message and action proposals to the coordination runtime.
4. Validate a proposal's agent, capability, target, schema, and timeout before
   it enters arbitration.
5. Arbitrate conflicting admitted proposals.
6. Commit each winner through the capability engine, including postcondition
   verification and rollback.
7. Record action results back into the originating Agno session.

The existing one-shot interface remains as a compatibility facade over these
steps until all callers use coordination. There must be one authorization and
commit implementation, not parallel Phase 4 and Phase 5 paths.

## Message model

Messages are immutable Mininet-owned envelopes. A versioned envelope needs at
least:

- message, run, correlation, and causation identifiers;
- source and destination agent instance identifiers;
- kind: `intent`, `delegation`, or `result`;
- structured payload and creation time;
- hop count and the traversed message identifiers needed for loop detection;
- optional triggering event and parent invocation identifiers.

An agent response may propose zero or more outbound messages. Mininet validates
every destination against the compiled directed graph before enqueueing it.
Unknown agents, undeclared edges, cross-run identities, duplicate message IDs,
and exhausted hop limits are rejected with typed issues and audit records.

Delivery is at-most-once within the first in-memory implementation. Acceptance
into a queue and completion are distinct records. Shutdown either drains an
accepted message or records its cancellation; it must not silently lose it.
Queue capacity is bounded by the deployment plan's existing
`maxQueuedEvents` resource limit until a distinct public message limit is shown
to be necessary.

### Entry routing

- Independent mode delivers an external intent to the named entry agent.
- Centralized mode always delivers an external intent to the compiled
  coordinator. The originally requested agent remains part of the payload so
  the coordinator can delegate deliberately.
- Hierarchical mode accepts an intent at its named entry agent. Further work
  may only move from a parent to a declared child.
- Distributed mode accepts an intent at its named entry peer. Further work may
  only move over a compiled directed peer edge.

The runtime never infers a multi-hop route on behalf of an agent. Each hop is a
new, explicit message proposal, which keeps delegation observable and prevents
graph connectivity from becoming implicit authority.

## Conflict arbitration

Mininet AI owns arbitration because only it has the compiled effects, scoped
targets, active commits, and experiment policy.

Two proposals conflict when they address the same target and their declared
capability effect sets intersect. This is deliberately conservative: it may
serialize independent changes within one effect domain, but it will not allow
two potentially incompatible mutations merely because their arguments differ.
Non-mutating capabilities with no declared effects do not conflict.

Admission assigns a monotonic sequence number under one lock. The existing
policy values have these runtime meanings:

- `reject`: the first admitted proposal wins; conflicting pending proposals are
  rejected with `coordination.conflict.rejected`;
- `serialize`: conflicting proposals commit in admission order;
- `priority`: the highest compiled agent priority among pending conflicts
  commits first, then admission sequence, agent ID, and proposal ID break ties.

An active action is never preempted. Priority orders pending work; it does not
attempt to undo a mutation already being verified or rolled back. Locks cover
conflict keys, not whole invocations, so nonconflicting actions may proceed up
to the experiment concurrency limit.

Every decision records the candidates, conflict keys, policy, ordering inputs,
winner, and disposition. Rejected proposals still produce normalized action
results so callers and Agno session history see a complete outcome.

## Agno teams and workflows

The canonical deployment plan is authoritative. Initial acceptance uses direct
per-agent execution through the existing Agno provider so routing semantics can
be tested without depending on Agno orchestration internals.

An Agno adapter may later map a graph to a team or workflow only when it
preserves:

- compiled instance identity and per-agent session identity;
- directed routing permissions and explicit delegation records;
- scoped context, capabilities, and shared-state access;
- Mininet-owned proposal arbitration and action execution;
- normalized metrics, audit records, deadlines, and cancellation behavior.

Centralized and strictly hierarchical graphs are candidates for teams. A
workflow is appropriate only for an explicitly deterministic sequence; graph
shape alone does not invent ordering. Peer graphs remain Mininet-routed unless
an Agno adapter can demonstrate equivalent directed-message semantics.

### v1alpha2 eligibility decision

The Agno integration locked for this branch is Agno 3.0.11. Its native team
and workflow execution interfaces do not preserve the coordination runtime's
required seam for any current `v1alpha2` plan:

| Plan shape | Native candidate | Decision |
| --- | --- | --- |
| Independent | None | Use direct per-agent execution; no orchestration object is needed |
| Centralized | Team | Ineligible: a team invokes members outside the Mininet executor and introduces team-owned leader/session behavior |
| Hierarchical | Nested teams | Ineligible for the same executor/session reason, and nested delegation would not emit canonical Mininet messages |
| Distributed | Team | Ineligible: Agno team membership does not preserve the compiled directed peer graph |
| Any current plan | Workflow | Ineligible: the specification declares no deterministic step sequence |

Consequently, `v1alpha2` does not enable a native Agno team or workflow
adapter. The existing Agno agent provider remains behind the staged Mininet
executor, and the canonical coordination runtime remains the only routing and
commit path. This is an intentional safety decision rather than a missing
fallback: silently translating one of these graphs would bypass scoped
context construction, explicit delivery records, capability admission, or
Mininet-owned arbitration.

A future native adapter requires a second, demonstrably equivalent
implementation at the existing `Coordinator.coordinate` seam. It must pass the
same acceptance matrix without adding synthetic agent identities or exposing
Agno objects through Mininet-owned contracts. Workflow support additionally
requires an explicit ordering construct in a future specification revision;
graph topology alone remains insufficient.

## Failure and shutdown semantics

- A failed agent invocation returns a correlated failure and does not erase
  earlier delivery or arbitration records.
- A failed child or peer does not automatically retry at another agent.
  Supervision applies the compiled restart policy to the intended recipient.
- Queue overflow follows the recipient's compiled overflow policy and records
  rejection, replacement, or coalescing explicitly.
- A message chain is bounded by a hop limit derived from the compiled graph and
  rejects repeated causation paths. Peer cycles are legal graph structure but
  do not permit unbounded delivery.
- Runtime stop closes producers first, then drains or cancels accepted messages
  within the caller's deadline, waits for action verification or rollback, and
  persists a terminal report.
- Restart does not replay at-most-once in-memory messages. The immutable ledger
  shows what was accepted and what completed; durable replay is future work and
  requires an explicit delivery contract.

## Contract and compatibility gates

The existing `mininet-ai/v1alpha2` experiment and deployment plan already carry
all four coordination modes and their expanded edges. The first runtime slice
must consume that plan without changing its serialized shape, snapshot, or
digest.

Adding outbound message proposals changes serialized agent-runtime models and
therefore requires a new agent-runtime contract version. Adding coordination
event or report variants may likewise require a runtime-event or ledger
contract revision. Those bumps belong in the implementation commit that adds
the affected models, with migration notes and focused schema tests.

Any new experiment field, deployment-plan field, changed edge expansion, or
changed meaning for an existing compiled edge requires a public specification
compatibility review under `docs/compatibility.md`. No golden plan is updated
merely to make an unexplained diff pass.

## Delivered sequence

1. Resolved and validated executable graphs from the existing deployment plan.
2. Introduced versioned messages and a bounded in-memory channel.
3. Routed external intents and explicit delegations through the graph.
4. Split proposal generation from capability commit and added arbitration.
5. Evaluated Agno team and workflow adapters and kept them disabled because no
   Agno 3.0.11 mapping preserves the required execution and safety seam.
6. Added rootless and live Mininet examples, acceptance tests, verbose logging,
   immediate fatal-error reporting, and automatic completion after submitted
   intents finish.

Each delivered slice retained direct Phase 4 invocation compatibility and
deterministic rootless coverage on the fake substrate.

## Acceptance matrix

| Scenario | Required evidence |
| --- | --- |
| Independent | Named agent receives the intent directly; no synthetic message hop |
| Centralized | Coordinator receives every external intent and can delegate only to compiled targets |
| Hierarchical | Parent-to-child delegation succeeds; child-to-parent initiation is rejected |
| Distributed | Declared peer delivery succeeds; non-neighbor delivery is rejected |
| Correlation | Intent, delegations, invocations, decisions, and results share one traceable correlation chain |
| Conflict reject | Exactly one conflicting proposal commits and every loser receives a typed rejection |
| Conflict serialize | Conflicting effects commit in stable admission order |
| Conflict priority | Highest pending priority wins; ties resolve deterministically |
| Parallel actions | Nonconflicting effects execute concurrently within the global limit |
| Capability safety | Every committed action passes scope, schema, effect, timeout, verification, and rollback checks |
| Queue pressure | Overflow behavior matches policy and every disposition is reported |
| Loop defense | Cyclic peer proposals terminate at duplicate or hop-limit enforcement |
| Failure | Agent, delivery, action, verification, and rollback failures remain distinguishable |
| Shutdown | Accepted work drains or records cancellation, resources release, and the next run starts cleanly |
| Agno eligibility | A native adapter is enabled only after proving equivalence; otherwise canonical routing remains mandatory |

## Acceptance evidence

The rootless end-to-end scenario is
`tests/acceptance/test_phase5.py`, backed by the application fixture under
`tests/fixtures/phase5/`. It proves event correlation, centralized entry
routing, two explicit delegations, capability admission, one committed
conflicting action, one typed rejection, complete arbitration evidence, and
orderly teardown through the public experiment owner. The same test compiles
the deterministic and Ollama-backed live Mininet fixtures.

The remainder of the acceptance matrix is covered at the owning module
interface:

- `tests/coordination/test_graph.py` covers independent, centralized,
  hierarchical, and topology-neighbor peer graphs, including invalid shapes;
- `tests/coordination/test_messages.py` covers versioned envelopes, bounded
  delivery, duplicate defense, closure, and public schemas;
- `tests/coordination/test_routing.py` covers directed delegation, queue
  pressure, peer-loop termination, pre-arbitration admission, conflict
  rejection, and concurrent nonconflicting commits;
- `tests/coordination/test_arbitration.py` covers deterministic `reject`,
  `serialize`, and `priority` policy semantics;
- `tests/runtime/test_experiment.py` covers continuous event integration,
  correlation retention, centralized entry routing, and shutdown ownership;
- `tests/test_cli_runtime.py` covers verbose progress and log output, immediate
  failure visibility and nonzero termination, and automatic stopping after all
  submitted intents reach terminal coordinated outcomes;
- `tests/capabilities/test_engine.py` retains the authorization, timeout,
  verification, and rollback safety evidence used by every admitted action.

Native Agno team/workflow equivalence is not claimed for `v1alpha2`; the
eligibility decision above keeps those paths disabled until they can satisfy
the same matrix.

## Non-goals

- placement into separate processes, namespaces, containers, or sidecars;
- durable cross-process messaging or exactly-once delivery;
- dynamic graph mutation during a run;
- agent-created capabilities or widened network scope;
- implicit broadcasts, automatic consensus, or automatic failover routing;
- framework-neutral orchestration alongside Agno.
