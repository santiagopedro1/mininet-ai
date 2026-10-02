# Inspecting run logs

Every live CLI run writes progress to `logs/run.log` in its run artifact
directory (by default `.mininet-ai/<run-id>/`). `--log-file` overrides that
path. Logging does not require `--verbose`; that flag also mirrors progress
to stderr, leaving `--format json` stdout machine-readable.

The artifact root is a shared container: an existing directory such as
`/run/mininet-ai` may have permissions like 0755. It is not chmodded or required
to be owner-only. Private permissions are enforced on the run directories,
`memory/` (when used), and `control/`, rather than their shared parent. For
example, `--artifact-root /run/mininet-ai` produces:

```text
/run/mininet-ai/                  # existing shared parent left unchanged
  mn-<run-id>/                    # 0700
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

Audit lines include the run, agent, and invocation IDs so concurrent work can
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
