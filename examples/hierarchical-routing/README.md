# Hierarchical Routing Coordination

A three-tier routing system: a global orchestrator sets policy, switch-level
routers install flows, and host-level agents generate traffic. Run locally on
Linux with root privileges, Mininet, Open vSwitch, iproute2, iperf v2, and an
Ollama server. No VM is required.

This single-subnet, loop-free topology uses Layer-2 MAC-learning forwarding
(`NORMAL`), not IP routing or path optimization. Switches have no external
controller and use secure fail mode: agents must install the forwarding rules.

*Primary goal: both — a plausible three-tier routing scenario that showcases
multi-layer placement and hierarchical coordination.*

## What it demonstrates

- Multi-layer placement (global + data + host)
- Hierarchical coordination (orchestrator → switch routers)
- YAML blueprints with Ollama provider
- Python factory agents with prompt-parsed, runtime-validated JSON responses
- `openflow.flow.install` capability with postconditions
- Shared state (orchestrator → router policy)

## Validate (works anywhere, no root)

```bash
uv run mininet-ai validate examples/hierarchical-routing/experiment.yaml
uv run mininet-ai plan examples/hierarchical-routing/experiment.yaml
```

Expect 6 instances: `global-orchestrator@network`, `switch-router@s1`,
`switch-router@s2`, `host-traffic@h1`, `host-traffic@h2`, `host-server@h3`.

## Run locally

All four blueprints use this Ollama configuration:

```yaml
model:
  provider: ollama
  name: qwen3.5:latest
  parameters:
    host: http://10.10.10.201:11434
    think: false
```

Thinking is disabled for this fixed policy/action workflow to keep responses
within the 120-second reasoning timeout, including on CPU-only Ollama servers.
All agents use the bundled deterministic prompt-parsed Ollama factory
(`temperature: 0`, `seed: 0`): the native structured output path failed to
return delegations during testing with this model. Pinned sampling reduces
variation; it does not replace runtime validation or installation checks.

From the repository root, prepare the local environment and check that the
endpoint is reachable and lists `qwen3.5:latest`:

```bash
uv sync --frozen
curl --fail http://10.10.10.201:11434/api/tags
sudo ovs-vsctl show
iperf --version
```

Open vSwitch must already be running. Use the project's environment directly
under sudo so the root process has the same Python dependencies.

Terminal 1 — start the owner with only the orchestrator intent:

```bash
sudo "$PWD/.venv/bin/mininet-ai" run \
  examples/hierarchical-routing/experiment.yaml \
  --intent 'global-orchestrator@network=Publish the routing policy and delegate its installation to both switch routers.' \
  --verbose
```

Keep terminal 1 running. The orchestrator commits `routing-policy` in run-scoped
shared state, then delegates to both routers. Do not submit simultaneous router
intents: that races policy publication. Periodic router invocations are disabled
to avoid redundant model calls and flow installations.
On the tested CPU-only Ollama endpoint, policy publication and both router
responses took about three minutes; host responses took roughly 40–70 seconds.
The 120-second timeout applies to each model invocation, not the whole hierarchy.

Terminal 2 — first verify both forwarding rules:

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
sudo ovs-ofctl -O OpenFlow13 dump-flows s2
```

Both switches must show a catch-all `actions=NORMAL` rule, and terminal 1 must
report successful capability execution/postcondition checks for both routers.
Then start the server (replace `<run-id>` with the ID from terminal 1):

```bash
sudo "$PWD/.venv/bin/mininet-ai" agents <run-id>
sudo "$PWD/.venv/bin/mininet-ai" invoke \
  <run-id> host-server@h3 --intent 'Start the foreground iperf TCP server.'
```

Wait for successful server process launch. To verify listener readiness, find
the PID of the `mininet:h3` shell and inspect its namespace:

```bash
sudo pgrep -af 'mininet:h[123]'
sudo mnexec -a <h3-pid> ss -ltn 'sport = :5001'
```

Only after a listener appears, invoke the clients:

```bash
sudo "$PWD/.venv/bin/mininet-ai" invoke \
  <run-id> host-traffic@h1 \
  --intent 'Run a TCP throughput test against 10.0.0.13.'
sudo "$PWD/.venv/bin/mininet-ai" invoke \
  <run-id> host-traffic@h2 \
  --intent 'Run a TCP throughput test against 10.0.0.13.'
```

Use the run ID from terminal 1. `agents` lists available IDs and manual-intent
eligibility. Neither command needs the original experiment file; both require
the live owner on the same machine and the same user; default discovery is
working-directory independent (or use an identical absolute `--control-dir`).
`invoke` acknowledges acceptance, not completion.

`host.process.start` reports process launch, not a completed throughput result;
managed process stdout/stderr are currently discarded. For a visible end-to-end
check, after the agent clients finish their 20-second tests, use the `h1` and
`h2` namespace shell PIDs found above:

```bash
sudo mnexec -a <h1-pid> iperf -c 10.0.0.13 -t 5 -f m
sudo mnexec -a <h2-pid> iperf -c 10.0.0.13 -t 5 -f m
```

Both must report positive transferred bytes and bandwidth. A response message
or successful process launch alone is not proof of connectivity. Stop the owner
with Ctrl-C when finished; it tears down the topology and managed server.

Root's live logs/databases default to `/var/lib/mininet-ai/<run-id>/`, not
the repository. Existing learned memory in other roots is not imported
automatically.
For portable results, use [offline snapshot export](../../README.md#export-results-to-the-host)
after stopping writers; this example's arbitrary host-process capabilities are
not automatically managed-export safe.

## How it works

1. **Global orchestrator** (global layer): Observes the topology, determines
   a run-scoped `routing-policy` with one catch-all `NORMAL` rule per switch,
   and delegates installation to its graph-authorized switch routers.
2. **Switch routers** (data layer): Read the routing policy from shared state
   and propose `openflow.flow.install` capabilities to implement it.
3. **Host agents** (host layer): A managed foreground iperf v2 server listens on
   `h3`; client agents on `h1` and `h2` launch 20-second TCP tests against it.
   Client process IDs use the invocation ID because managed IDs are run-wide,
   not host-scoped.
4. **Hierarchical coordination**: The orchestrator is the parent of the switch
   routers. The compiler generates coordination edges from the orchestrator
   to each switch router.

### Flow proposal arguments

`openflow.flow.install` requires an `arguments.match` object and a non-empty
`arguments.actions` string (comma-separated OpenFlow actions, not an array).
For example, a policy-backed proposal might contain:

```json
{"id":"policy-flow","capability":"openflow.flow.install","target":"s1","arguments":{"match":{},"actions":"NORMAL"}}
```

This is the policy for `s1`; the `s2` router uses target `s2`. The empty match
covers ARP and return traffic as well as client TCP packets. Routers should
return no proposals when the policy is missing. When authorized they submit
the rule even if it already exists: same-match `add-flow` replaces it, avoiding
model guesses about whether a sibling switch's installation applies locally.
The postcondition checks `switches.0.flows.0.actions == NORMAL` in native OVS
telemetry; it is specific to this fresh-switch, single-rule example, not a
general check for arbitrary flow proposals. The blueprints spell out the
proposal argument shape; runtime validation rejects malformed proposals.
An agent's response message is not proof of installation:
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
