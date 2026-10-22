# THROWAWAY: transient systemd user-service containment

The human selected this candidate and explicitly authorized disposable transient
user units. This supplements the parent-death counterexamples, not production
supervision or architecture acceptance.

## Run

```sh
python mininet_ai/agents/agno/systemd_containment_prototype.py
```

Standard library only; requires Linux, cgroup v2, a running accessible systemd
user manager, and executable `systemd-run`/`systemctl`. There is no silent
fallback if these prerequisites fail. Does not test other deployments.

Each fixture has a UUID-named `mininet-ai-containment-prototype-*.service`.
The service main process is the disposable supervisor, **not the short-lived
systemd-run client**. Worker and tracked subprocesses inherit its cgroup.
Properties: `Type=exec`, `KillMode=control-group`, `SendSIGKILL=yes`,
`TimeoutStopSec=1s`, `RuntimeMaxSec=15s`. These times are experimental controls,
not production defaults. The manager establishes the service boundary before
the fixture code starts; this last statement describes the candidate mechanism,
not a newly exercised arbitrary pre-exec race.

The harness records properties, cgroup membership, process traces and source
hashes. It observes process exit and an empty/removed cgroup, rather than
equating successful signal delivery or an inactive unit with quiescence.
Finally it stops/resets only its own UUID units. It creates no persistent unit
files, invokes no sudo, and touches no unrelated services. Temporary trace data
is removed only after fixture exit checks.

## Observed: three groups, fifteen checks

Host: Linux 7.2.11-arch1-1 x86_64, glibc 2.44, Python 3.14.8, systemd
262.1-1-arch. Exact observations and commands are in the companion JSON.

1. Kill the service main process while the worker waits before watchdog arming.
   Worker never reaches ready; worker and supervisor exit; cgroup disappears.
2. Kill main after a ready worker has created a tracked child and grandchild.
   The descendants deliberately ignore TERM. Unlike the bare parent-death
   fixture, all processes exit without the harness issuing a group KILL;
   the manager finishes service cleanup and the cgroup disappears.
3. Explicitly stop the service with those descendants active. All exit and the
   cgroup disappears. The unit reports `Result=timeout`, **not successful
   cleanup status**. Exit verification and cleanup failure reporting are separate.

The main-loss cases report `Result=signal`. Neither a failed unit nor committed
effects are rewritten as successful execution. The main-loss/stop descendant
cases take about a second on this host, consistent with the configured stop
timeout and KILL escalation; no per-signal kernel audit trace is claimed.

## Remaining acceptance gates

This is evidence for an independent manager cleaning an owned cgroup while the
manager remains alive. The probe harness also remains alive to observe results.
It does not establish behavior if the manager/user session/host disappears,
controller ownership cannot be correlated, a process cannot be killed, `/proc`
or cgroup observations fail, or the fixture changes cgroup/session membership.
Tracked fixtures do not attempt cgroup escape; arbitrary Python is not sandboxed.
Full unit startup/exec failures and creator-thread behavior under this new
candidate are not exercised. The service-main-process requirement is essential:
loss of an arbitrary external CLI is not tested as equivalent to main loss.

There is no real Agno operation, SQLite/database/sidecar, persistent gate,
durable admission latch or action executor here. Cancellation during admission,
reasoning and outcome phases, active siblings, lost context/no replacement,
preserved committed learning/outcomes and deliberately unverified-exit denial
remain unvalidated. No remote Provider termination evidence is obtained.

**Proposed verdict for live review:** retain bounded positive cgroup cleanup
evidence alongside the earlier negative parent-death evidence; keep deployment
acceptance open for remaining gates. Do not infer successful architecture
acceptance or authorize production implementation from these observations.
