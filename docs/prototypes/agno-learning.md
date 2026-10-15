# Throwaway complete-learning-boundary investigation

**Partial evidence, NOT architecture acceptance. Do not merge this branch.**

For [Validate complete learning-gate and strict persistence coverage](https://github.com/santiagopedro1/mininet-ai/issues/58),
under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).
Extends the session-only [integrated worker probe](agno-integrated.md); no production
behavior, schemas, migration policy, glossary or ADRs change. The question concerns
real SQLite behavior, so this is a runnable offline Python artifact, not an HTML model.

## Enumerated candidate boundary

This enumeration is a **proposal for human review**, not permission to narrow the
agreed contract. `ROUTES`, the fixed operation switch, `StrictDb`, and the scripted
fixtures define the tested surface; they do not discover or contain arbitrary Python.
The worker caches a distinct Agent configuration per identity/scope/route fixture,
with one execution-owner thread per compiled identity. It does **not** yet combine
every route into one universally configured cached Agent; cross-route volatile
session continuity is not established by this pass.

| Admitted route | Actual exercised path / limit |
| --- | --- |
| Automatic learning | Synchronous `Agent.run(update_memory_on_run=True)` and tracked executor completion, including foreground error with active learning |
| Agentic learning | `enable_agentic_memory=True` / `update_user_memory` tool; scripted manager add/update |
| Manager create/task | `create_user_memories`, `update_memory_task`; internal synchronous add/update tools |
| Direct manager mutations | `add_user_memory`, `replace_user_memory`, explicit scoped `delete_user_memory`, `clear_user_memories` (batch deletion) |
| Read/search | `get_user_memory`, manager reads via `get_user_memories`/`read_from_db`, `search_user_memories(last_n)`; expected missing reads are valid, unlike required writes |
| Optimization | `optimize_memories(apply=True)` with one fixed offline strategy preserving ownership; real delete-and-reinsert path. Not the stock summarization model/strategy or arbitrary strategies |
| Hooks | One fixed synchronous pre-hook and post-hook, calling the bound manager |
| Custom tool | One fixed synchronous memory-task tool registered with Agno, using the bound manager |
| Session-only reasoning | No memory access; independent owners meet a two-party reasoning barrier without the learning gate |

Explicitly **ineligible**, not validated equivalents: async APIs/async DBs,
streaming, Team/Workflow, AgentOS, alternate memory stores, agentic retrieval,
first-n search, direct bulk upserts/import/export/migration, raw SQL or underlying
engine access by extensions, global memory clear (`MemoryManager.clear` and the
internal global-clear tool), arbitrary hooks/tools/strategies, custom executors,
unregistered/detached threads/tasks/processes, Provider retries, and unaccounted
I/O. `clear_memories` is rejected; default manager task configuration leaves the
global-clear tool disabled. The model never selects it in this probe. Fixed wire
routes reject an unknown extension before dispatch. This is not a validation of
a production extension loader or a security sandbox.

All memory-capable operations take the worker-owned root flock before access and
retain it through all tracked futures, strict checks and terminal persistence.
Each compiled identity has a single-thread owner; independent session-only work
overlaps. A timeout with unfinished tracked futures parks the operation **holding
the gate** for controller containment, rather than releasing it. That timeout
branch is not induced here; deployment containment remains with
[Validate deployment worker startup and descendant containment](https://github.com/santiagopedro1/mininet-ai/issues/57).

Run-scoped identity is `trial:<compiled-agent>`; agent-scoped identity is the
compiled agent. Reads and mutations use an explicit scope. New IDs may be added;
existing IDs must belong to the current scope. Required deletion of a missing or
foreign target fails in this candidate; this is a deliberately strict fixture
choice, **not a chosen public deletion/idempotency policy**. Stock void-return
APIs cannot be checked by treating every `None` as failure: run rows and deletion
postconditions are read back instead. Postcondition equality is not general
proof that a no-op rewrite of an existing row occurred.

## Run

Reuse the isolated pinned interpreter from [the storage probe](agno-storage.md):

```bash
/tmp/opencode/agno-storage-prototype/venv/bin/python -P mininet_ai/agents/agno/learning_gate_prototype.py --scratch-parent /tmp/opencode/agno-learning-gate --report /tmp/opencode/agno-learning-rerun.json
```

The report destination must not already exist. Each invocation creates fresh,
clearly named disposable roots; it never opens production or prior run stores.
All model responses are scripts. No Provider clients, network calls, credentials,
deployment, production imports or provisioning are needed. Nonzero exit means an
experiment/check failed, not that the architecture has been rejected or accepted.

## Observed runtime evidence

The adjacent `agno-learning.observations.json` retains commands, replies, ordered
SQLite events, errors, final scoped memory, live/closed filenames, exits, timings,
package inventory and source hashes. Each group is a separate selected root,
not a design placing many DBs inside one root.

- All healthy enumerated routes completed, including actual Agno hook/tool
  execution. Two independent session-only operations passed the reasoning barrier;
  repeated same-scope reads retained the same cached Agent object and owner thread.
- Same-worker A/B memory operations had nonoverlapping admission-to-terminal
  intervals under the root gate. Repeated spawned-process control experiments
  sharing one agent scope lost a fact **without** the gate and retained both facts
  **with** it. These reuse the earlier synchronous manager race, not concurrent
  full long-lived worker protocol instances. No starvation/FIFO guarantee follows.
- Foreground error returned `RunStatus.error` while blocked automatic learning
  was active. Both a controller acquisition and another thread's acquisition were
  denied until learning finished. The final result failed after drain; the late
  learning fact remained committed. The first development run exposed that
  checking only raised exceptions would incorrectly mark that response successful;
  the captured candidate checks status too.
- Run-B reads of the agent-scoped seed returned no memory; explicit agent-scoped
  reads found it. Foreign memory stayed unchanged after scoped clear/optimization.
  Inspection reopened the file with a fresh adapter. This does not test a second
  production run, worker restart/resume, or every run/agent scope combination.
- **Stock Agno negative control:** manager replacement using a foreign ID changed
  that owner's content while the caller's scoped read stayed empty. The candidate
  rejected foreign-ID replacement, wrong owner, foreign deletion, absent required
  deletion, and global clear. Some manager methods swallow exceptions and still
  return an ID; the strict adapter's retained error, not that ID, decides failure.
- Raised and quiet no-row/no-op injections failed memory, session, run, single
  deletion and batch deletion writes; additional agentic/hook/tool/optimization
  injections failed too. Subsequent admission was denied. Memory/session/run
  errors can coexist with stock `RunStatus.completed`. Faults are at the adapter
  seam, not an exhaustive set of SQLite/VFS failure locations.
- Optimization write failure **after deletion** left the affected scope empty.
  Session-write failure could follow committed learning. This intentionally
  preserves partial effects; it provides **no whole-operation rollback**.
- Durable SQLite supervisor admission preceded dispatch. Worker admission,
  DB-attempt/ack/error, drain and terminal records carried operation correlation;
  successful IPC replies matched an already committed terminal and command hash.
  A separate coordinator connection wrote the same file while the worker lived.
- Identical duplicate IDs did not replay; conflicting payloads did not replay.
  A deliberately dropped reply left one existing terminal; another delivery did
  not mutate again. Seen-ID tracking is in memory and dies with the worker: this
  is **not exactly-once delivery, restart deduplication or recovery**.
- Injected evidence failures at worker admission, DB acknowledgement, DB error
  recording and terminal recording prevented success and new admission. A failed
  terminal write left no terminal; its transport-only error is not durable
  completion. Failed DB acknowledgement could follow committed memory. Required
  evidence loss fails the modeled run; it cannot magically preserve unwritable
  error evidence. Missing terminals must remain unresolved/failed, never success.

### Startup, file ownership and provenance

Fresh and concurrent two-process initialization succeeded under the root gate.
An incompatible `agno_memories` fixture raised `MigrationRequiredError`; no
migration was performed. A directory occupying the selected DB filename failed
open. A real independent `BEGIN IMMEDIATE` holder caused a driver timeout; a
held root gate caused an admission timeout. Observed blocked waits were about
0.21 and 0.25 seconds, respectively: experiment settings, not production defaults
or portable timing promises. All startup children exited normally after reporting
their outcome. The startup preflight uses private table APIs and minimal fixture
column checks, **not a selected schema/version/ownership policy**.

An earlier development run exposed lazy concurrent session DDL after readiness;
the captured route fixture explicitly initializes memory/session/run tables
before serving operations. Concurrent initialization is then exercised separately
under the gate. This makes the narrower fixture reproducible, not proof of an
integrated production startup protocol.

Runtime: Python 3.14.8, Agno 3.0.11, SQLAlchemy 2.1.0, SQLite 3.54.0. SQLite source
ID and compile options are captured; this is not an OS binary/vendor-patch or
hardware durability audit. Live SQLite WAL/SHM sidecars were observed alongside
one main database. No fallback DB was observed in captured roots, including
startup failures; rejected-path tests do not prove arbitrary extension behavior.
No sidecars were manually deleted. Main evidence writes explicitly request
`synchronous=FULL`; the inspection connection observed WAL / synchronous=2 /
busy_timeout=5000 / integrity=ok. That timeout is the **inspection connection**,
not Agno's engine timeout (200 ms) or a production guarantee. No transaction or
SQLite writer lock spans model work. File-gate aliasing through symlinks/hardlinks,
path replacement, filesystems other than this host's scratch storage, independent
clients bypassing the gate, and ownership manifests are not tested.

## Source facts versus modeled behavior

Pinned Agno source hashes are in the report:

- `memory/manager.py`: `replace_user_memory` forwards a caller-selected ID and
  user to upsert; `_upsert_db_memory` catches errors. `clear`/global-clear tools
  call unscoped `clear_memories`. `optimize_memories` clears first, then writes
  optimized rows without wrapping the whole operation in a transaction or
  normalizing every strategy output's owner.
- `db/sqlite/sqlite.py`: `upsert_user_memory` conflicts on `memory_id` alone and
  does not add a caller-ownership predicate to the update. `upsert_run` and delete
  APIs normally return `None`. These are source facts, with the specific foreign
  mutation/partial optimization above independently observed at runtime.

The controller is a **modeled coordinator**, not Mininet's production admission,
action execution, Run Evidence implementation or invocation leases. SQLite event
rows are a disposable evidence experiment replacing the prior JSONL/file-fsync
journal, **not a production durability protocol**. They do not make memory and
evidence writes atomic. No power loss, abrupt process death between event and DB
commits, torn/corrupt evidence, parent-death race, restart reconciliation,
duplicate in-flight race or supervisor evidence-write fault is validated here.
Failure admission denial is exercised after completion of the failed operation;
the shared failed flag is set at operation finalization, so immediate global
admission closure at the first DB error with concurrent operations remains a gap.
The versioned production wire contract/complete identity-order validation also
remains unimplemented. The first probe's zero-DB observations are retained, not
retested or converted into a required-memory/no-DB fallback here.

### Python extension / singleton eligibility

The candidate only admits fixed synchronous worker-local fixtures using the
operation-bound manager/adapter and tracked executor. A **modeled** object identity
registry accepts one mutable Agent for one cached identity and rejects sharing it
with a second identity; fresh factory output is eligible. This does not exercise
real module-level entrypoint imports, inherited parent state, client/loop affinity,
or a production eligibility diagnostic. A module-level Agent bound to one identity
remains a supported *candidate*, not transparently compatible arbitrary Python.
Rejecting the shared singleton must not be replaced by cloning it or serializing
away required Independent Agent Invocation overlap. Broader Python compatibility
decisions remain with
[Define compatibility and migration boundaries](https://github.com/santiagopedro1/mininet-ai/issues/44).

## Verdict and sharp remaining question — live review required

**The complete acceptance question is still not established.** The closed
synchronous fixture demonstrates useful gating and strict failure checks, not
complete coverage of the proposed production boundary. No missing route, evidence
gap, or eligibility limitation can clear architecture acceptance. No remote
Provider completion/termination evidence is produced; local drain/exit does not
certify remote quiescence or admit the next independent trial.

The evidence crash/reconciliation gate exceeds this bounded pass. Proposed sharp
follow-up (not silently claimed covered):

> Can a same-file evidence protocol fail closed across abrupt death at each
> admission/write/ack/drain/terminal boundary, reconcile a lost acknowledgement
> without replay, and preserve explicit gaps when either writer cannot commit,
> without turning process restart into resumed reasoning or weakening independent
> session concurrency?

Keep schema/lifecycle/compatibility policy in its existing tickets; this question
tests failure evidence rather than choosing those policies. The user should
decide whether to retain this partial evidence and split that validation question,
require broader explicit route/extension eligibility first, or return a negative
verdict to
[Choose a database-free or single-database storage architecture](https://github.com/santiagopedro1/mininet-ai/issues/41).
Until that live exchange, the current ticket stays open/claimed and architecture
acceptance stays blocked. Do not merge the throwaway branch or open a production PR.
