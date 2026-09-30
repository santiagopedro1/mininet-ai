# iperf throughput test (Ollama-driven traffic generation)

Two host agents generate real traffic: `iperf-server@server` starts
`iperf -s -D`, then `iperf-client@client` runs a 20s TCP test against
`10.0.0.12`. Both agents reason with `qwen3.5` on your Ollama server; Mininet
AI authorizes and executes their `host.process.start` proposals.

Topology: `client` (10.0.0.11) and `server` (10.0.0.12) through `s1`
(the switch name must stay canonical — Mininet derives its datapath ID from
it), `main-controller`, 100 Mbps links. `max-concurrent-invocations: 1`
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

Pass `OLLAMA_HOST` through `sudo` so the privileged runtime inherits the model
endpoint.
Terminal 1 — intents run at startup, then it idles for inspection (no
`--stop-after-intents`):

```bash
vagrant up
vagrant ssh
cd /vagrant
sudo env OLLAMA_HOST=http://10.10.10.152:11434 \
  scripts/vm-run.sh mininet-ai run \
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

Back in terminal 1: `Ctrl+C`, or `sudo scripts/vm-run.sh mininet-ai stop <run-id>`.

## UDP instead of TCP

Edit the client blueprint arguments to
`["-c", "10.0.0.12", "-u", "-b", "20M", "-t", "20", "-f", "m"]`
(and the instructions to match), re-validate, rerun.

## Caveats

- Managed-process output goes to `DEVNULL`, so the Mbit/s number is not in
  the report — the report proves `SUCCEEDED` starts; counters prove traffic.
- Cross-process `invoke` is unsupported; all intents must be passed at startup.
