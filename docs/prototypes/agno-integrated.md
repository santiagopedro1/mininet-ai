# Throwaway integrated worker probes — partial evidence

**Do not merge. Not production IPC. Architecture NOT validated. Human review pending.**

For [Validate the integrated worker persistence and containment protocol](https://github.com/santiagopedro1/mininet-ai/issues/56),
under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).
This extends the previous [storage prototype](agno-storage.md), not its coverage
claims. The resolved integration protocol is the candidate, not an implemented
or accepted production boundary. No ADR or production behavior changed.

## Question and shape

Can stable, independently concurrent session owners in one long-lived process
retain history/outcomes, expose required storage failures, and correlate durable
terminal records without replaying ambiguous operations? What survives an active
write crash in the same selected database?

A real Python/Agno/SQLite probe is used instead of an HTML state simulation:
only actual runtime observations can answer these persistence questions.
Session-only reasoning is supported; Learned Memory operations/extensions are
not admitted by this worker. The crash fixture separately invokes real Agno
learning. This is **not** the complete worker-owned learning-gate integration.

## Run

Use the isolated installation described in [the earlier prototype](agno-storage.md#run):

```bash
/tmp/opencode/agno-storage-prototype/venv/bin/python -P mininet_ai/agents/agno/integrated_storage_prototype.py --scratch-parent /tmp/opencode/agno-integrated-prototype --report /tmp/opencode/agno-integrated-prototype/NEW-report.json
```

Choose a new report path on every run; overwrite is refused. Fresh
`PROTOTYPE-wipe-me-*` directories are retained for inspection, never reused.
All model responses are deterministic scripts. No Provider clients, credentials,
live/paid calls, Mininet topology, or existing data stores are used.
The exit code reports probe execution errors, **not acceptance**.

Primary assets:

- [Runnable probe](../../mininet_ai/agents/agno/integrated_storage_prototype.py).
- [Captured full observations, messages, checks and provenance](agno-integrated.observations.json).
- [Reused scripted models and limited error journal](../../mininet_ai/agents/agno/storage_prototype.py).

## Observed evidence

Three groups completed without experiment errors. All boolean observation
checks were true; string-valued checks explicitly mark cases not exercised.

### Database-free and one-file long-lived sessions

- One spawned process owns separate single-thread executors for four session
  identities. Each cached Agent is constructed and operated on its owning thread.
  A two-party model barrier was reached by independent A/B sessions and by two
  invocation-specific stateless identities. A globally serial worker would fail
  the bounded barrier. This proves overlap for these scripted synchronous calls,
  **not** async-loop ownership, Mininet invocation leases or realistic latency.
- A retained the same Agent object and thread across reasoning and a separately
  dispatched outcome update. Its next captured model input contained its earlier
  message; B's did not. A's local state retained the installed action result.
- The outcome carried `shared_commit=failed`; it was retained. This models the
  coordinator's intended dispatch after failure, **not** execution of Mininet's
  failed-commit path, actions, authorization, or shared-state versioning.
- The two stateless identities used distinct objects. No claim about eviction,
  dynamic singleton factories, or exhaustive isolation follows.
- In one-file mode the coordinator initialized schemas before worker startup,
  then retained its own SQLite connection. It committed its own table row while
  the worker was alive. Worker-owned Agno connections used that same file.
  This rules out a lifetime-wide writer lock **in this probe**, not concurrent
  first-use/schema/ownership correctness.

### Failure and ambiguous delivery

- Admissions and worker terminal records are separate append-and-file-fsync JSONL
  files, not additional databases. Every terminal record carries the operation
  ID, session and submitted-payload hash. IPC success follows terminal recording.
- Repeated identical operation IDs were reported as duplicates without mutation;
  changed payloads under the same ID were flagged and not executed. A foreign
  session was rejected. This is an in-memory duplicate registry, **not** durable
  restart recovery, complete wire validation, or an exactly-once guarantee.
- One operation committed a terminal record but intentionally sent no IPC reply.
  Inspection found one correlated terminal; no reasoning replay was attempted.
  A separate injected terminal-write exception produced neither terminal record
  nor reply even though the operation could already have changed session history.
  Missing/rejected admissions remain explicit in the observations. A read of this
  local journal is not a production reconciliation or delivery protocol.
- In one-file mode an injected session-write error was swallowed by Agno, which
  reported `RunStatus.completed`. The limited adapter journal retained the error,
  the terminal result had `success=false`, and an attempted subsequent dispatch
  was denied by the probe's supervisor admission gate.
- Both workers were stopped and joined with exit code zero. A subsequent dispatch
  was actually attempted and rejected; no replacement worker/cache was created.
  This only exercises the supervisor's local gate. A caller that bypasses it is
  not contained by this prototype transport.

The worker terminal-write exception is held by a thread Future; the supervisor
does not infer success from orderly process exit. Its missing terminal remains
a failure/evidence gap. Production needs owned task-error propagation and bounded
live detection, rather than relying on this controlled drain.

File fsync here is **not** a power-loss durability proof: new-file directory
entries are not directory-fsynced, torn JSONL records/recovery are not handled,
and supervisor death during evidence writes is not tested. The separate journals
also are not a transaction with session writes, actions, or learned memory.

### Active-write crash and partial learning

- A child performed actual Agno learning and closed its adapter, committing
  `seed | committed-before-crash` under the original learned-memory user.
- It then opened `BEGIN IMMEDIATE` on the same file and changed `agno_memories`
  user IDs without committing. The child reported an active transaction before
  the controller sent SIGKILL and verified child exit (`-9`).
- Reopening returned `integrity_check=ok`; the earlier committed fact survived,
  while the uncommitted scope had no records. **This is partial learning, not
  whole-operation rollback.** The interrupted statement is raw SQLite, not an
  interrupt at every point in Agno's learning APIs.
- Live files were one `.sqlite3` plus WAL/SHM; final clean-close files contained
  just that database. The DB-free case created only two JSONL evidence files.
  No fallback/extra database was observed in these cases. This is not exhaustive
  no-fallback coverage for arbitrary configuration or failure paths.

## Runtime provenance

Captured Python 3.14.8, Agno 3.0.11, SQLAlchemy 2.1.0 and SQLite 3.54.0, plus
SQLite source ID, host platform, installed package versions and hashes of both
prototype source files. The crash fixture observed WAL, synchronous=2 (FULL),
busy_timeout=200 ms and foreign_keys=1 on the Agno inspection connection.
The raw crash connection has its own settings; the probe does not imply that
all connections share those PRAGMAs. This is runtime provenance, **not** an audit
of native SQLite build patches, deployment filesystem durability or packaging.
No sidecars were manually removed.

All wait values are experiment controls, not proposed production timeout defaults.

## Acceptance coverage and remaining sharp questions

**The answer to the ticket's full acceptance question remains “not established.”**
No remote Provider completion/termination witness was produced: model responses
here are scripts. Verified local exit cannot establish remote quiescence. Preserve
the existing fail-closed independent-trial contract; unresolved remote work still
blocks the next independent trial. Do not reinterpret this as successful remote
containment or authorization to provision/call a Provider.

These questions are precise enough for follow-up decision/prototype tickets if
human review agrees; they are not hidden in a claim of complete validation:

1. **Complete learning and strict evidence boundary:** Can the long-lived worker
   account for every allowed learning read/mutation route (automatic, agentic,
   manager, hooks/tools, optimization/deletion, scopes and IDs), retain a root-wide
   gate through background quiescence, and durably fail on all required write/no-row
   errors without lost updates? Include same-run contention, wrong-owner writes,
   concurrent first-use/incompatible schemas, no fallback, supervisor evidence
   loss, partial writes and explicit compliant/rejected Python singleton extensions.
2. **Deployment local containment:** Can pre-arming supervisor death, parent-thread
   ownership, cancellation, blocked siblings, unverified exit and allowed tool
   descendants be bounded/denied on the actual supported deployment? Prior Linux
   parent-death observations do not cover the setup race or escaped descendants;
   this probe adds no watchdog and cannot claim to cover them.
3. **Provider acceptance route:** Can an explicitly selected, separately authorized
   Provider/deployment supply complete durable per-attempt accounting and terminal
   reconciliation, including retries and lost-ID submission windows? If it cannot,
   what architecture restriction or acceptance blocker follows? Source evidence
   from the resolved Provider investigation remains the baseline, not a live test.

Also not exercised: session summaries, async execution owners, actual invocation
budget/deadline integration, complete wire types/ordering, sustained contention,
cache exhaustion, permission/disk-full/power-loss behavior, backups and migrations.
Existing storage/lifecycle/compatibility decisions still own their policy choices.

## Review checkpoint

Recommend retaining these as useful **partial local observations**, leaving this
ticket open for live review and leaving the architecture gate blocked. Decide with
the human whether the sharp questions above become separate map children rather
than attempting to label a single-session prototype complete. Do not update the
map's Decisions-so-far or close this HITL ticket without that exchange.
