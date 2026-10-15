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

The adjacent `agno-storage.observations.json` records the captured run, complete
scripted model messages for race/failure operations, process exits, package
versions, and prototype source hash. Each scenario subdirectory represents an
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

A plausible next candidate combines full-operation cross-process serialization,
strict per-operation persistence error accounting, and ownership of all learning
workers until verified quiescence. Bounded local-worker containment may require
process isolation, but this prototype does not implement or endorse that choice.
Remote Provider termination remains a separate unvalidated contract.

### Still unvalidated

- Full guarded automatic/agentic Agent operations across processes and threads;
  every supported mutation, optimization, deletion, custom tool, and scope.
- Concurrent first-use schema creation; incompatible schemas, wrong-owner/no-row
  collisions, malicious/foreign memory IDs, roots with the same agent identity.
- Session restoration in a new process (memory race workers are new processes,
  but do not restore Agent history); cached-session reconstruction/switch-back
  loss; session summaries and their failure paths.
- Strict end-to-end affected-run failure/evidence, retries, cancellation, timeout,
  lock-holder/supervisor death, late-write prevention, crash recovery, partial
  learning updates, checkpointing, permissions, live backups, and no-fallback
  behavior across all failure modes.
- Real Provider learning/retrieval quality, remote cancellation/containment,
  fairness, long-lived contention, and performance under realistic model latency.
- Production timeout values (0.2/0.4/10-second values here are experiment controls,
  not lifecycle decisions) and SQLite deployment provenance.

ADRs 0001–0006 and independent-trial isolation remain unchanged. No actual run
data, learned memory, production implementation, or measurement was migrated or
deleted. Human review is required; this artifact alone does not close the ticket.
