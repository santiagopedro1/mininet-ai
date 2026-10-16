# Same-file evidence crash and reconciliation probe

**THROWAWAY, offline, not production. Do not merge this branch.**

For [Validate same-file evidence crash and reconciliation boundaries](https://github.com/santiagopedro1/mininet-ai/issues/60).
**Live human review is pending. This document is evidence, not a resolution or
architecture acceptance.** Existing accepted integration, failure-admission and
extension verdicts are not reopened or broadened by this probe.

## Question and agreed seam

Can one scratch SQLite file retain enough correlated evidence to distinguish
recorded historical outcomes from failed/unknown gaps across abrupt worker or
controller death, without replay, resumed reasoning, replacement workers,
fallback databases or whole-operation rollback?

The human agreed to a standalone offline Python CLI, JSON report and findings;
real subprocess deaths and SQLite commits; scripted models; and read-only
reconciliation rather than restarting the run. A recorded historical outcome is
not authorization to continue a failed run or start another independent trial
while remote work remains unresolved.

- [Probe](../../mininet_ai/agents/agno/evidence_crash_prototype.py)
- [Captured observations](agno-learning.evidence.observations.json)
- Existing helpers: `admission_failure_prototype.py`, `learning_gate_prototype.py`,
  `integrated_storage_prototype.py`, and `storage_prototype.py`.

## Candidate being tested (modeled policy, not a production API)

1. A controller commits admission before dispatch and creates a spawned worker.
2. The worker owns the learning flock; its separately owned Agno adapter commits
   a memory fact, performs scripted reasoning and session/run persistence, and
   starts tracked background memory work. All use the same physical database.
3. A foreground acknowledgement does **not** mean background completion or terminal
   success. The worker drains tracked work before committing a terminal outcome.
4. A terminal commits before a reply. The controller separately persists receipt.
   Failed evidence writes produce only a volatile diagnostic where no durable
   error could be committed; they cannot produce a new terminal success.
5. The operation slot is selected storage identity / run / operation ID. Admission
   retains the full canonical command, including session, agent, kind, scope,
   profile and payload. Protocol records bind its SHA-256; terminals also retain
   the command, result and result hash. These are synthetic fixture identities,
   not a proposed production identity/schema policy.
6. Read-only reconciliation requires exactly one matching admission and terminal,
   full command/result correlation, and ordered, uniquely matching dispatch,
   acknowledgement and drain evidence. Other operations in the same file do not
   invalidate that operation's record. Missing, ambiguous or invalid evidence is
   unknown, never inferred success from materialized memory/session/run rows.
7. Identical in-flight duplicates are held/rejected without executing; conflicts
   for an existing operation slot are rejected. A completed identical duplicate
   may return the recorded historical outcome with `actionable=False`. No worker
   is reconstructed and no new reasoning or action is dispatched by reconciliation.

The table is deliberately an experimental ledger, not an implementation of the
future evidence schema or an exactly-once execution guarantee. Hashes are not
authentication against an actor rewriting the database and all hashes together.

## Observed coverage

**86 experiment groups / 1,123 passing observation checks.** These checks include
per-group invariants and conditional controls; they are not 1,123 independent
durability guarantees or new regression tests.

### Real process death: 62 groups

The outer harness pauses actual processes at explicit barriers and sends SIGKILL
to either the worker or controller. It captures PID/exit observations and uses
Linux pidfds to verify the known worker's exit, including an orphaned worker.

- Before/during/after admission, before/after dispatch.
- Before/after each first required memory/session/run write, and separately
  before/after its evidence acknowledgement. Thus a write committed without its
  acknowledgement is a distinct captured window.
- Before/after foreground acknowledgement, before background drain, before/after
  the real tracked background memory write, and after drain.
- Before/during/after terminal persistence, before reply, before/during/after
  controller receipt persistence, and after reply.
- Before/after durable error recording after an injected required-write failure.
- No worker-death case is invented before a worker exists.

"During" admission/terminal/controller-receipt means the evidence INSERT ran but
the transaction had not committed. It does not mean arbitrary instruction-level
interruption inside SQLite or Agno's own commit implementation.

Controller death with an active worker does not certify quiescence: the worker
remains deliberately blocked, and its gate is observed unavailable. The outer
harness explicitly kills that exact known worker, verifies exit, and only then
checks the gate is available. It never supplies a replacement worker. This is a
local experimental containment fixture, not a deployment supervisor contract.

### Required write/evidence failures: 14 groups

- Actual read-only SQLite write rejections at admission, dispatch, adapter write
  acknowledgement, foreground acknowledgement, drain evidence, terminal, error
  recording, and controller receipt.
- An injected required memory-write exception with durable error/failed terminal.
- Actual SQLite COMMIT rejection at admission, acknowledgement, terminal, error
  and controller receipt: an intentionally invalid deferred foreign key allows
  the INSERT but makes COMMIT fail and rolls back that evidence transaction.
  The guard tables live in the same scratch file. This is a constraint-induced
  commit failure, **not** a simulation of disk-full, fsync, torn-write or power loss.

Agno can swallow adapter evidence errors and still attempt later persistence;
the scratch strict adapter retains those errors. The experiment does not turn
Agno completion into clean success. Already admitted work may leave committed
effects during failure/drain; no transaction spanning the whole operation exists.

### Damaged evidence: eight groups

After a healthy operation, separately remove a terminal or acknowledgement;
damage terminal JSON; duplicate a terminal; alter the command or result; substitute
an acknowledgement hash; or move drain evidence after terminal. All reconcile to
unknown, even when a volatile success reply had previously been observed.
These are controlled row mutations, not a physical-corruption recovery audit.

### Healthy and concurrent controls: two groups

The healthy full operation retains foreground and background memory facts, session
and run rows, a correlated terminal, controller receipt and one reasoning event.
Duplicates are sent over the controller IPC boundary while the original operation
is paused before drain and after terminal; the controller returns no-replay or
historical/conflict responses rather than invoking the model again.

Two real memory-free Agno invocations complete a two-party model barrier on
different owner threads with distinct Agents/connections and isolated sessions.
Both commit admission/dispatch/ack/drain/terminal records to the **same file** and
reconcile independently. This does not globally serialize independent reasoning.
It is a bounded threaded-owner control, not a multi-operation deployment stress
test or a new failure-latch timing test.

## What can be established without replay

| Durable evidence | Reconciliation | Observed partial effects |
| --- | --- | --- |
| No admission, or admission without terminal | Unknown; no dispatch/replay from reconciliation | None, or foreground/background memory and session/run rows depending on the death window |
| Durable error but no terminal | Unknown completion; the failure fact itself remains available | Snapshot and error records remain separate facts |
| Valid failed terminal | Recorded failure, never actionable success | Previously committed effects are not rewound |
| Valid success terminal; reply lost or controller receipt fails | Recorded historical success, `actionable=False` | Both memory facts and session/run effects remain |
| Missing, corrupt, conflicting or uncorrelated terminal/protocol evidence | Explicit unknown gap | Existing effects cannot substitute for missing evidence |
| Error/evidence writer cannot commit | Missing durable record, plus only the volatile error if observed | No invented durable error or fallback store |

All inspected stores report `integrity_check=ok`. Ordinary crash windows preserve
earlier committed foreground and late background effects. Each group observes one
selected database; live stage inventories capture WAL/SHM sidecars and the separate
learning lock. The lock is not a second database. SQLite recovery during reopening
does not resume an Agent Session or run reasoning.

## Provenance and rerun

```bash
.venv/bin/python -P mininet_ai/agents/agno/evidence_crash_prototype.py --scratch-parent /tmp/opencode/gh60-rerun --report /tmp/opencode/gh60-rerun.json
```

The report must not exist. Only newly created `PROTOTYPE-wipe-me-*` roots are used;
no existing stores, credentials, network or Provider clients are needed. `--quick`
is a smoke subset, not the complete acceptance capture.

Captured runtime: Python 3.14.8 / Agno 3.0.11 / SQLAlchemy 2.1.0 / SQLite 3.54.0 on
Linux. The report contains SQLite source ID/compile options, five source hashes,
live file inventories, checkpoint order, durable records, partial-effect snapshots,
exit observations and individual checks. Worker connection observations are WAL
and synchronous=2 (FULL); evidence connections explicitly select FULL. Inspection
pragmas are labeled separately and are not substituted for writer observations.

Checks: targeted new-source Ruff and Pyright pass. Existing offline suite with
`MININET_AI_LIVE_TESTS=0`: **547 passed / 4 skipped / 179 subtests**. This is not a
whole-repository typecheck claim. No new tests or production behavior were added.
Two final full captures have identical five source hashes and all 86 group / 1,123
check maps; PID, timing and scratch-path values naturally differ.

## Exact limits and handoffs for live review

- The harness, controller protocol, ingress duplicate policy, canonical encoding
  and read-only reconciler are modeled. Agno execution, SQLite transactions,
  process kills, local locks and inspected rows are real; model responses and the
  required-write exception are scripted/injected.
- Checkpoints are bounded selected windows, not every possible instruction,
  arbitrary repeated crashes, production integration or a hardware durability
  audit. Mid-Agno-transaction kills, physical database corruption, disk/full-I/O
  faults, power loss, malicious record rewriting and arbitrary Python are not
  established here. Rejected COMMIT is observed through SQLite constraints only.
- This does not re-prove immediate first-error admission closure with concurrent
  siblings from [Validate immediate admission shutdown after persistence failure](https://github.com/santiagopedro1/mininet-ai/issues/72).
  Nor does it prove all those fixtures compose into one production coordinator.
  The concurrent control uses two in-process owners; crash groups use a spawned
  controller and one spawned per-run worker.
- No transparent replacement, resumed reasoning, replay, retries, fallback store,
  whole-operation rollback, or network action is exercised or authorized. This
  is evidence reconciliation after failure, not a run-resumption policy.
- Remote Provider completion, deployment startup/descendants and production
  containment remain with [Validate deployment worker startup and descendant containment](https://github.com/santiagopedro1/mininet-ai/issues/57)
  and the Provider gates. Local exit is not remote quiescence.
- Complete learning/integration reconciliation remains with
  [Validate complete learning-gate and strict persistence coverage](https://github.com/santiagopedro1/mininet-ai/issues/58).
  Schema, lifecycle, ownership, compatibility and migration policy remain with
  their existing tickets. No ADR or production implementation was changed.

**Review requested:** accept this explicitly covered local crash/reconciliation
boundary, retain partial evidence and name a sharper prerequisite, or return a
negative verdict. Missing guarantees cannot be cleared by closing this ticket;
architecture acceptance remains with its existing decision.
