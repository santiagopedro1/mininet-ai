# THROWAWAY: integrated cancellation and quarantine

Live human review pending for **Validate integrated service cancellation and
quarantine boundaries**. This is an offline prototype, not production admission,
supervision, a migration, or architecture acceptance. Keep this branch unmerged.

## Reproduce

```sh
.venv/bin/python -P mininet_ai/agents/agno/integrated_quarantine_prototype.py
```

Requires the existing Python environment, Git object
`d96928ecd8cb608b69942e4fb08c957134621b28`, Linux `/proc`, cgroup v2 and an
accessible systemd user manager. The human explicitly authorized these UUID
transient fixture services. No sudo, persistent unit configuration, existing
daemon access, network/Provider calls, credentials or model downloads.

Scratch roots are created only under `/tmp/opencode` and discarded after cleanup.
Four historical helper sources are extracted from the immutable Git object.
Three execute unchanged. The admission helper is transformed by exact-count
source replacements into a clearly throwaway scratch variant. Both baseline and
executed hashes are captured; the variant can be regenerated from this script.
No historical branch or production module is patched. This mechanism is a cheap
probe seam, not a proposed plugin/patch distribution architecture.

## Actual integrated boundary

- The transient service main process is the disposable **controller bridge**.
  Its real Agno worker and multiprocessing resource tracker belong to its cgroup.
- Commands traverse file requests, the bridge and a real multiprocessing pipe.
  Controller submissions read the scratch database before forwarding. An explicit
  fixture-only bypass sends adversarial late commands directly to the worker,
  establishing worker refusal independently of controller refusal.
- The worker accepts a real cancel command and sets its existing failure latch.
  Admission consults the durable quarantine as well. Short SQLite dispatch
  transactions serialize dispatch authorization against cancellation commit;
  worker execution counters transition inside that boundary. A command authorized
  before cancellation is executing work to drain/contain, even if its next Python
  instruction runs later. Queued work is not relabeled as executing on cancellation.
- Cancellation commits quarantine and a failed-run tombstone before its bridge
  acknowledgement. Active operations consult that durable state before producing
  actionable results. Drained operations may have locally successful work but are
  run-failed/non-actionable. No whole-operation rollback is implied.
- The same scratch database holds the baseline evidence/memory/session tables,
  controller audit, quarantine and failed-run tombstones: one physical SQLite
  database per case, WAL/SHM sidecars. Full synchronous transactions provide the
  prototype's same-host commit boundary, not a power-loss/storage-stack proof.
- Admission is bound to one controller process identity (PID plus `/proc` start
  tick). Startup ownership handoff is single-use. A fresh controller cannot adopt
  the old run simply because its durable state was still open at controller death.
  Failed-run tombstones prevent reuse after a successor generation is reserved.
  Host reboot/PID-namespace changes and adversarial identity manipulation are not
  validated by this cooperative same-host fixture.
- Quarantine release requires matching generation and stored systemd InvocationID
  plus trusted observations of process exit and empty/removed cgroup. Missing,
  wrong-incarnation and contradictory observations do not clear it. The harness
  retains the service/cgroup witness; a restarted process demonstrates **denial**,
  not autonomous reconstruction of all lost observer context or permission to
  resume reasoning.

The database schema, file bridge and ownership handoff are disposable probes, not
public schemas, production IPC, compatibility policy or the final lifecycle design.

## Captured runtime

Python 3.14.8; Agno 3.0.11; SQLAlchemy 2.1.0; SQLite 3.54.0;
Linux 7.2.11-arch1-1 x86_64 / glibc 2.44; systemd 262.1-1-arch.
Exact platform strings, helper/current source hashes, commands, service properties,
InvocationIDs, PIDs, request/reply inventories, worker/controller event ordering,
retained memory and filenames are in the companion observation JSON.

Eight cases, **179 checks**, all true in the final capture. These are bounded
observations, not 179 independent guarantees or a scheduling proof.

| Case | Forced boundary and observation |
| --- | --- |
| Startup | Worker pauses before ready. Controller commits cancellation and refuses a late submission; released startup is rejected and never reaches ready. |
| Admission | Operation waits before actual dispatch. Cancellation causes it and a queued operation to be refused at dispatch; no reasoning for either starts. |
| Foreground | A real Agno call blocks inside the scripted foreground model invocation. A memory-free sibling and queued command exist. On release, active/sibling results are non-actionable and queued dispatch is refused. |
| Background | Real automatic learning blocks in its owned scripted learning call. Cancellation closes both admission boundaries; draining learning/sibling work cannot produce actionable success. |
| Outcome | Real session outcome update commits, then pauses before terminal. Its known-installed outcome and seeded learning survive cancellation; queued work is refused. This is a fixture action result, not real network execution. |
| Controller loss | Kill the service main process after cancellation committed, with blocked work outstanding. systemd contains observed processes; a fresh process still denies conflicting service launch. |
| Controller loss before cancel | Kill main while active and before quarantine commit. A fresh process attempts to launch a harmless same-run marker service through the actual launch gate: ownership binding refuses it even though state remains open. Recovery records failure/quarantine, never adopts the old worker. |
| Healthy | B/C complete a real two-party model barrier on distinct owner threads. A later B call retains the same Agent object and owner. Dispatch transactions do not serialize entire reasoning operations. |

Every case then rejects missing, stale-incarnation and contradictory exit
observations. With the physical flock available after exit, a **fresh process**
invokes the actual guarded launch function and is denied. No marker process starts.
After delayed valid observation, that same gate allows a harmless marker service
for a **new fixture generation**, not a replacement Agno cache. The old failed run
remains denied. This positive control establishes that the gate is not merely
hard-coded to reject every process. It does not authorize a real independent
experiment while remote work is unknown; no real Provider work exists here.

Controller audit transactions show no dispatch authorization after cancellation
commit. Request/reply inventories show actual refusals, one worker incarnation and
no duplicate operation terminals. These observations and the failed-run launch
checks are stronger than absence of an event named replay, but do not certify
arbitrary extensions or hidden remote retry behavior.

## Gates and cleanup

The real worker-owned flock denies another process while learning-capable work is
active. Drained quiescence can release it before service exit; abrupt worker exit
also necessarily releases an OS flock. The **durable quarantine**, not a fiction
that dead processes retain locks, continues to deny conflicting service admission
until the observer supplies valid containment evidence.

Service properties are Type=exec, KillMode=control-group, TimeoutStopSec=1s,
SendSIGKILL=yes, RuntimeMaxSec=45s; harmless successor services have a five-second
runtime cap. These are experiment controls, not selected production defaults.
The harness observes recorded fixture PIDs non-running and cgroup empty/removal,
then stops/resets only its UUID units. Post-unload systemctl defaults are not used
to manufacture retained unit Result evidence. Full durable cleanup-cause retention
is not proved by successful stop or empty cgroup.

## Limits and remaining handoffs

- Exit faults are injected at the integrated reconciliation boundary while real
  fixtures remain safely containable. No genuinely unkillable process, unavailable
  systemd manager, host reboot, device execution or broken `/proc` is exercised.
- The observer is trusted. Arbitrary fabricated observations, reused cgroup paths,
  malicious filesystem/identity changes and proof against arbitrary Python are
  not covered. Cgroup membership of these owned fixtures is not a sandbox claim.
- Controller-loss denial survives process restart in the scratch database.
  Autonomous recovery of observer/service identity after loss of **all** witness
  holders, writer failure, torn records, filesystem failure and reboot remains
  unvalidated; do not infer automatic recovery or resumed reasoning.
- No actual tool-launched subprocess is added here. Tracked TERM-resistant child/
  grandchild containment remains the earlier dedicated fixture evidence, not an
  assertion that every tool automatically registers itself.
- An outcome barrier is after a real commit, not every instruction of a SQLite
  write. Abrupt write/crash reconciliation and required-evidence failure retain
  their separate accepted boundaries; this probe does not supersede them.
- Startup here means the controlled pre-ready barrier. Exec failure, service
  creation failure, creator-thread behavior under the service boundary and
  manager/user-session lifetime remain with the parent deployment decision.
- No Provider status, server/runner/backend containment, live inference or remote
  quiescence evidence exists. Unknown remote work still blocks independent trials.

Proposed live review: accept only the enumerated integrated offline surface,
retaining the parent deployment and Provider gates; alternatively require a
focused follow-up for any uncovered guarantee. Ticket closure must never upgrade
these exclusions into successful architecture acceptance.
