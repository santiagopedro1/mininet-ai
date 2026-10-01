# iperf throughput test (Ollama-driven traffic generation)

Two host agents generate real traffic: `iperf-server@server` starts
`iperf -s -D`, then `iperf-client@client` runs a 20s TCP test against
`10.0.0.12`. Both agents reason with `qwen3.5` on your Ollama server; Mininet
AI authorizes and executes their `host.process.start` proposals.

Topology: `client` (10.0.0.11) and `server` (10.0.0.12) through `s1`,
`main-controller`, 100 Mbps links. `max-concurrent-invocations: 1`
serializes the two intents so the server is up before the client connects.

## iperf vs iperf3

Not a problem. `host.process.start` execs argv directly with no shell, so
any binary works as long as the flags match it. These blueprints use iperf v2
syntax (`-s -D`, `-c ADDR -t SECS -f m`). For iperf3, change the arguments
arrays to `["-s", "-D"]` / `["-c", "10.0.0.12", "-t", "20", "-f", "m"]`
(the basic flags are the same) and point `command` at `iperf3`.

## Validate (works anywhere, no root)

```bash
uv run mininet-ai validate examples/iperf-throughput/experiment.yaml
uv run mininet-ai run examples/iperf-throughput/experiment.yaml --dry-run
```

Expect 2 instances: `iperf-server@server`, `iperf-client@client`.

## Run (Vagrant VM only)

Set `model.parameters.host` in both
`agent-blueprints/ollama-iperf-server.yaml` and
`agent-blueprints/ollama-iperf-client.yaml` to your Ollama endpoint:

```yaml
model:
  provider: ollama
  name: qwen3.5:latest
  parameters:
    host: http://10.10.10.152:11434
```

Replace that example address with one reachable from the VM. The checked-in
blueprints use `http://localhost:11434`, which only works if Ollama is running
inside the VM. An explicit host overrides `OLLAMA_HOST`, so no endpoint
environment variable needs to be passed through `sudo`. To use the environment
instead, remove `parameters.host` from both blueprints and pass
`OLLAMA_HOST` through `sudo env`. Endpoint URLs must use HTTP/HTTPS and contain
no credentials; credentials remain in environment variables.

Terminal 1 — intents run at startup, then it idles for inspection (no
`--stop-after-intents`):

```bash
vagrant up
vagrant ssh
cd /vagrant
sudo scripts/vm-run.sh mininet-ai run \
  examples/iperf-throughput/experiment.yaml \
  --intent 'iperf-server@server=Start the iperf server on server.' \
  --intent 'iperf-client@client=Run a TCP throughput test against 10.0.0.12.' \
  --verbose --format json
```

Each `--intent` is `AGENT=TEXT` and order matters: server first. Give each
LLM call up to 120s (`reasoning.timeout`).

Terminal 2 — while terminal 1 is up, watch the traffic cross `s1`:

```bash
vagrant ssh
cd /vagrant
sudo ovs-ofctl -O OpenFlow13 dump-flows s1
# repeat: n_packets / n_bytes grow during the 20s test
sudo ovs-vsctl --timeout=5 get Interface s1-eth2 statistics
```

You can also submit another client intent while the owner remains active:

```bash
sudo scripts/vm-run.sh mininet-ai invoke \
  examples/iperf-throughput/experiment.yaml <run-id> iperf-client@client \
  --intent 'Run a TCP throughput test against 10.0.0.12.'
```

Use the run ID printed by terminal 1 or written to `.mininet-ai/run.log`.
The command returns when the intent is queued, not when iperf finishes. Results
are logged and persisted by terminal 1; it already has the Ollama configuration.
Both terminals must use the same user and working directory (or the same
absolute `--control-dir`). Keep terminal 1 running without
`--stop-after-intents` to accept later requests.

Back in terminal 1: `Ctrl+C`, or `sudo scripts/vm-run.sh mininet-ai stop <run-id>`.

## UDP instead of TCP

Edit the client blueprint arguments to
`["-c", "10.0.0.12", "-u", "-b", "20M", "-t", "20", "-f", "m"]`
(and the instructions to match), re-validate, rerun.

## Caveats

- Switch names need not be canonical. To use `edge-sw`, replace `s1` and
  `s1-ethN` in the experiment and inspection commands with `edge-sw` and
  `edge-sw-ethN`. The compiler generates a deterministic non-zero 64-bit DPID;
  canonical `sN` names retain their numeric DPID. An optional switch `dpid`
  field (for example, `dpid: "abc"`) overrides it and is normalized to 16
  lowercase hexadecimal digits. Fixed IDs must be unique; generated IDs skip
  collisions. Pin an explicit DPID if identity must survive renaming or topology
  changes involving collisions. Switch and interface names remain limited to
  15 bytes, including generated interface suffixes.
- The public contract is `mininet-ai/v1alpha3`. Update `apiVersion` on existing
  experiments, agent blueprints, and capabilities from `v1alpha2` to `v1alpha3`,
  then recompile saved deployment plans. Switch entries now require a resolved
  `dpid`; earlier versioned inputs and plans are rejected. See the
  [migration guide](../../docs/compatibility.md#public-specification-v1alpha2-to-v1alpha3).

- Managed-process output goes to `DEVNULL`, so the Mbit/s number is not in
  the report — the report proves `SUCCEEDED` starts; counters prove traffic.
- Cross-terminal `invoke` requires a live foreground owner. Startup and later
  intents share its scheduler, authorization, model sessions, and ledger.
