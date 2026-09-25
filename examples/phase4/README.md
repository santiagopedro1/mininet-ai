# Phase 4 autonomous runtime example

This rootless example proves the complete continuous experiment lifecycle. A
telemetry policy observes a congested queue, emits `queue.congested`, invokes a
deterministic Agno agent, installs an authorized OpenFlow rule, verifies that
the effect is observable, and persists the run history before teardown.

Run it from the repository root:

```bash
uv run python -m examples.phase4
```

Use `--ledger-db`, `--agno-db`, and `--shared-state-db` to choose other database
locations. The command prints a JSON report and exits nonzero if no autonomous
action completes before `--timeout`.
