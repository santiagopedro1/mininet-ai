# THROWAWAY: offline Agno phase cancellation inside a transient service

Live human review pending. This extends the accepted bounded systemd cleanup
evidence, without accepting the architecture or implementing supervision.

## Rerun

```sh
.venv/bin/python -P mininet_ai/agents/agno/systemd_phase_prototype.py
```

Requires the existing locked environment and Git object
`d96928ecd8cb608b69942e4fb08c957134621b28`. Four unchanged helpers from the
accepted admission-failure probe are extracted from that object into a fresh
`/tmp/opencode` directory. There is no fetch, dependency install, Provider client,
credential lookup or network access. Their hashes are captured. `-P` is required
to avoid the sibling `logging.py` shadowing Python's standard library.

Each case creates an authorized UUID transient user service with the fixture
supervisor as main PID, the real existing Agno worker, and its resource tracker
inside the service cgroup. Properties: Type=exec, KillMode=control-group,
TimeoutStopSec=1s, SendSIGKILL=yes, RuntimeMaxSec=30s. Experimental times only.
Only probe units are stopped/reset. Scratch files are discarded after cleanup.
No production modules, ADRs, actual stores or persistent units are changed.

## Observed versus modeled

Four case executions, 49 checks, with **overlapping coverage**:

- `admission` and `reasoning` deliberately repeat the same composite scenario:
  real automatic learning is held in a scripted model barrier; a memory-free
  sibling executes on another owner and waits before finalization; a command
  queues behind the first owner's blocked call. A same-worker gate-probe reply
  establishes that the queue command reached the worker loop. Stopping the
  service exits all observed processes, leaves the queued operation unstarted,
  and produces no active/sibling operation terminal. This is not separate proof
  of cancellation at every foreground reasoning boundary or every admission race.
- `outcome` holds after a real session outcome update committed but before its
  terminal. The `known-installed` outcome and earlier seeded Learned Memory
  survive whole-service shutdown. The sibling and queued operation are retained
  as interrupted/missing-terminal evidence, not promoted to success. The action
  result is a fixture input, not a network action execution.
- `unverified` repeats blocked learning, but withholds observed exit from a
  **modeled controller policy**. Even though the host has actually stopped and
  its flock is available, the policy denies independent conflicting work. This
  tests the small policy's choice with missing evidence, not real unkillable
  processes, broken systemd, failed `/proc` or durable quarantine recovery.

Agno 3.0.11, SQLAlchemy 2.1.0 and SQLite 3.54.0 are real. Learning/session writes,
owned executor threads, queue behavior, flock denial while active, process exit,
cgroup emptiness and SQLite integrity checks are observed. There is one physical
SQLite database per scratch case, with WAL/SHM sidecars. Actual root-gate ownership
comes from the pinned worker. It remains held while work is active and becomes
physically available when the owner exits; a dead process cannot retain flock.

`Quarantine` is **new modeled coordinator policy**, not wired into the production
coordinator or baseline worker admission latch. Its fail/exit booleans deny late
admission, failed-run replacement and new conflicting local work before exit
verification. No refused late command is sent to the underlying worker. Killing
queued work is not proof that the worker itself refuses cancellation-era commands.
The file command/reply bridge is disposable, not crash-durable IPC.

Independent eligibility means local eligibility in this no-Provider fixture only.
It never grants independent trial admission with unknown remote work. Remote
Provider status is outside this model and remains a separate blocking condition.

The service can be unloaded immediately after stop. When LoadState is not-found,
subsequent `show` values are manager defaults, **not retained unit properties or
proof of successful cleanup**. The capture retains the pre-stop configuration,
actual process/cgroup observations, stop command result and post-stop LoadState.
No lost unit failure cause is invented. Durable cleanup-status retention remains
a requirement, not validated by an inactive/unloaded unit.

## Corrections during the experiment

- Initial direct invocation without `-P` failed on standard-library logging
  shadowing, before any service creation. The documented command fixes it.
- An initial outcome inspection found no session: a memory-only seed does not
  establish an Agent Session. The fixture now performs successful real session
  reasoning before attempting an outcome update. The final capture verifies the
  committed session outcome explicitly. Earlier incomplete runs were cleaned up;
  they are not successful observations in the final capture.

## Remaining gates

No real coordinator cancellation latch, persistent admission quarantine,
worker replacement implementation, gate transfer/recovery policy, all-phase
race enumeration, actual failed exit, manager loss or startup/exec failure
validation. No arbitrary extension containment, hostile-code sandbox or remote
termination guarantee. Foreground/background evidence remains incomplete on
abrupt death; missing operation terminals remain unknown and failed-run scoped.

Proposed live review: retain these bounded runtime observations and modeled
denial checks, but keep deployment acceptance open. Decide whether the modeled
controller boundary is sufficient for this decision or requires an explicitly
integrated follow-up. Never silently count it as production evidence.
