# Hierarchical Routing Coordination

A three-tier routing system: a global orchestrator sets policy, switch-level
routers install flows, and host-level agents generate traffic. Requires a
Vagrant VM and Ollama server.

*Primary goal: both — a plausible three-tier routing scenario that showcases
multi-layer placement and hierarchical coordination.*

## What it demonstrates

- Multi-layer placement (global + data + host)
- Hierarchical coordination (orchestrator → switch routers)
- Declarative blueprints with Ollama provider
- Python factory blueprints (host agents)
- `openflow.flow.install` capability with postconditions
- Shared state (orchestrator → router policy)

## Validate (works anywhere, no root)

```bash
uv run mininet-ai validate examples/hierarchical-routing/experiment.yaml
uv run mininet-ai plan examples/hierarchical-routing/experiment.yaml
```

Expect 5 instances: `global-orchestrator@network`, `switch-router@s1`,
`switch-router@s2`, `host-traffic@h1`, `host-traffic@h2`.

## Run (Vagrant VM only)

Set `model.parameters.host` in all three blueprints to your Ollama endpoint:

```yaml
model:
  provider: ollama
  name: qwen3.5:latest
  parameters:
    host: http://YOUR_OLLAMA_HOST:11434
```

Terminal 1 — start the orchestrator and switch routers:

```bash
vagrant up
vagrant ssh
cd /vagrant
sudo scripts/vm-run.sh mininet-ai run \
  examples/hierarchical-routing/experiment.yaml \
  --intent 'global-orchestrator@network=Set the routing policy for the network.' \
  --intent 'switch-router@s1=Install flows based on the global policy.' \
  --intent 'switch-router@s2=Install flows based on the global policy.' \
  --verbose
```

Terminal 2 — generate traffic from the hosts:

```bash
vagrant ssh
cd /vagrant
sudo scripts/vm-run.sh mininet-ai agents <run-id>
sudo scripts/vm-run.sh mininet-ai invoke \
  <run-id> host-traffic@h1 \
  --intent 'Run a TCP throughput test against 10.0.0.13.'
sudo scripts/vm-run.sh mininet-ai invoke \
  <run-id> host-traffic@h2 \
  --intent 'Run a TCP throughput test against 10.0.0.13.'
```

Use the run ID from terminal 1. `agents` lists available IDs and manual-intent
eligibility. Neither command needs the original experiment file; both require
the live owner, the same user, and the same working directory (or an identical
absolute `--control-dir`). `invoke` acknowledges acceptance, not completion.

## How it works

1. **Global orchestrator** (global layer): Observes the topology, determines
   the optimal routing policy, and writes it to shared state.
2. **Switch routers** (data layer): Read the routing policy from shared state
   and propose `openflow.flow.install` capabilities to implement it.
3. **Host agents** (host layer): Generate iperf traffic to verify the routing
   works correctly.
4. **Hierarchical coordination**: The orchestrator is the parent of the switch
   routers. The compiler generates coordination edges from the orchestrator
   to each switch router.

### Flow proposal arguments

`openflow.flow.install` requires an `arguments.match` object and a non-empty
`arguments.actions` string (comma-separated OpenFlow actions, not an array).
For example, a policy-backed proposal might contain:

```json
{"id":"policy-flow","capability":"openflow.flow.install","target":"s1","arguments":{"match":{"in_port":1},"actions":"output:2"}}
```

This illustrates the shape only: choose the target and ports from the actual
policy and observations. Routers should return no proposals when that
information is missing. Declarative agents receive their scoped capability
input schemas in the model instructions; runtime validation still rejects
malformed proposals. An agent's response message is not proof of installation:
check `capability.execution.completed` for the action's actual status.

## Topology

Three hosts (`h1`, `h2`, `h3`) connected through two switches (`s1`, `s2`).
The switches are linked by a 1 Gbps backbone. Hosts `h1` and `h2` are
clients; `h3` is the server.

## Next steps

- [Getting Started](../getting-started/README.md) — the simplest possible
  experiment
- [iperf Throughput](../iperf-throughput/README.md) — Ollama-driven TCP
  throughput test with real traffic
- [Autonomous Network Operation](../autonomous-operation/README.md) — event
  triggers, detectors, rollback, and shared state
