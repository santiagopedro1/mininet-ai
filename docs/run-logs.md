# Inspecting run logs

Every live CLI run writes progress to `logs/run.log` in its run artifact
directory (by default `.mininet-ai/<run-id>/`). `--log-file` overrides that
path. Logging does not require `--verbose`; that flag also mirrors progress
to stderr, leaving `--format json` stdout machine-readable.

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
