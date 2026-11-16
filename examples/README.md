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
   state, plus shared model configuration and shallow response overrides.
3. **[Hierarchical Routing Coordination](hierarchical-routing/README.md)** —
   a three-tier routing system with multi-layer placement and hierarchical
   coordination.
4. **[iperf Throughput](iperf-throughput/README.md)** — an Ollama-driven TCP
   throughput test with real traffic on `mininet-ovs`.
5. **[Diamond Recovery](diamond-recovery/README.md)** — an exploratory,
   offline bounded-decision topology and planning contract; it does not claim
   live model or network support.

## Index

**[NOMS 2027 System One recovery](noms27/single-switch-recovery/README.md)** adds
one-agent monitoring with seven choices, automatic repeated port-down trials,
configurable warm-up requests, and an
explicit escalation choice that records a request without calling another model.
The specification supports offline planning; its privileged runner requires
separate live validation.

**[Unified System One/System Two recovery](noms27/unified-system-1-2-recovery/README.md)**
combines both paths: a randomly selected link-failure or missing-flow fault, a
local System One repair, and escalation to a System Two agent on an explicit
escalation choice or when confidence falls below
the configured threshold. It is the combined form of the two focused NOMS
examples.

System One and System Two describe decision-making approaches, not placement or
authority. See [decision-making approaches](../docs/decision-making.md) for the
model assumptions and their relationship to agents and execution mechanisms.

| Example | Description | Substrate | Provider | Primary Goal | Key Features |
| --- | --- | --- | --- | --- | --- |
| [Getting Started](getting-started/README.md) | Minimal offline experiment | `fake` | `mock` | Feature demonstration | Basic workflow, one agent, one capability |
| [Autonomous Network Operation](autonomous-operation/README.md) | Self-healing network | `fake` | `mock` | Feature demonstration | Shared model + shallow override, event/interval triggers, threshold detector, rollback, postconditions, shared state, multi-layer placement |
| [Hierarchical Routing Coordination](hierarchical-routing/README.md) | Three-tier routing system | `mininet-ovs` | `ollama` | Both | Multi-layer placement, hierarchical coordination, declarative + Python factory blueprints, postconditions, shared state |
| [iperf Throughput](iperf-throughput/README.md) | Ollama-driven TCP throughput test | `mininet-ovs` | `ollama` | Realistic scenario | Host placement, Python factory blueprints, cross-terminal `invoke`, serialized startup intents |
| [Diamond Recovery](diamond-recovery/README.md) | Four-switch failover decision contract (offline only) | `fake` | `llamacpp-systemone` (placeholder) | Both | Explicit choices, fixed recovery bundle, three-round inputs |
| [Unified System One/Two Recovery](noms27/unified-system-1-2-recovery/README.md) | Combined local recovery and confidence-gated escalation | `mininet-ovs` | `llamacpp-systemone` + `ollama` | Both | Random link/flow faults, local repair, confidence gate, escalation to System Two |

## Feature Matrix

| Feature | Getting Started | Autonomous Operation | Hierarchical Routing | iperf Throughput | Diamond Recovery |
| --- | --- | --- | --- | --- |
| `fake` substrate | ✓ | ✓ | | |
| `mininet-ovs` substrate | | | ✓ | ✓ |
| `mock` provider | ✓ | ✓ | | |
| `ollama` provider | | | ✓ | ✓ |
| Manual trigger | ✓ | ✓ | ✓ | ✓ |
| Interval trigger | | ✓ | ✓ | |
| Event trigger | | ✓ | | |
| Observation detector | | ✓ | | |
| Rollback | | ✓ | ✓ | |
| Postconditions | | ✓ | ✓ | |
| Shared state | | ✓ | ✓ | |
| Multi-layer placement | | ✓ | ✓ | |
| Hierarchical coordination | | | ✓ | |
| Declarative blueprint | ✓ | ✓ | ✓ | |
| Python factory blueprint | | | ✓ | ✓ |
| Shared model + shallow override | | ✓ | | |
| Bounded decision contract (offline plan only) | | | | | ✓ |

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
