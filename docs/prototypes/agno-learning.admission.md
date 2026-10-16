# Immediate admission shutdown after persistence failure probe

**Throwaway, offline, not production. Do not merge this branch.**

This follows the [first learning-gate investigation](agno-learning.md) and the
[integrated stable-Agent learning probe](agno-learning.integrated.md) for
[Validate complete learning-gate and strict persistence coverage](https://github.com/santiagopedro1/mininet-ai/issues/58).
It addresses **item 2 only**, tracked as
[Validate immediate admission shutdown after persistence failure](https://github.com/santiagopedro1/mininet-ai/issues/72):

> Can the long-lived per-run worker stop new admission at the first
> required-persistence failure while other operations are active, safely drain or
> contain admitted work, and report failure without releasing the learning gate
> early, losing known partial effects, or serializing away required independent
> concurrency?

Item 1 (mixed supported routes through a stable cached Agent) is covered
separately by the integrated probe. Real Python extension eligibility (item 3) and
[evidence crash/reconciliation](https://github.com/santiagopedro1/mininet-ai/issues/60)
remain unresolved. Positive local observations do not accept the architecture or
prove remote Provider quiescence.

## What changed

The new `admission_failure_prototype.py` worker reuses the existing offline
fixtures (`StrictDb`, `TrackedExecutor`, `OfflineOptimization`, the scripted
memories/gate/adapters and the overlap model). It adds a **failure latch** and a
per-command **dispatch boundary**:

- `LatchDb.checked` wraps the required persistence call. When a required write
  raises, it journals the failure and, in `write` mode, synchronously **arms the
  latch at the failing write**, before Agno can swallow it.
- The worker's command loop checks the latch **at admission** (before a command
  takes a queue slot) and again **at dispatch** (inside the owner thread, before an
  already-queued command starts executing).
- A worker-owned root-wide `WorkerGate` is held from the first memory-capable
  admission and released only by a quiescence monitor once both `executing` and
  `queued` reach zero. It is never released per operation, so a late failure
  cannot unlock it early.

Each operation is modeled across four states — `new -> queued -> executing ->
terminal` — plus `refused-at-admission` (before a slot) and `refused-at-dispatch`
(already admitted, not yet started). Three configurations isolate which mechanism
is load-bearing:

| Config | Latch armed | Dispatch check | Purpose |
| --- | --- | --- | --- |
| `write+dispatch` | at the failing write | yes | proposed boundary |
| `write,no-dispatch` | at the failing write | no | is the dispatch check load-bearing? |
| `finalize+dispatch` | only at operation finalization | yes | is write-armed timing load-bearing? |

### Why the latch must be armed at the write

Pinned Agno 3.0.11's `Agent.run` waits for the automatic memory future via
`wait_for_open_threads` (`agno/utils/agent.py:70`), which **catches and logs** that
future's exception. A required write failing inside background learning therefore
does not surface on the run's return value. The adapter must observe and re-raise
the failure (`StrictDb.checked`) and arm the latch itself; recognizing failure only
at operation finalization leaves a window in which new work is admitted.

Because a genuinely hanging Agno memory future would hang `run_route` itself,
containment is modeled with a `hold_forever` fixture option rather than a real
stuck Agno thread.

## Sequence and observed evidence

The captured report records **ten groups and 47 passing observation checks**. Exit
code is zero; `failed checks: none`.

- **Proposed boundary (`write+dispatch`).** The first failure is marked with
  `source == "adapter"` (latch armed at the write) and precedes every terminal
  event. A memory-free operation queued before the failure is
  `refused-at-dispatch`; a command arriving after the failure is
  `refused-at-admission`. No operation starts after the failure.
- **The dispatch check is load-bearing.** With `write,no-dispatch`, the operation
  that was queued before the failure **runs after** it (`queued_runs_after_failure`
  observed) — its reply is `success=True` but `run_failed=True`,
  `actionable_success=False`.
- **Write-armed timing is load-bearing.** With `finalize+dispatch`, a command that
  arrives in the finalization window — after the required write already failed but
  before errors were inspected — is **admitted** and returns
  `success=True, run_failed=False, actionable_success=True`. That reproduces the
  GH #58 gap: an actionable success is reported after required persistence failed.
  Under `write+dispatch` the same command is refused at admission.
- **Concurrent active sibling.** At the failure two operations were executing
  (`executing >= 2`). No operation starts after the failure and late work is
  refused at admission. The already-executing memory-free sibling **drains** and
  returns `success=True` with `run_failed=True` / `actionable_success=False`, so
  its known partial effect is contained without being reported as a clean success.
- **Failure classes.** `upsert_user_memory`, `upsert_session` and `upsert_run`,
  each as `raise` and as `missing`, plus a memory-free session fault, all latch
  and deny post-failure admission.
- **Evidence-write failures.** Both simulated evidence faults latch and deny
  post-failure admission, but the durable cause evidence is **absent** for the
  `db-error` case: the latch fires while the failure cannot be recorded. Latching
  is not the same as presenting recoverable evidence.
- **Quiescence releases the gate.** After the latch and a quiescent drain
  (`{executing: 0, queued: 0}`), the root gate is acquirable by another process,
  yet late commands are still refused — the gate release is tied to verified
  quiescence, not to the failure.
- **Containment retains the gate.** When admitted work cannot reach local
  quiescence (`hold_forever`), the worker declares containment
  (`executing >= 1`), a cross-process gate probe is **denied**, the worker refuses
  `stop`, and the process is still alive locally until the controller kills it.
- **Healthy control.** With no failure there is no latch, every reply is
  `success=True` / `actionable_success=True`, and the two overlap operations ran on
  **three distinct owner threads** — a global serial command loop would fail this.

## Rerun and provenance

With the repository's installed locked development environment:

```bash
.venv/bin/python -P mininet_ai/agents/agno/admission_failure_prototype.py --scratch-parent /tmp/opencode/af-run --report /tmp/opencode/af-report.json
```

The report destination must not exist. Each invocation creates a fresh disposable
`PROTOTYPE-wipe-me-admission-*` root under the scratch parent; it never opens a
production or previous-run store. All responses are scripted and Agno execution
and SQLite are real. No Provider client, credential, network call, provisioning,
production implementation, migration or ADR change is required.

`agno-learning.admission.observations.json` captures runtime/package inventory,
SQLite source ID/compile options, six relevant source hashes, per-group events,
replies, per-identity partial-effect scopes, `journal_mode`/`synchronous`/
`integrity_check` pragmas, live filenames (including WAL/SHM sidecars), worker exit
codes and the observation checks. Every group reports `integrity_check=ok`, a live
WAL sidecar and a clean exit; committed partial learning (for example the healthy
group's `late-background-fact`) remains present rather than rolled back. Captured
runtime is Python 3.14.8, Agno 3.0.11, SQLAlchemy
2.1.0 and SQLite 3.54.0. This is not a deployment binary, hardware, power-loss or
remote-termination audit.

## Human-review boundary

These are actual offline Agno/SQLite observations, with a modeled worker/controller
and fixed trusted hooks and tool — not Mininet production integration or a
deployment supervisor. The latch and dispatch check are prototype control-flow, not
a proposed production API. Specifically not established here:

- Remote Provider termination or descendant containment. Local drain/exit is not
  remote termination; the containment group reuses the existing prototype
  kill/join fixture and does not meet the deployment ticket's containment contract.
- Durable evidence of the failure when the evidence write itself fails (the
  `db-error` case latches but cannot present the cause).
- Crash recovery, restart reconciliation, retries, duplicate handling or schema
  migration.
- That an unresolved remote operation blocks the next independent trial.

Live review should decide whether this bounded synchronous boundary — adapter-armed
latch at the failing write plus admission and dispatch checks, with
quiescence-gated release and contained non-actionable drain — **suffices** for
item 2, needs explicit narrowing or a follow-up, or returns a validation failure.
Missing coverage above cannot clear acceptance on its own, and this cannot accept
the architecture or close the parent learning-gate ticket.
