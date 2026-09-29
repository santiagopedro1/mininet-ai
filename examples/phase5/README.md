# Phase 5 centralized coordination example

This rootless example exercises the complete coordination path. A manual
intent addressed to `primary-remediator@s1` enters `global-coordinator`, which
explicitly delegates to two switch-scoped agents. Both agents propose the same
`dataplane.write` effect on `s1`. Under the declared `reject` policy, Mininet AI
commits the first admitted action and returns a typed
`coordination.conflict.rejected` result for the second.

Run it from the repository root:

```bash
uv run mininet-ai validate examples/phase5/experiment.yaml
uv run python -m examples.phase5
uv run pytest -q tests/acceptance/test_phase5.py
```

The command prints the triggering runtime event and final experiment report as
JSON. Inspect `report.continuous.invocations[0].coordination` for the versioned
outcome, including the root intent, both delegation messages, three invocation
records, and the two arbitration decisions.

The example uses Agno's deterministic offline model and the fake substrate. It
requires neither model credentials nor root privileges. Native Agno teams and
workflows are intentionally not used because the current Agno interfaces do
not preserve Mininet-owned scoped invocation, explicit message delivery, and
action arbitration semantics.

For the equivalent live topology, see the
[Mininet/OVS variant](mininet/README.md). It uses the same coordination graph
and agent responses but replaces the fake substrate capability with the live
`substrate.action` provider.
