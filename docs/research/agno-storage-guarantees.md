# Agno session and memory storage guarantees

Research for [Verify Agno session and memory storage guarantees (#46)][ticket],
under [Plan clearer output and simplified run storage (#38)][map].
Investigated 2026-10-14 against repository base `ed8688b`.

**Outcome:** Agno exposes the pieces for zero-database, instance-local Agent
Sessions and for sessions and Learned Memory in one SQLite database. It does
**not** establish the required concurrent-learning, strict persistence-failure,
or bounded shutdown guarantees. The architecture in [the resolution of #41][architecture]
remains preferred, not validated. [The HITL prototype (#47)][prototype] is still
required; this report neither implements it nor resolves its human-review gate.

## Method, versions, and evidence limits

- **Source-derived** below means inspected code, not an observed execution result.
  **Inference** means a consequence of that code and the cited database semantics.
  **Prototype question** means behavior still requiring implementation-independent
  experimental evidence and human review.
- `uv.lock` pins **Agno 3.0.11** and **SQLAlchemy 2.1.0**, rather than merely the
  broader constraints in `pyproject.toml`. Downloaded their exact source archives
  to `/tmp/opencode/agno-storage-sources`, verified these SHA-256 hashes against
  the lock, and inspected them without installing dependencies:

  | Archive | SHA-256 |
  | --- | --- |
  | [agno-3.0.11.tar.gz][agno-archive] | `8e625ebea5265f0cf47b5dafbd749b0e6feb226f8b14f230e2674f5ded41fb7f` |
  | [sqlalchemy-2.1.0.tar.gz][sa-archive] | `37600e4641c435a7e4549f4e6df79d9dfc096bb9712a10dba8925921bc87bc3c` |

- The Agno source files cited here were byte-compared with upstream commit
  `8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc` (`v3.0.11`); the SQLAlchemy
  dialect and two documentation files cited below were byte-compared with
  `634c911a6d6a0d0aee1521bfb901b5dca35c78f1` (`rel_2_1_0`). Links are pinned
  to those commits. Archive/hash verification and source comparison succeeded.
- **No behavior, concurrency, crash, Provider, Mininet, or test-suite execution
  was performed.** No database was opened for this research. Dependency versions
  do not pin the Python-linked SQLite runtime or its build options. Official
  SQLite documentation was consulted as of this date; deployment-specific SQLite
  version, PRAGMAs, filesystem, and transaction behavior remain prototype inputs.
- Scope is concurrent local processes on one host and a local filesystem, not
  network shares or multi-host coordination. Run Evidence, Shared Operational
  State, Agent Session, and Learned Memory have their [glossary meanings][glossary].
  Reviewed [domain guidance](../agents/domain.md) and ADRs
  [0001](../adr/0001-link-failure-connectivity-recovery.md)–[0006](../adr/0006-raw-latency-records-and-summaries.md).
  In particular, [ADR-0004](../adr/0004-repeated-failure-trial-lifecycle.md)
  requires fresh sessions/trial-local state and containment before the next trial;
  [ADR-0005](../adr/0005-clock-correlation-and-instrumentation.md) rejects local
  cancellation as proof of remote Provider termination. Nothing here relaxes
  those contracts or makes database rows authoritative Run Evidence.

## 1. Database-free continuity is possible, but conditional

**Source-derived:** `Agent.cache_session` defaults to **False**. With it enabled,
`read_or_create_session` retains an in-memory `AgentSession`; that session holds
history and `session_data`. Terminal persistence first adds the run and updated
state to the session object. `save_session`/`save_run` have database guards, so a
database is not intrinsically needed to keep that cached object alive.
[Agent defaults][agent-defaults], [cache][agent-cache], [session construction][session-construction],
[terminal state/history update][persist-run], [save guards][session-crud].

Cache matching checks session ID, an explicitly supplied user ID, and database
object identity. It is **one cached session**, not a durable multi-session store.
Switching sessions replaces it; releasing/reconstructing the Agent or exiting
the process loses it. With the default cache disabled and no database,
`get_session` returns `None` and subsequent runs construct fresh sessions.
`get_session_state`/`update_session_state` require an existing session; updates
mutate its dictionary then call `save_session`. A DB-free cached session can
therefore support these APIs, but merely initializing `agent.session_state`
is not equivalent to retaining the evolving session. Bounded-history reads
explicitly preserve the cache path when no database is present.
[Cache lookup][agent-cache], [session CRUD][session-crud],
[state utilities][state-utils], [bounded-history rule][history-limit].

**Repository boundary:** the current factory explicitly rejects any configured
local, conversation, or learned memory without a database. It does not enable
`cache_session` for declarative agents. Its per-Agent reuse is not, by itself,
proof of DB-free session continuity. Local action results use the session-state
APIs. Thus “Agno always requires a database for local/history state” is too strong,
but “the current repository supports that mode” is also unsupported.
[Factory configuration][repo-provider], [invocation/action-result paths][repo-invoke].

**Prototype questions:** prove repeated history/state/action-result continuity
with a stable Agent and explicit identities; prove fresh-session isolation;
demonstrate expected loss on reconstruction/session switching and zero database
creation. Session summaries also live with the session, but success/failure and
their context inclusion must be exercised rather than inferred from caching alone.

## 2. Configured persistence and one logical SQLite database

**Source-derived:** `SqliteDb` selects engine, URL, file, then default `./agno.db`
in that order. Avoid constructing an unspecified adapter in a claimed zero-DB
configuration. One adapter has one engine and table names for both sessions and
memories. Defaults include `agno_sessions`, `agno_runs`, `agno_memories`, and
schema-version tables; tables are resolved/created as needed. Session state is
JSON in the session row; v3 history has separate run rows, **not a separate
database per session**. Custom session-table names also affect default run-table
names and FK resolution. This is direct source support for co-location, not
evidence that the repository's intended shared-root configuration was tested.
[SQLite constructor][sqlite-constructor], [default names][table-names],
[schemas][schemas], [table resolution][table-resolution].

Persistence across reconstructed Agents needs the same physical database and
compatible schema, plus matching identities. The repository assigns the same
factory-owned database to its Agents, rejects conflicting databases, and maps
automatic/agentic Learned Memory to `MemoryManager` flags. Upstream initializes
that manager with the Agent database; without a database it warns and memory
creation returns a “provide a db” string. Upstream's newer `LearningMachine`
is a different API and is disabled without a database; it is **not** the
repository's configured Learned Memory path. Do not import guarantees from that
API into this one. [Repository configuration][repo-provider],
[manager initialization][manager-init], [memory creation][memory-rmw].

Existing tables are validated at resolution and mismatches raise. But this is
not a single eager startup validation of all configured persistence features.
The current repository preflight opens the file and briefly obtains/rolls back
`BEGIN IMMEDIATE`, explicitly without creating/validating Agno-owned tables.
It does not reserve later write access or prove all schemas compatible.
[Table resolution][table-resolution], [repository adapter][repo-storage].

**Prototype questions:** session/state/history restoration with fresh objects;
memory reuse with fresh processes; concurrent first use of an empty database;
compatible/incompatible schemas; no fallback/default/per-run database; correct
table naming and ownership. Changing configurations must not silently change
the memory identity of an existing session (see below).

## 3. Connections, transactions, WAL, and sidecars

**Source-derived:** Agno uses `scoped_session(sessionmaker(bind=engine))` and
per-method transaction contexts. The locked SQLAlchemy pysqlite dialect uses
`QueuePool` for ordinary file databases and defaults `check_same_thread=False`;
plain in-memory SQLite uses different pooling behavior. One logical file does
not imply one connection, one SQLAlchemy Session, or one application mutex.
SQLAlchemy documents Session-per-thread/AsyncSession-per-task and warns against
sharing inherited pooled connections after fork. Separate processes need valid
process-local connection ownership, even when they share the same file.
[Agno engine/session setup][sqlite-constructor], [locked dialect][sa-dialect],
[Session concurrency][sa-session], [fork/pool guidance][sa-pool].

Agno's connection hook attempts `foreign_keys=ON` and `journal_mode=WAL`.
It accepts a non-WAL result and catches hook exceptions. It does **not** explicitly
set `synchronous`, a busy timeout, or a total application wait budget. A source
comment describing default FULL is not runtime verification of durability
settings. SQLAlchemy accepts a driver `timeout`, but the repository's simple
`db_file` construction does not configure a project-specific one.
[Hook/constructor][sqlite-constructor], [driver arguments][sa-args],
[repository adapter][repo-storage].

**Documented SQLite semantics:** separate connections see committed changes,
SQLite serializes writers, and WAL allows readers alongside a writer. This
protects transactions, not a read–model–write sequence spanning several
transactions. WAL still permits `SQLITE_BUSY`; an old read snapshot is not a
promise to see concurrent commits until a new transaction. None of these
properties provides run-wide reproducible snapshots for shared learning.
[Isolation][sqlite-isolation], [WAL §§2.2, 9][sqlite-wal].

WAL's `-wal` and `-shm` and rollback journal files are sidecars, **not extra
logical databases**. Committed data may still reside in the WAL. Copying only
the main file while live, or deleting sidecars manually, is not a safe retention
or cleanup policy. Last-close cleanup/checkpointing can be delayed by other
connections or interrupted by crashes; pooled disposal is not a memory-worker
drain. Permissions, backup/export, checkpointing, and crash recovery must account
for the database and its sidecars. [SQLite WAL §§3–6][sqlite-wal],
[Agno close disposes its engine][sqlite-constructor].

**Additional runtime prerequisite:** SQLite's current official WAL documentation
records the WAL-reset corruption race and fixes in **3.51.3+**, with named
backports **3.44.6 / 3.50.7**. The affected pattern includes concurrent
connections writing/checkpointing on one file. The Agno/SQLAlchemy lock alone
does not establish that the deployed SQLite includes the fix. Record and verify
the actual library/patch provenance before accepting concurrent WAL operation;
this research did not inspect a deployment library. [WAL §11][sqlite-wal].

## 4. Identity scoping is application policy, not automatic isolation

**Source-derived repository mapping:**

| Concept | Current value |
| --- | --- |
| Agno `session_id` | `context.run_id + ':' + context.agent_id` |
| Agno `run_id` | invocation ID (not the experiment run ID) |
| Run-Scoped Memory `user_id` | session ID |
| Agent-Scoped Learned Memory `user_id` | agent ID |

The adapter checks output identities. The mapping is intended to separate
sessions between runs and allow cross-run learning for an explicitly shared
agent identity within a selected database/root. Distinct roots give separate
physical stores. A shared file does not itself choose the correct namespace.
[Invocation mapping/checks][repo-invoke].

**Source-derived upstream limits:** session, run, and memory primary keys are
respectively `session_id`, `run_id`, `memory_id`, **not composite keys including
agent/user/run scope**. Memory reads by `MemoryManager` select the `user_id`
(defaulting to `"default"` when omitted), not an automatic agent namespace.
Session reads accept optional user filtering; the Agent-session upsert prevents
overwriting a row owned by a different non-null user by returning no row, not
by allocating a new scoped row. A session's memory-scope/user mapping must
therefore stay consistent. [Schemas][schemas], [manager reads/defaults][memory-reads],
[SQLite reads/upserts][sqlite-session], [memory upsert][sqlite-memory].

These are trusted application conventions, not access-control guarantees.
Memory updates conflict on `memory_id` alone and do not check the caller's
`user_id` on conflict. Manager tools accept a model-selected memory ID.
Default manager construction disables clear/delete for agentic tasks, but if
the clear tool is enabled it calls database-wide `clear_memories`, not a
user-scoped clear. Ordinary scoped reads do not make all mutation/admin APIs
scope-safe. [Manager defaults][memory-defaults], [memory tools][memory-tools],
[SQLite memory mutation][sqlite-memory].

**Prototype questions:** unique/unambiguous session and invocation identities
across processes; same agent ID in different runs; distinct agents/roots;
Run-Scoped Memory isolation; deliberate Agent-Scoped Learned Memory reuse;
fresh trial sessions even when learning scope is shared; no default-user or
custom-Python-agent escape from declared policy. Include wrong-user/no-row
writes and foreign memory IDs, not just successful reads. This report does not
choose a new namespace or authorize broader memory sharing.

## 5. Full Learned Memory operations can race

**Source-derived:** automatic `create_user_memories` and agentic
`update_memory_task` read existing memories, supply them to a model, invoke
memory tools that independently mutate the database, then reread. Async twins
follow the same separation. Adds use UUIDs; updates replace the memory payload
at an existing ID. Each SQLite upsert has its own transaction with no expected
revision/CAS check. Optimization can read/summarize, clear a user's existing
memories, then write replacements across separate calls.
[Read–model–write paths][memory-rmw], [model execution][memory-model],
[tool writes][memory-tools], [SQLite transaction][sqlite-memory],
[optimization][memory-optimization].

**Inference, not a reproduced failure:** A and B can both read memory M, derive
different replacements from the same old content, and commit in sequence.
The second upsert wins without SQLite corruption or a lock error: the first
semantic update is lost. Independent new UUID inserts need not overwrite one
another, but add-only success does not prove safe updates, deduplication,
deletion, or optimization. Per-SQL-write locking and WAL cannot close this gap.

For the agreed no-silent-lost-update contract, **the stock path requires
serialization of conflicting full read–model–write operations across all
participating threads/processes**, or another validated full-operation conflict
protocol. Releasing a lock while the model runs then locking only the write is
insufficient without reread/recompute/conflict detection. Serialization must
cover every applicable mutation entry point and actual background completion;
it does not itself make multi-call learning crash-atomic. Whether the mechanism,
wait budget, and model latency are feasible is precisely a prototype question,
not a decision to hold one SQLite transaction open during inference.

The repository's `RLock`s serialize use of one cached Agent in one factory;
they do not synchronize separate factories/processes. `SourceLocks(writer=True)`
holds **shared** lifetime locks to exclude export, not mutually exclusive
learning locks. The startup `BEGIN IMMEDIATE` is immediately rolled back.
Neither establishes the full-operation protocol.
[Factory locks][repo-provider], [source-lock semantics][repo-locks],
[startup preflight][repo-storage].

## 6. Completion is not persistence success or bounded containment

**Source-derived:** automatic memory creation runs in a background thread for
sync `run` and an asyncio task for `arun`. Successful non-streaming paths wait
on these jobs before setting the run completed and saving its session/run.
Agentic memory invokes `update_memory_task` via a model tool. The repository
uses bounded, non-streaming `run`/`arun`, not Agno's detached background-run API.
[Memory scheduling][memory-scheduling], [sync completion/error][sync-run],
[async completion/cancellation][async-run], [agentic dispatch][agentic-tool],
[repository invocation][repo-invoke].

However, the join helpers catch ordinary background exceptions and warn.
Memory tools also catch DB errors and return error strings. Summary generation
can warn then continue. Critically, Agent-level session-upsert wrappers catch
DB write exceptions and return `None`; `save_session` does not turn that into
a required-persistence failure. Session **read** errors do propagate rather
than masquerading as missing rows. Thus a normal Agent response, completed
status, memory-update event, or successful join is **not** proof that all
configured persistence succeeded. The database-level method raising is not
enough when a higher layer swallows it.
[Join helpers][join-helpers], [memory tools][memory-tools],
[summary/completion][sync-run], [session storage wrappers][session-wrappers],
[save guards][session-crud].

Sync error/retry paths call `Future.cancel()`; already-running threads cannot
be stopped this way, and final cleanup uses `future.result(timeout=0)` rather
than a drain. Async error paths cancel/await active memory tasks, while request
cancellation can schedule detached persistence. Adapter `close()` only disposes
the engine. The repository factory closes its runners/database after workers
stop, but does not explicitly drain the Agent's sync background executor.
No source basis establishes one bounded shutdown covering retries, background
learning, late writes, detached persistence, and remote Provider work.
[Scheduling/retry comment][memory-scheduling], [sync finally][sync-run],
[async finally][async-run], [detached cancellation persistence][cancel-persist],
[repository factory close][repo-provider].

**Inference:** releasing a full-operation lock at an error/timeout return may
permit a surviving worker to write after the next learner starts. Waiting for
success-only completion is therefore insufficient. A new guard must also avoid
deadlocking a background worker by holding a lock it needs. Strict failure
visibility and bounded quiescence require explicit prototype evidence; database
integrity and local task cancellation do not prove remote Provider containment.

## 7. Handoff to the HITL prototype (not implemented here)

Preserve the [#41 validation contract][architecture]. At minimum demonstrate:

1. **Zero/one logical database:** DB-free cached history/local/action-state
   continuity and fresh-session loss boundaries; configured persistence restores
   with new objects/processes; count all database files and explain sidecars.
2. **Identity matrix:** run/agent/root isolation and intentional agent-scoped
   reuse, wrong-owner collisions, fresh trials, explicit ID propagation, and
   same-session user/scope consistency. Test configured automatic and agentic
   paths, not only direct SQL calls. Distinguish deterministic/mock-model storage
   evidence from real Provider learning/retrieval evidence and disclose either
   that remains untested.
3. **Adversarial full-operation concurrency:** deterministic model-controlled
   barriers make two processes read the same old memory and update the same ID.
   First expose the baseline race, then prove the proposed serialization/conflict
   boundary retains both intended facts. Cover parallel same-process users,
   distinct scopes, all allowed mutations, and any optimization/custom tools.
4. **Completion and failure:** blocked writes beyond a bounded wait, bad path,
   incompatible schema, tool-write/summary/session-write failures, retries,
   timeout/cancellation during learning, late workers, shutdown, and lock-holder
   termination. Require explicit affected-run failure and retained evidence;
   no silent disabled memory, fallback database, or warning-only success.
5. **Real connection/lifecycle conditions:** concurrent empty-store creation,
   process-local engines (including any fork use), thread-local sessions,
   actual SQLite version/patches and PRAGMAs, contention budgets, permissions,
   live/crashed WAL sidecars, checkpoint/reopen, and bounded worker containment.
   Record where multi-call learning can be partially applied after a crash;
   do not claim transactional rollback for the whole model operation.
6. **Human review:** compare observed results with #41 and ADRs 0001–0006.
   If a guarantee is infeasible, reopen #41 rather than silently weakening it.
   Source research alone does not accept the architecture or resolve #47.

## Proposed research resolution text

> Locked-version source research is complete: Agno 3.0.11 / SQLAlchemy 2.1.0.
> DB-free instance-local sessions are possible with explicit caching, but are
> not the current repository's supported configured-memory mode. Sessions,
> runs, and learned memories can occupy tables in one SQLite file. Identities
> and scope must be assigned by the application; SQLite transactions/WAL do
> not serialize Agno's full learned-memory read–model–write operation. The
> existing per-factory locks and shared lifetime writer locks are insufficient
> for cross-process learning. Stock background/session/tool paths can downgrade
> persistence failures to warnings, and error/cancellation returns do not prove
> bounded worker or remote Provider containment. A full-operation cross-process
> protocol, strict failure reporting, identity matrix, actual SQLite runtime,
> sidecar lifecycle, and failure/quiescence behavior remain for the HITL
> prototype. No runtime guarantees were tested and the architecture is not
> validated. Link this research branch/file as context; retain #47's human
> review and #41's acceptance gate.

## Primary sources

[ticket]: https://github.com/santiagopedro1/mininet-ai/issues/46
[map]: https://github.com/santiagopedro1/mininet-ai/issues/38
[architecture]: https://github.com/santiagopedro1/mininet-ai/issues/41#issuecomment-6178665569
[prototype]: https://github.com/santiagopedro1/mininet-ai/issues/47
[glossary]: ../../GLOSSARY.md
[agno-archive]: https://files.pythonhosted.org/packages/7b/87/69e4de59b404fcfe303ed76f199407eb675cd83f8de10bba89ddb8fec6f8/agno-3.0.11.tar.gz
[sa-archive]: https://files.pythonhosted.org/packages/ba/29/8de56a7f78fb0d71fe4f91ee43401a24ba8777e6d4f72683f268c9e0c72e/sqlalchemy-2.1.0.tar.gz
[repo-provider]: https://github.com/santiagopedro1/mininet-ai/blob/ed8688b/mininet_ai/agents/agno/provider.py#L222-L373
[repo-invoke]: https://github.com/santiagopedro1/mininet-ai/blob/ed8688b/mininet_ai/agents/agno/provider.py#L475-L651
[repo-storage]: https://github.com/santiagopedro1/mininet-ai/blob/ed8688b/mininet_ai/agents/agno/storage.py#L16-L50
[repo-locks]: https://github.com/santiagopedro1/mininet-ai/blob/ed8688b/mininet_ai/saved_runs.py#L73-L125
[agent-cache]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/agent.py#L718-L748
[agent-defaults]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/agent.py#L96-L110
[session-construction]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_storage.py#L635-L775
[persist-run]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_run.py#L6121-L6242
[session-crud]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_session.py#L75-L355
[state-utils]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/utils/agent.py#L1063-L1134
[history-limit]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_session.py#L745-L768
[sqlite-constructor]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/db/sqlite/sqlite.py#L91-L309
[table-names]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/db/base.py#L330-L362
[schemas]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/db/sqlite/schemas.py#L13-L78
[table-resolution]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/db/sqlite/sqlite.py#L775-L812
[manager-init]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_init.py#L99-L145
[sa-dialect]: https://github.com/sqlalchemy/sqlalchemy/blob/634c911a6d6a0d0aee1521bfb901b5dca35c78f1/lib/sqlalchemy/dialects/sqlite/pysqlite.py#L214-L260
[sa-args]: https://github.com/sqlalchemy/sqlalchemy/blob/634c911a6d6a0d0aee1521bfb901b5dca35c78f1/lib/sqlalchemy/dialects/sqlite/pysqlite.py#L751-L827
[sa-session]: https://github.com/sqlalchemy/sqlalchemy/blob/634c911a6d6a0d0aee1521bfb901b5dca35c78f1/doc/build/orm/session_basics.rst#L996-L1045
[sa-pool]: https://github.com/sqlalchemy/sqlalchemy/blob/634c911a6d6a0d0aee1521bfb901b5dca35c78f1/doc/build/core/pooling.rst#L595-L708
[sqlite-isolation]: https://www.sqlite.org/isolation.html
[sqlite-wal]: https://www.sqlite.org/wal.html
[sqlite-session]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/db/sqlite/sqlite.py#L1496-L1857
[sqlite-memory]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/db/sqlite/sqlite.py#L2420-L2613
[memory-reads]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/memory/manager.py#L125-L218
[memory-defaults]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/memory/manager.py#L77-L107
[memory-rmw]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/memory/manager.py#L377-L567
[memory-model]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/memory/manager.py#L1044-L1320
[memory-tools]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/memory/manager.py#L1322-L1551
[memory-optimization]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/memory/manager.py#L797-L937
[memory-scheduling]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_managers.py#L139-L215
[sync-run]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_run.py#L641-L764
[async-run]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_run.py#L1784-L1936
[agentic-tool]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_default_tools.py#L37-L74
[join-helpers]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/utils/agent.py#L53-L149
[session-wrappers]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_storage.py#L355-L454
[cancel-persist]: https://github.com/agno-agi/agno/blob/8063a7ec3ac6ab4902bdc7966870d3b5c5f51ffc/libs/agno/agno/agent/_run.py#L6325-L6368
