# Bounded recovery campaign paths

Primary goal: **both** (feature demonstration and realistic scenario).

This maintained campaign runner implements the offline paths for tickets 01–03 of the four-treatment
study, not a replacement for the older compiler-backed recovery examples.
Its declared JSON campaign configuration is validated separately from an
`Experiment` specification. The approved test boundary is configuration →
observable events, packet evidence and outcomes.

See [implementation, CLI and limitations](../../../docs/recovery-01-implementation.md).
The [development configuration](development.json) has **unapproved** numerical
budgets and an intentionally unconfigured model. Live feasibility and scientific
approval are still required; scripted outputs are never live campaign evidence.

```bash
python -m examples.noms27.recovery_campaign \
  --config examples/noms27/recovery_campaign/development.json --check
```

Run from the repository root. `--check` never contacts a model or network.

Direct S2 uses [development-s2.json](development-s2.json). Validate it with the
same command, substituting that filename. See
[direct S2 permissions, runbook and offline limitations](../../../docs/recovery-02-implementation.md).
Combined ungated uses [development-ungated.json](development-ungated.json), with
the same network-free `--check` command. See
[handoff semantics and offline limitations](../../../docs/recovery-03-implementation.md).
Ticket 01 remains blocked on valid live acceptance; none of these paths is approved for
campaign evaluation. No VM or model-machine access is authorized by this runbook.
