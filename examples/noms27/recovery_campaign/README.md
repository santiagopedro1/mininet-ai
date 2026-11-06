# Bounded recovery campaign paths

Primary goal: **both** (feature demonstration and realistic scenario).

This maintained campaign runner implements the offline paths for tickets 01–06 of the four-treatment
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
The fourth slot uses [development-gated.json](development-gated.json). Its gate is
explicitly enabled with an **uncalibrated development-only** threshold. Setting
`confidence_gate_enabled` to `false` retains the slot without a distinct policy.
See [gate semantics and evidence](../../../docs/recovery-04-implementation.md).
Ticket 01 remains blocked on valid live acceptance; none of these paths is approved for
campaign evaluation. No VM or model-machine access is authorized by this runbook.

## Six-class offline matched blocks

Ticket 05 adds a manifest and controlled rehearsal via
`python -m examples.noms27.recovery_campaign.blocks --check`.
It reserves all four primary/three higher-load slots. By default the rehearsal
executes only the three ungated treatments on calibration placements, using
explicitly scripted, in-process graph/packet evidence. Ticket 06 integrates the
gated slot as an explicit offline opt-in:

```bash
python -m examples.noms27.recovery_campaign.blocks --controlled \
  --repetitions 1 --development-confidence-threshold 0.5 \
  --evidence run-evidence/recovery-06-development
```

The threshold is development-only, not calibrated or approved. Omit the threshold
to preserve the previous rehearsal; add `--load 0.7` to rehearse all three
higher-load slots. Neither mode authorizes live or held-out runs.
See [implementation](../../../docs/recovery-05-implementation.md) and
[validation](../../../docs/recovery-05-validation.md) for commands and limits.
See [integration notes](../../../docs/recovery-06-integration.md) for merge scope.
