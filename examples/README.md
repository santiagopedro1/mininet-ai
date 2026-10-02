# Examples

Mininet AI examples demonstrate how to define, validate, and run agentic
networking experiments. Each example is a self-contained directory with an
`experiment.yaml`, a `README.md`, and any needed blueprint and capability
files.

## Progression

Start with the simplest example and work your way up:

1. **[Getting Started](getting-started/README.md)** — the simplest possible
   experiment. One agent, one capability, no external services.
2. **[Autonomous Network Operation](autonomous-operation/README.md)** — a
   self-healing network with event triggers, detectors, rollback, and shared
   state.
3. **[Hierarchical Routing Coordination](hierarchical-routing/README.md)** —
   a three-tier routing system with multi-layer placement and hierarchical
   coordination.

## Index

| Example | Description | Substrate | Provider | Key Features |
| --- | --- | --- | --- | --- |
| [Getting Started](getting-started/README.md) | Minimal offline experiment | `fake` | `mock` | Basic workflow, one agent, one capability |
| [Autonomous Network Operation](autonomous-operation/README.md) | Self-healing network | `fake` | `mock` | Event/interval triggers, threshold detector, rollback, postconditions, shared state, multi-layer placement |
| [Hierarchical Routing Coordination](hierarchical-routing/README.md) | Three-tier routing system | `mininet-ovs` | `ollama` | Multi-layer placement, hierarchical coordination, declarative + Python factory blueprints, postconditions, shared state |

## Feature Matrix

| Feature | Getting Started | Autonomous Operation | Hierarchical Routing |
| --- | --- | --- | --- |
| `fake` substrate | ✓ | ✓ | |
| `mininet-ovs` substrate | | | ✓ |
| `mock` provider | ✓ | ✓ | |
| `ollama` provider | | | ✓ |
| Manual trigger | ✓ | ✓ | ✓ |
| Interval trigger | | ✓ | ✓ |
| Event trigger | | ✓ | |
| Observation detector | | ✓ | |
| Rollback | | ✓ | ✓ |
| Postconditions | | ✓ | ✓ |
| Shared state | | ✓ | ✓ |
| Multi-layer placement | | ✓ | ✓ |
| Hierarchical coordination | | | ✓ |
| Declarative blueprint | ✓ | ✓ | ✓ |
| Python factory blueprint | | | ✓ |

## Running the examples

All examples can be validated and planned without root access or external
services:

```bash
uv run mininet-ai validate examples/<name>/experiment.yaml
uv run mininet-ai plan examples/<name>/experiment.yaml
```

The `fake` substrate examples can also be run with `--dry-run` without a VM:

```bash
uv run mininet-ai run examples/<name>/experiment.yaml --dry-run
```

The `mininet-ovs` examples require a Vagrant VM and an Ollama server. See
each example's README for detailed instructions.
