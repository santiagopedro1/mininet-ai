# Inspecting run logs

Every live CLI run writes progress to `logs/run.log` in its run artifact
directory (`/var/lib/mininet-ai/<run-id>/` for root; the effective user's local
state directory otherwise). `--log-file` overrides that
path. Logging does not require `--verbose`. Default stderr progress shows run
milestones, every completed agent's summary, successful network changes, and
warnings/errors. `--verbose` adds invocation/model starts and completions,
capability starts and all results (including no-ops), and shared-state updates.
Neither mode changes command results or `--format json` stdout. Saved diagnostic
detail does not depend on terminal verbosity; repeated summaries and failures
are not suppressed.

Terminal progress uses local time, a four-character severity column, and a
source label:

```text
14:32:02 INFO [Run] Network ready
14:32:06 INFO [Agent:monitor-1] Congestion detected; proposed a link update
14:32:08 ERR  [Mininet] Link update failed agent=monitor-1
14:32:09 WARN [Run] Agno dependency warning diagnostic dependency=agno agent=unknown
```

`[Run]` identifies lifecycle/runtime coordination; `[Agent:<id>]` uses the
compiled agent-instance ID. `[Mininet]` identifies native diagnostics and
Mininet-backed network action results, not every capability or an action
implemented by the fake substrate. Authorization rejections are runtime errors.
Agent summaries describe reasoning, not successful execution of their proposals.
In interactive terminals sources are cyan, magenta, and blue respectively;
warning/error severity is yellow/red. `NO_COLOR` or non-TTY output disables
styling. Message markup is literal, terminal controls are escaped, and each
multiline continuation receives its own prefix.

The artifact root is a shared container: an existing directory such as
`/run/mininet-ai` may have permissions like 0755. It is not chmodded or required
to be owner-only. Private permissions are enforced on the run directories,
`memory/` (when used), and `control/`, rather than their shared parent. For
example, `--artifact-root /run/mininet-ai` produces:

```text
/run/mininet-ai/                  # existing shared parent left unchanged
  mn-<run-id>/                    # 0700
    run.json                     # private versioned source/lifecycle bookkeeping
    logs/run.log                 # 0600
    dbs/                         # 0700, database files 0600
    artifacts/                   # 0700
  memory/agno.sqlite3             # optional private agent-scoped memory
  control/<run-id-hash>/control.sock  # private default root-user control endpoint
```

Existing output is not moved or deleted. Directory symlinks are still rejected,
and existing private children with unsafe permissions still fail validation.
A parent writable by other users allows them to interfere with child directory
names; use a parent you trust. `/run` is volatile: use a persistent local path
such as `/var/lib/mininet-ai/runs` when output must survive a reboot. VirtualBox
shared mounts such as `/vagrant` may still lack private permissions or SQLite
locking; accepting a shared parent does not make those filesystems suitable.

Saved lines use a full UTC ISO timestamp with timezone and full severity names
(`INFO`, `WARNING`, `ERROR`), followed by JSON-encoded `run_id` and the source
label. Managed export filters shared explicit logs by exact run identity and
supports both this format and the legacy timestamp/level format. Newlines and
terminal controls are escaped; saved logs contain no ANSI styling.
Audit lines also include the run, agent, and invocation IDs so concurrent work can
be correlated. Agent-start lines include the intent. Agent-completion lines
include the response message, proposal/delegation/shared-state update counts,
and available Agno model identity, session, duration, and token metrics.
Agent completion means reasoning finished, not that its proposed actions
succeeded: check the subsequent capability results.

Capability-start lines identify the proposal, capability, target, and reason.
Capability-completion lines show the request ID, status, changed flag, available
effect latency, and any issue code/message. Unsuccessful capability results
are logged at ERROR even when the executor returned a result instead of
raising an exception. This does not change scheduling or stop behavior.

Detail values use JSON encoding: strings are quoted, and newlines and control
characters are escaped to keep each audit entry on one physical line. Rich
markup in response messages is displayed literally. Missing optional values
are omitted. Full prompts, observations, action arguments, and provider output
are not dumped into the text log; the run ledger retains the full audit records.

Treat logs as sensitive: intents and agent responses can contain private data.
Log files retain owner-only permissions (0600).

## Dependency diagnostic boundaries

Built-in CLI runs own dependency logging through startup, rollback, draining,
and teardown, then flush/detach and restore foreign logger configuration before
closing the log and finalizing evidence. Library callers do not enable this
capture automatically. Concurrent embedded capture owners are rejected.

Native Mininet capture attaches to `mininet.log.lg` itself. Collection is fixed
at INFO, **not DEBUG**, regardless of `--verbose`. Reviewed lifecycle INFO call
sites in `net.py` (`build`, `buildFromTopo`, `configHosts`, `start`, `stop`,
`waitConnected`) and `node.py` (`batchShutdown`, `waitListening`,
`checkListening`, `defaultIntf`) are eligible; their fragments are assembled
without mixing threads or invocation identities. Routine accepted diagnostics
go to saved logs and verbose stderr. Native warnings/errors use safe summaries
with the originating function, not raw command-result excerpts, and appear by
default too. Command-echo INFO and native OUTPUT/test/interactive records are
not collected. Missing-newline fragments are bounded and flushed on close.

Agno's supported custom logging route emits normalized warning/error summaries,
not raw provider bodies, message arguments, or tracebacks. Known emission-time
invocations carry agent/invocation context; otherwise `[Run]` explicitly reports
`dependency=agno agent=unknown`. Async invocations receive their own context,
not the previous invocation's context. Agno INFO/DEBUG, unrelated SDK/Python
warning routes, arbitrary plugin writes, direct stdout/stderr writes, subprocess
streams, and daemon files are outside this capture guarantee. There is no global
stream/FD redirection; command return values and interactive consumers stay
untouched. Raw native DEBUG collection remains deferred.

On Python 3.14 Mininet's detached logger can retain stale level-enable caches.
Capture is configured before first native calls; a previously used logger that
cannot enable INFO is rejected with a fresh-process diagnostic rather than
silently losing lifecycle events. Concurrent/pre-used embedded Mininet use is
not a supported substitute for the owned CLI integration.

Nonprivileged logger and deterministic-model tests do not prove live OVS or
remote provider behavior. Native integration claims require the VM tests and
provider-warning acceptance against the actual deployment environment.

## Source coordination and finalization

Built-in CLI runs persist version-1 `run.json` before opening output files. It
records effective paths, owner UID/PID/boot/start identity, lifecycle, source
identities and evidence finalization. It is local bookkeeping, not a signed
manifest. Run directories are never reused; unknown metadata versions fail.
Finalization follows stopped workers, successful cleanup and closed output
handles. Failed invocations can produce finalized failed experiments; crashes
or failed cleanup do not certify stable evidence. Normal export refuses those
runs, even if the owner/socket disappeared or locks became available after reboot.
An unsuccessful deployment with no returned run requires an explicit adapter
cleanup receipt; missing run information alone is never proof of clean rollback.

Cooperating writers retain shared lifetime locks in the fixed private local
`/tmp/mininet-ai-locks-<effective-uid>` namespace; export takes nonblocking
exclusive locks through source reading. Physical aliases are deduplicated and
locks acquired in deterministic order. Standalone ledger/state/Agno stores also
participate. Lock files are not removed during normal cleanup; namespace or path
replacement is an error. The namespace is independent of artifact-root and XDG.
These guarantees do not cover hostile same-user/root edits or arbitrary external
writers. Runs with Python agents, plugins/custom providers, or arbitrary process
launch capabilities require operator-prepared offline snapshots instead.

## Bundle format (version 1)

`export` and `export-offline` require `--acknowledge-sensitive-data`, a run ID,
and an exact new `--destination` whose parent already exists. Offline mode also
requires `--acknowledge-offline-consistency` and at least one explicit source.
It records operator-supplied provenance rather than claiming managed verification.

- `manifest.json`: `schema_version`, `run_id`, `outcome`, `provenance`,
  `evidence_complete`, `missing_sources`, `excluded_sources`, effective `sources`
  and a `sha256` inventory (evidence payloads, not the manifest or marker itself).
- `ledger.json`: version, selected run ID, its manifest and ordered existing
  ledger-record representations. No unrelated-run records are included.
- `shared-state.json`: version, selected run ID and namespace/key/value entries
  with version, deletion marker, updater and timestamp.
- `run.log` and `artifacts/`: when available. Artifact symlinks and SQLite
  payloads are excluded and listed; unsafe special files fail. Agno stores and
  raw databases are not export inputs.
- `EXPORT_COMPLETE`: written last after publication and checksum verification.

Export stages privately on local storage, then uses exclusive creation to publish
host-readable output (requested directory/file modes 0755/0644) on the shared
destination. No source permissions are changed. Never use an existing destination
or a destination inside/aliasing a source tree. Failed exports have no completion
marker and return nonzero; diagnostics identify retained partial output.
Successful exports with genuinely missing evidence return zero and warn; supplied
missing, unreadable, unsafe or corrupt offline inputs fail instead of being skipped.
Completion is distinct from experiment success and evidence completeness. Shared
mounts need not honor modes or support directory fsync; exports are not promises
of atomic publication or power-loss durability. Consumers must check the marker.

## VM acceptance

Inside the disposable Vagrant VM, run from `/vagrant`:

```bash
sudo scripts/vm-run.sh python scripts/check-vm-storage.py
# Restart the VM, then verify the printed bundle path:
sudo scripts/vm-run.sh python scripts/check-vm-storage.py verify /vagrant/.storage-acceptance-...
```

The check uses actual Mininet without a model service or storage flags, verifies
private VM-local output, exports to `/vagrant`, and rejects explicitly unsafe
shared live storage. Verify host readability/checksums separately. It retains
diagnostic run data and uniquely named bundles; it does not overwrite existing
output. Automated tests do not prove arbitrary shared-filesystem SQLite locking.
