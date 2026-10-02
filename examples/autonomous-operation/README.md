# Autonomous Network Operation

A self-healing network that monitors queue occupancy, detects congestion, and
reroutes traffic with rollback. No external services, no root access, no VM.

## What it demonstrates

- Event triggers (queue threshold exceeded)
- Interval triggers (periodic health check)
- Observation detectors (threshold)
- Rollback and postconditions
- Shared state (preferred path)
- Multi-layer placement (data + host)
- Declarative blueprints with mock provider

## Validate and plan (works anywhere)

```bash
uv run mininet-ai validate examples/autonomous-operation/experiment.yaml
uv run mininet-ai plan examples/autonomous-operation/experiment.yaml
uv run mininet-ai run examples/autonomous-operation/experiment.yaml --dry-run
```

Expect 4 instances: `switch-monitor@s1`, `switch-monitor@s2`,
`host-traffic@h1`, `host-traffic@h2`.

## Run

```bash
uv run mininet-ai run examples/autonomous-operation/experiment.yaml \
  --intent 'switch-monitor@s1=Monitor queue occupancy and reroute on congestion.' \
  --intent 'host-traffic@h1=Generate test traffic to verify reachability.' \
  --stop-after-intents --verbose
```

The mock provider returns a deterministic response. The switch-monitor agent
proposes a flow installation when congestion is detected. The capability has
rollback and postconditions, so the flow is verified after installation.

**Note**: The event trigger fires on the fake substrate's mock observation data,
which may cause additional invocations after the manual intents complete. This
is expected for a mock example — the threshold detector fires because the fake
substrate returns deterministic (non-zero) observation values. Use
`--stop-after-intents` to stop after the initial intents finish.

## How it works

1. **Monitoring**: The `switch-monitor` agent observes `tc.queue-occupancy`
   on both switches (`s1`, `s2`) with a 1-second sampling interval.
2. **Detection**: A threshold detector fires when queue depth >= 80%. The
   detector emits a `queue.threshold-exceeded` event.
3. **Response**: The event trigger invokes the agent, which proposes an
   `openflow.flow.install` to reroute traffic through the alternate path.
4. **Verification**: The capability has a postcondition that checks
   `openflow.flows.0.state == installed`. If the postcondition fails, the
   capability rolls back the flow installation.
5. **Shared state**: The agent updates the `preferred-path` key in shared
   state so other agents can read the current routing decision.

## Topology

Three hosts (`h1`, `h2`, `h3`) connected through two switches (`s1`, `s2`).
The switches are linked by a 1 Gbps backbone. Hosts `h1` and `h2` are
clients; `h3` is the server.

## Next steps

- [Hierarchical Routing Coordination](../hierarchical-routing/README.md) —
  multi-layer placement and hierarchical coordination
- [Getting Started](../getting-started/README.md) — the simplest possible
  experiment
