# Throwaway Agno storage prototype

**Not production code. Not approved architecture. Do not merge this branch.**

Question: can locked Agno retain scoped Agent Sessions and Learned Memory with
zero/one SQLite database per selected storage root, without silent lost updates,
silent persistence failures, or writes from old background workers?

For [Validate single-database session and shared-memory concurrency](https://github.com/santiagopedro1/mininet-ai/issues/47),
under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).
This is a real Python/SQLite experiment rather than an HTML state simulation:
an in-browser model would not establish any of the persistence guarantees at issue.

## Run

From the repository root, with Python 3.14 and `uv` available:

```bash
uv venv /tmp/opencode/agno-storage-prototype/venv --python python
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt --output-file /tmp/opencode/agno-storage-prototype/locked-constraints.txt --quiet
UV_CACHE_DIR=/tmp/opencode/agno-storage-prototype/uv-cache uv pip install --python /tmp/opencode/agno-storage-prototype/venv/bin/python --constraint /tmp/opencode/agno-storage-prototype/locked-constraints.txt 'agno==3.0.11' 'sqlalchemy[asyncio]==2.1.0'
/tmp/opencode/agno-storage-prototype/venv/bin/python -P mininet_ai/agents/agno/storage_prototype.py --scratch-parent /tmp/opencode/agno-storage-prototype/runs
```

`-P` avoids importing this module's neighboring `logging.py` as stdlib logging.
The script is offline; installation downloads dependencies. Every invocation
creates fresh `PROTOTYPE-wipe-me-*` stores, retained for inspection, never opens
an existing run store, and prints the observations path. Optional
`--report NEW_PATH` also writes a shareable JSON file, refusing overwrite.
No API keys, Provider requests, Mininet deployment, or production imports are
required. The exit code reports experiment execution errors, **not architecture
acceptance**: negative observations are intentional outcomes.

## Evidence and interpretation

The adjacent `agno-storage.observations.json` records the initial captured run, complete
scripted model messages for race/failure operations, process exits, package
versions, and prototype source hash (historical source at commit `6a69858`).
`agno-storage.supervised.observations.json` captures the follow-up supervised
candidate, including a rerun of the initial experiments, with its own source hash.
Each scenario subdirectory represents an
independent selected storage root; it is not a design with multiple databases
inside one selected root. The prototype changes no runtime storage policy.

| Experiment | Observation | What it establishes |
| --- | --- | --- |
| DB-free cached Agent | Second invocation sees first message and action-result state; fresh session lacks that state; no files created | Conditional instance-local continuity, not restart persistence |
| Persistent Agent | New Agent/adapter restore history and action-result state | Session persistence in one file, same process reconstruction |
| Scoped memory | Run-B and other-agent reads empty; agent-scoped memory survives adapter reopen | Explicit-user scoped reads; not authorization or complete mutation isolation |
| Two spawned processes without gate | Both read `seed`, both exit normally, only one new fact survives | Real semantic lost update, not SQLite corruption |
| Same scenario with full-operation gate | Both new facts survive | Candidate serialization works for this successful synchronous manager operation |
| Session-write injection | Agent returns normal content despite DB exception | Normal return does not establish required persistence success |
| Configured automatic and agentic learning | Healthy calls store memory; injected memory failures still yield `RunStatus.completed` | Both configured paths need strict error propagation outside stock completion status |
| SQLite writer contention | Actual `BEGIN IMMEDIATE` blocks memory write; bounded driver error is caught by memory tool | Driver timeout is not an affected-run failure policy |
| Gate contention | Other process reports timeout around 0.4 seconds | Candidate gate has a bounded acquisition wait, not a total inference budget |
| Foreground failure with blocked automatic learning | Error returns before learning completes; another operation acquires gate; released old worker then writes | Releasing gate at Agent return is unsafe on error paths |

`ObservedDb` is deliberately only a small **prototype error journal**. Its
post-call `prototype_affected_run_status` field is a derived observation, not an
implemented Mininet run status. It catches the demonstrated writes, including
those Agno swallows, but covers neither all mutation paths nor warnings, partial
multi-write operations, cancellation, custom tools, or recovery. It is not an
approved strict adapter.

Agno printed a no-database/history warning even though the cached second
invocation's captured model input included the first message. The positive
continuity finding is based on that input and the retained state, not the warning.

The background experiment intentionally releases the candidate lock too early
to reproduce the hazard. It then explicitly releases and drains its own offline
worker before closing its scratch database. A successful normal-return race
does not validate this unsafe lifecycle protocol.

SQLite 3.54.0 was used. Observed persistent-store settings: WAL, synchronous=2
(FULL), foreign_keys=1, busy_timeout=200 ms. Sessions, runs, memories, and schema
versions occupied four tables in one file. Live WAL/SHM sidecars disappeared on
clean final close in this scenario; that says nothing about crash recovery or
safe live copying. No sidecars were manually removed. The SQLite runtime version
is newer than the research report's named WAL fix versions, but deployment
binary/package patch provenance was not independently audited.

## Verdict requiring human review

**Do not accept the preferred architecture yet.** One-file storage and conditional
zero-DB continuity are feasible in these probes. Stock completion/error behavior
and a gate released at Agent return do not satisfy the agreed safety guarantees.
Revisit [Choose a database-free or single-database storage architecture](https://github.com/santiagopedro1/mininet-ai/issues/41)
without weakening those guarantees implicitly.

The follow-up below exercises a candidate combining full-operation cross-process
serialization, per-operation persistence error accounting, and process-isolated
learning workers. It does not establish complete architecture acceptance.
Remote Provider termination remains a separate unvalidated contract.

## Follow-up: supervised local workers

The user approved exploring supervised learning workers, not accepting the
architecture. This adds **13 cases**: 11 single-operation cases plus simultaneous
automatic and agentic operation pairs, each pair using two independent
supervisors and spawned Agent processes sharing one file.

### Candidate boundary

- A spawned worker owns the root-wide file gate **before** Agno reads any state;
  its background threads use that same guarded operation. No SQLite transaction
  is held open during model execution.
- Worker ownership avoids the specific parent-only-lock hazard: losing the
  supervisor cannot itself close the worker's lock descriptor. Actual supervisor
  death and orphan recovery are **not experimentally validated** here.
- On graceful paths, the worker keeps the gate through executor drain and DB
  close, including foreground error paths. A foreground response, completion
  message, or EOF is not accepted as local containment.
- The supervisor confirms process exit with bounded join; if needed it sends
  SIGTERM, then SIGKILL and joins again. A missing final record, nonzero exit,
  forced stop, worker error, or observed DB error means failed operation.
  Failure to confirm exit denies the next local operation; the prototype raises
  rather than pretending cleanup succeeded. That denial branch was not induced
  on this host.
- The prototype supervisor has a 4-second operation budget and 0.4-second
  terminate/kill waits. Foreground errors and a deterministically observed stall
  shorten the remaining drain budget to 0.4 seconds. Gate acquisition inside the
  worker is bounded to 2 seconds. These are **experiment controls**, not selected
  production budgets or hard real-time guarantees.
- The worker contains only Agno threads. This does not contain subprocesses
  spawned by arbitrary tools, network actions, remote model-service work, or
  other processes that bypass the gate.

### Observed results

| Case | Supervisor result | Local observation |
| --- | --- | --- |
| Healthy automatic / agentic | Success, normal exit | Configured fact retained |
| Automatic / agentic memory-write failure | Failed despite Agno completion | Injected error retained in journal; seed unchanged |
| Session-write failure | Failed | Session error plus run-row FK error recorded; learned memory had already committed |
| Foreground error, cooperative learning drain | Failed, normal exit | Gate denied a concurrent acquisition; learning completed before gate release |
| Foreground error, blocked learning | Failed, SIGTERM exit | Gate denied acquisition while blocked; confirmed dead process before next operation; blocked fact absent |
| Blocked learning with SIGTERM ignored | Failed, SIGKILL exit | Escalation worked; blocked fact absent |
| Stalled normal foreground/background completion | Failed, SIGTERM exit | No foreground completion required to enforce the supervisor budget |
| Worker `os._exit(23)` after DB open | Failed, exit 23 | Missing final record not treated as success; file reopened normally |
| Real SQLite writer contention | Failed despite Agno completion | Three observed errors (memory/session/run writes); writer released only after the worker exited |
| Two concurrent automatic operations | Both success | Same memory retained `seed`, `fact-A`, and `fact-B` |
| Two concurrent agentic operations | Both success | Same memory retained `seed`, `fact-A`, and `fact-B` |

All 11 single-operation cases verified process exit before opening the next
guarded learning operation, then reopened the same database and observed
`PRAGMA integrity_check = ok`. A clean reopen does **not** establish arbitrary
crash recovery; the hard-crash case did not interrupt an active write transaction.
The three forced-stop cases contained the worker in approximately 0.90, 0.91,
and 1.30 seconds including process startup on this machine. Those timings are
observations, not a portable bound. Post-containment inspection is not bounded
by the supervisor's operation budget.

The journal now also catches raised `upsert_run` errors. It is still incomplete
for all persistence APIs. Its in-memory observations can be lost on worker death;
the supervisor conservatively fails operations with missing records, but this
does not provide durable per-write audit evidence or reliable result delivery
if the supervisor dies.

**Interpretation:** supervised processes are a promising local containment
boundary for the covered automatic/agentic paths. This is not authorization to
replace the runtime, resume a timed-out trial, or accept the whole architecture.
Failed-run learning is not transactional: existing ADR 0010's partial-learning
behavior remains, and a later failure must not be presented as a rollback.

### Still unvalidated

- All supported mutations, optimization, deletion, custom tools, and scopes;
  long-lived same-process concurrency beyond the covered spawned automatic and
  agentic operations.
- Concurrent first-use schema creation; incompatible schemas, wrong-owner/no-row
  collisions, malicious/foreign memory IDs, roots with the same agent identity.
- Session restoration in a new process (memory race workers are new processes,
  but do not restore Agent history); cached-session reconstruction/switch-back
  loss; session summaries and their failure paths.
- Strict end-to-end durable affected-run failure/evidence, retries, external
  cancellation, supervisor death/orphans, unexpected worker descendants,
  uncontainable workers, active-write crashes, partial multi-write learning,
  checkpointing, permissions, live backups, and no-fallback behavior across all
  failure modes. The forced local-worker stops above cover only the shown cases.
- Real Provider learning/retrieval quality, remote cancellation/containment,
  fairness, long-lived contention, and performance under realistic model latency.
- Production timeout values (0.2/0.4/10-second values here are experiment controls,
  not lifecycle decisions) and SQLite deployment provenance.

ADRs 0001–0006 and independent-trial isolation remain unchanged. No actual run
data, learned memory, production implementation, or measurement was migrated or
deleted. Human review is required; this artifact alone does not close the ticket.
