# Diamond switch diagnosis (exploratory Maintained Example)

**Primary Goal:** demonstrate one native System 1 diagnosis agent per switch.
Planning uses the `fake` Substrate; the opt-in Mininet/OVS harness provides a
real four-switch topology. Each agent is scoped to one switch and receives only
its current local flow and port-counter observations. No observation history,
conversation memory, or network-wide state is part of the model request. Choices
are diagnostic (`healthy` / `degraded`) and carry no action proposals. Live model
evaluation remains exploratory; no network recovery is claimed. The live-network
support verdict is recorded in the [live-network evaluation report](../../docs/implementation/bounded-decision-live-network-evaluation.md).

## Prerequisites and plan

Python 3.14 and the repository `uv` environment. No controller or model server
is contacted by validation/planning. The model identity and endpoint are
intentionally marked exploratory placeholders; replace them in a new experiment
before any inference trial.

```bash
uv run mininet-ai validate examples/diamond-recovery/experiment.yaml
uv run mininet-ai plan examples/diamond-recovery/experiment.yaml
```

The source and resulting plan contain four per-target bounded agents, one for
each switch. Each is bound to current `openflow.flows` and `ovs.port-counters`
observations, with diagnosis-only choices and no capabilities.

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

The network harness still configures the diamond and records its probe rounds
for topology diagnostics, but those rounds are not sent to System 1. A switch
agent request is current-only: its target switch identity, latest local port
state/counters, and latest local flow state. Missing or unavailable fields stay
explicit; agents must not infer the condition of other switches.

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

The prior network-recovery oracle fixtures remain historical tests and are not
the current agent's decision contract. Current acceptance labels local switch
states (healthy, degraded, and unavailable/missing local evidence) independently
for each target switch; unavailable required evidence is classified as degraded
because health cannot be confirmed. Any down local port or missing expected local
flow entry takes precedence over healthy signals. Model evaluation does not require
a pinned artifact or build; provenance is recorded when available. The example's
5-second decision deadline is an operational scenario setting, not a universal
latency acceptance target. Report warm and cold latency distributions separately.
The opt-in harness was run from a root-capable shell. Both isolated primary
link faults (`s1-s2` and `s2-s4`) produced three rounds of bidirectional
primary timeouts with recovery-path probe success, and reset/cleanup were
verified. Evidence is retained at `/tmp/diamond-recovery-evidence`.

## Live diagnosis runner

The combined runner starts the topology, verifies the native service, sends
current-only local observations for each switch, and records model decisions
for healthy operation and both isolated link faults. It performs no recovery
action:

```bash
sudo env PYTHONPATH=. .venv/bin/python \
  examples/diamond-recovery/live_diagnosis.py \
  --endpoint http://127.0.0.1:8080 \
  --evidence /tmp/diamond-live-diagnosis
```

Decision records are written to `decisions.jsonl`; setup, fault, probe, reset,
and cleanup records remain in the harness JSONL files in the same directory.
The completed live run produced 16/16 expected diagnoses, including explicit
unavailable-evidence cases. The local artifact hash and server build were
recorded; cold-start latency was not measured because the running service was
not restarted.
