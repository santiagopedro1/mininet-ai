# Owner-bootstrap toy fixtures — THROWAWAY GH-99, NEVER MERGE

2026-10-22. Final experiment before the human paused this investigation in favor
of narrowing #38. No further Ollama/runtime work is authorized. All assets on this
branch are archival evidence, not production changes or a merge candidate.

## Authorized scope and captured evidence

The human approved two harmless user-service fixtures, each <=60s/128MiB/16tasks,
controller <=128MiB/2min, with temporary setup authority only in new owned
user/network namespaces. Configure private loopback in its owning namespace
before entering the unchanged final guards; drop every capability before toy work.
No acquired-code execution/permission changes, model loading, inference, external
network/download, host elevation/firewall changes, guard removal or deletion.

- [Executed fixture tool](ollama_containment_owner_bootstrap_fixtures_prototype.py),
  SHA-256 `f358b5d4744415b4c268f163f5e85cc0c93f1b5db1c88f4889444edebc7faed6`.
- [Full observations, commands, process identities and exit witnesses](ollama-containment-owner-bootstrap-fixtures.observations.json).
- Trusted construction helper remains pinned at
  `a0065e77b60ca3b31cba8afa9119aaf77334e4850b95c4d6c995fcf71364845d`.

Executed once:

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_owner_bootstrap_fixtures_prototype.py
```

## Bounded result: 21/21 checks pass

Elapsed **0.286 seconds**. Newly created namespace-local setup user0 mapped to
host UID1000, not host root. Kernel ownership was checked before enabling only
the newly owned loopback (flags8->73). Temporary namespace-local setup capabilities
were recorded. Neither host network nor an existing daemon was used.

The worker verified the simulated Provider's PID birth/cgroup/network identity,
joined its **network-owning setup user namespace**, then entered its own final
guarded application sandbox. Both retained --disable-userns and
--assert-userns-disabled; every final inheritable/permitted/effective/bounding/
ambient capability mask was0 before toy traffic. Final user/PID/mount/cgroup views
were separate while their private network was shared. Controller namespace-owner
observations independently matched both applications to the same new owner.

Observed /build unavailable, fresh empty /work as CWD, hidden host home/run/sys,
only basic device nodes, acquired package read-only and server/runner files0600.
Unit-local loader-environment unsetting preceded env-i; controller bus locators
were observed absent in both applications. Direct fixed nonce-correlated TCP toy
request/reply and exact listener-FD inode ownership were observed. TEST-NET probes
inside the private network returned ENETUNREACH; no outward route was observed.
This is not a universal egress or adversarial-host proof.

Separate simulated-role invocations:

- Provider fixture `8f66924af7a84f31b5807e4b1a08fe0d`, three sampled processes
 35120/35122/35123, sampled cgroup memory peak14,270,464 charge-accounting bytes.
- Worker fixture `626b42c72aa34a8f87aae148c52937d8`, three sampled processes
 35127/35129/35130, sampled memory peak13,291,520 charge-accounting bytes.

Both observed MemoryMax128MiB/TasksMax16/RuntimeMax50s/NoNewPrivileges/no-restart.
Provider-fixture stop returned0; all its sampled processes exited and its cgroup
was removed. The same worker invocation/PID birth continued without replacement,
with unchanged private namespaces and zero capabilities. A diagnostic connection
to the old toy endpoint returned ECONNREFUSED, and the old listener inode was
absent from that private network's LISTEN table. Worker stop then returned0 with
its sampled processes exited and its separate cgroup removed. Direct stderr was
empty for both. No journal read or resource-limit increase occurred.

Controller observed128MiB address-space limit, own VmHWM22,844KiB/VmPeak34,340KiB.
These are bounded fixture/process/cgroup observations, not a host memory
reservation, timed escalation guarantee or complete startup-history trace.

## Explicit limits and session disposition

**Ollama never ran. No model was loaded and no native inference occurred.**
This experiment validates only the selected harmless topology, fixed toy exchange,
search-path filesystem constraints and enumerated independent-stop observations.
It does not establish real server/runner/backend loaded-byte identity, native
terminal semantics, complete Agno learning/persistence composition, ambiguous
request containment, controller loss, device cessation or architecture acceptance.
There was no actual experiment trial, reasoning replay, model-store migration or
change to existing production persistence/lifecycle behavior.

The user explicitly agreed to narrow #38 instead: clearer output/failure summaries,
one obvious results directory, consistent understandable evidence files and fewer
user-facing storage options, preserving existing persistence/lifecycle behavior.
Database removal/consolidation and Provider containment are deferred; uncertainty
must be reported honestly without promising new Provider guarantees.

The user requested cleanup and a handoff for a new session to perform that scope
reset. This branch's evidence is retained remotely, **never merged**. Local
downloaded/extracted payloads, generated private fixture artifacts and only this
session's identified temporary files/failed-unit records may be removed under
that explicit cleanup authorization. Historical observations describe the files
and services at capture time, not promises that local scratch still exists.
The private full corrected-failure receipt may be discarded during cleanup; its
previously published explicitly redacted copy and original raw hash remain.

Validation before archival: source/helper/input hashes, local receipt equality,
21 predicates, namespace/capability/peer/inode correlation, both independent exit
witnesses and unchanged staged modes. No production tests or extra fixture run.
#99 is deferred, not validated/accepted. #38's body and dependency graph should be
reworked in the next session using the explicit user scope decision, not by
continuing these prototypes or silently weakening independent-trial guarantees.

## Cleanup completed under explicit human authorization

[Cleanup receipt](ollama-containment-session-cleanup.observations.json) records
the verified archive-before-disposal commit and exact removed paths/unit actions.
All six previously created unit names were checked: none was running; the single
remaining failed-unit record was reset only after its invocation ID matched.
Every final unit state was inactive/not-found, MainPID0, no ControlGroup.

Before removal, downloaded/staged files were rehashed against their receipts and
directory inventories checked for unfamiliar entries. The session's six private
artifact folders (about **4.38 GB logical payload**, not a measured filesystem
space claim), remaining known temporary capability folder and now-empty dedicated
parents were removed. The untracked private corrected-failure receipt was removed
only after checking its original hash against the retained public/redacted record.
The first temporary capability folder was already absent; no missing evidence was
invented. No other session, existing run data, learned memory, software installation
or unrelated service was touched. Remote evidence was retained, not merged.

Future reproduction needs new explicit authorization and fresh acquisition; old
scratch paths and historical runtime grants are not current resources/permission.
