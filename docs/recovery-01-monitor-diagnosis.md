# Ticket 01: final fault-check / monitor race

## Preserved symptom and scope

Started from clean `fix/recovery-01-review` after merging integration `5dede608`.
The parent's preserved `smoke-001/evidence.json` reports one successful action,
independent service recovery, verified cleanup, and experimental invalidity solely
from `fault_monitor_failed:`. All 147 retained fault checks confirmed both endpoints
down. Observation completed at tick 3152225655839; the last successful check ended
at 3152228107129. Stderr is empty. The original invalid evidence is unchanged.

These artifacts do not retain an exception type or traceback. They establish the
symptom and timing, **not conclusive identification of the historical exception**.
The confirmed implementation defect below reproduces that exact symptom through
the actual Mininet assertion. The parent owns the authorized live repeat; no VM,
network, Provider, tracker or original example was changed during diagnosis.

## Feedback loop established before hypotheses

```bash
/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python -m pytest \
  tests/examples/test_recovery_campaign.py \
  -k live_final_collection_drains_periodic_shell_check -q
```

The approved seam is configuration → `run_campaign` → exported evidence. The
fixture runs real `LiveRuntime.inject`, periodic monitoring, final collection and
cleanup. Mininet's real `Intf.isUp`, `Node.cmd` and `Node.sendCmd` are used with
controlled command I/O, not a real shell/network. Events pin the final read against
a due periodic read; controlled complete packet journals and one controlled repair
keep experimental conditions otherwise healthy. No private check helper is called
by the test to manufacture the race. Guards bound a broken cancellation drain.

Before production changes, the loop failed with the exact sole invalidity reason
`fault_monitor_failed:` while action/service success and cleanup stayed true.
The initial reproducer was red in 3/3 runs; replacing the shell simulation with
Mininet's actual command path retained the same red (~1.5 seconds per run).

## Ranked hypotheses, predictions and probes

1. **Concurrent final/periodic shell access.** If both readers share an interactive
   Mininet shell, holding the final read while a periodic read starts should hit
   `Node.sendCmd`'s `assert self.shell and not self.waiting`. Draining monitoring
   before the final read should remove both collision and invalidity.
   **Confirmed:** the real Mininet assertion reproduced the empty error; a temporary
   fixture-only ordering control made the same test green. The control was removed.
2. **Cancellation consumed during synchronous check drain.** If `_thread` consumes
   cancellation while retaining a terminal result, a monitor cancelled inside
   `isUp()` may restart instead of finishing. Moving cancellation earlier alone
   should then stall collection. **Confirmed by a second red:** the in-flight
   variant stalled until its one-second guard and lost essential measurements.
   Checking the monitor task's cancellation state prevents another sampling cycle.
3. **Independent inspection failure, rather than overlap.** If the command itself
   fails without any concurrent caller, serialization should not make the trial
   valid. Its exception type and phase must remain visible. **Control confirmed:**
   independent empty `AssertionError`s in periodic and final inspection still
   invalidate evidence, while retaining packet measurements and verified cleanup.
   New typed/phase assertions were red before adding the diagnostic records.

## Fix and evidence contract

- Stop and await periodic monitoring **before** a final check or network teardown.
  A check already running in a thread drains to its terminal result; cancellation
  cannot silently restart the sampling loop. This retains `_thread`'s existing
  terminal-result behavior for other operations.
- Each fault-check record retains `phase` (`injection`, `periodic`, `final`),
  `started_ns`, completion `tick_ns`, and endpoint observations. Failed checks retain
  `error.type` / `error.message` and unknown (`null`) endpoint/down-state evidence.
- Failure reasons include the exception type even when its message is empty.
  A failed final inspection does not discard collected packet evidence, nor can it
  establish fault persistence: the trial remains experimentally invalid.
- Shared best-effort cleanup-budget semantics and lack of a hard synchronous
  teardown deadline are unchanged. No validity rule was weakened.

## Validation and remaining gate

The four boundary/control cases pass: monitor sleeping or inside a synchronous
check, plus independent failures in each corresponding final/periodic phase.
Targeted campaign suite: **71 passed**. Full suite: **1,385 passed, 3 skipped,
179 subtests passed** (124 existing specification-deprecation warnings).
Repository-wide lint and full/scoped Pyright pass; changed-file formatting is
clean. The four-case feedback loop also passes in 3/3 repeated runs (~2.8 seconds
per run). No temporary debug instrumentation or ordering-control environment
switch remains.

The parent must merge/review the fix and run the authorized repeat with a fresh
evidence identifier, preserving `smoke-001`. Local mechanism validation is not a
live acceptance result, feasibility evidence or approval of development budgets.
