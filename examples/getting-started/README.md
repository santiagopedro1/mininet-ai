# Getting Started

The simplest possible Mininet AI experiment. One agent observes the topology
and reports status. No external services, no root access, no VM.

*Primary goal: feature demonstration — showcases the basic `validate` →
`plan` → `run` workflow.*

## What it demonstrates

- Basic `validate` → `plan` → `run --dry-run` workflow
- `fake` substrate (compile-time, in-memory)
- `mock` model provider (deterministic, offline)
- One agent, one capability, one manual trigger

## Validate and plan (works anywhere)

```bash
uv run mininet-ai validate examples/getting-started/experiment.yaml
uv run mininet-ai plan examples/getting-started/experiment.yaml
uv run mininet-ai run examples/getting-started/experiment.yaml --dry-run
```

Expect 1 instance: `global-observer@network`.

## Run

```bash
uv run mininet-ai run examples/getting-started/experiment.yaml \
  --intent 'global-observer=Observe the topology and report status.' \
  --verbose
```

The mock provider returns a deterministic response, so the agent will always
report the same summary. This example is about the workflow, not the reasoning.

## Topology

Two hosts (`h1`, `h2`) connected through one switch (`s1`) and a builtin
controller (`c0`). 100 Mbps links with 1 ms delay.

## Next steps

- [Autonomous Network Operation](../autonomous-operation/README.md) — event
  triggers, detectors, rollback, and shared state
- [Hierarchical Routing Coordination](../hierarchical-routing/README.md) —
  multi-layer placement and hierarchical coordination
- [iperf Throughput](../iperf-throughput/README.md) — Ollama-driven TCP
  throughput test with real traffic
