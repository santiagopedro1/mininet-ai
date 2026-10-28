# Diamond recovery (exploratory Maintained Example)

**Primary Goal:** both feature demonstration and realistic scenario. Planning
uses the `fake` Substrate; a separate opt-in Mininet/OVS harness provides actual
network setup and probes. Neither a live model run nor a live network acceptance
run is claimed. The harness retains round snapshots as JSONL and does not feed
them into the current bounded-agent runtime; model execution remains exploratory
and gated on the separate integration/evaluation work.

## Prerequisites and plan

Python 3.14 and the repository `uv` environment. No controller or model server
is contacted by validation/planning. The model identity and endpoint are
intentionally marked exploratory placeholders; replace them in a new experiment
before any inference trial.

```bash
uv run mininet-ai validate examples/diamond-recovery/experiment.yaml
uv run mininet-ai plan examples/diamond-recovery/experiment.yaml
```

The source and resulting plan retain the paths, observation bindings, choice
criteria, materialized initial policies, and fixed ordered recovery proposal bundle. One grouped singleton
decision-maker is scoped to the four labeled switches, so normal capability
authorization still checks the target against the compiled scope.

## Topology contract

Pinned switch ports: s1 host=1, s2=2, s3=3; s2 s1=1/s4=2; s3 s1=1/s4=2;
s4 host=1/s2=2/s3=3. Expected link mapping is h1-s1(1), s1-s2(2)/s1-s3(3),
s2-s4(2), s3-s4(2), s4-h2(1). Hosts use MACs
`02:00:00:00:00:01`/`02:00:00:00:00:02` and active addresses
`10.0.0.1`/`10.0.0.2`. Diagnostic aliases are primary `.11`/`.12` and recovery
`.21`/`.22`; install explicit peer static neighbors for all six host addresses
before probing. The scenario embeds the exact aliases, static-neighbor map and
initial/probe policy. The opt-in harness applies these to a fresh
controller-free, fail-secure OVS network and verifies actual interface/port
mapping, table-miss drop, and absence of `NORMAL` forwarding. The `fake`
Substrate plan itself does not deploy these settings.

Initial active and diagnostic flow rules are priority 100 with permanent
timeouts: active and primary probe pairs use the primary path, recovery probes
use the recovery path, all with exact IPv4 src/dst and ingress-port matches in
both directions. Each one-second observation round contains independent primary
and recovery probes in each direction (one-second timeout, sent/received counts
and timestamp), all four inter-switch link states, current policy and previous
action outcomes. Keep three completed rounds, explicit gaps/missing values and
timestamps; freeze each invocation snapshot. Only distinct active-address
end-to-end probes confirm recovery. The scenario has no hidden chat memory or
oracle-inserted diagnosis.

## Trial lifecycle ownership

The opt-in single-process harness provides recorded setup/readiness, deployed
port verification, policy inspection, one-link fault injection (s1-s2 or
s2-s4), baseline reset, probe rounds, active-address confirmation, and cleanup
reporting. These trusted operations are journaled separately and never execute
the model-proposed recovery bundle or bypass normal capability authorization.
Reset and cleanup outcomes are independent; do not repeat a trial when either
remains unknown.

On a disposable Linux host with root, Mininet and OVS installed, from the repo
root:

```bash
sudo env PYTHONPATH=. .venv/bin/python \
  examples/diamond-recovery/live_harness.py \
  --evidence /tmp/diamond-recovery-evidence
```

The harness starts the topology and keeps it alive in the foreground. Commands:
`readiness`, `inspect`, `fault s1-s2` or `fault s2-s4`, `probe 3`, `confirm`,
`reset`, and `cleanup`. `confirm` only measures three consecutive bidirectional
active-address successes within five seconds while the selected primary link is
still down; it does not install recovery. Only confirm after a separately
authorized recovery action has changed the live network. The harness refuses to
replace existing bridges/interfaces and reports cleanup as unverified if owned
interfaces or bridges remain.

The offline fixtures in `tests/fixtures/specifications/diamond-recovery/` label
expected decisions for healthy/startup, transient, sustained, stale/missing/
conflicting, failed recovery-probe, partial/unknown and post-recovery cases.
They are oracle inputs, not an alternate deterministic model gate. Fake
Substrate tests assert probe payloads and the embedded rule contract. Offline
mechanics and planning are separate from pinned-model/server evaluation and
live-network acceptance. The opt-in harness has not been run in this
environment; neither live gate is passed or claimed.
