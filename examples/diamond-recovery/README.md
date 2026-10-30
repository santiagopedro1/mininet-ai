# Diamond switch diagnosis (exploratory Maintained Example)

**Primary Goal:** demonstrate one native System 1 diagnosis agent per switch.
Planning uses the `fake` Substrate; the opt-in Mininet/OVS harness provides a
real four-switch topology. Each agent is scoped to one switch and receives only
its current local flow and operational port-state observations. No observation history,
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
each switch. Each is bound to current `openflow.flows` and `ovs.port-state`
observations, with diagnosis-only choices and no capabilities.

## Topology contract

Pinned switch ports: s1 host=1, s2=2, s3=3; s2 s1=1/s4=2; s3 s1=1/s4=2;
s4 host=1/s2=2/s3=3. Expected link mapping is h1-s1(1), s1-s2(2)/s1-s3(3),
s2-s4(2), s3-s4(2), s4-h2(1). Hosts use MACs
`02:00:00:00:00:01`/`02:00:00:00:00:02` and active addresses
`10.0.0.1`/`10.0.0.2`. Diagnostic aliases are primary `.11`/`.12` and recovery
`.21`/`.22`; install explicit peer static neighbors for all six host addresses
before probing. The trusted harness module declares the aliases, static-neighbor
map and initial/probe policy. The scenario embeds only per-switch local port
and flow expectations; it does not send network-wide policy/topology to the
model. The opt-in harness applies setup to a fresh
controller-free, fail-secure OVS network and verifies actual interface/port
mapping, table-miss drop, and absence of `NORMAL` forwarding. The `fake`
Substrate plan itself does not deploy these settings.

The network harness still configures the diamond and records its probe rounds
for topology diagnostics, but those rounds are not sent to System 1. A switch
agent request is current-only: its target switch identity, latest local port
operational state, and latest local flow state. Missing or unavailable fields stay
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

For the current blockers, service-review checklist, once-only operator command
and evidence return instructions, use the
[acceptance handoff](../../docs/implementation/bounded-decision-acceptance-handoff.md).
A later archived healthy real-runtime trial failed with four correct diagnoses,
two timeouts and unknown cleanup. The independent cleanup observer added after
that trial still requires live validation; do not treat earlier direct-client
results as acceptance.

The runner now uses the real `MininetOVSRuntime` and all four compiled agents
through `ExperimentRuntime`. It does not call the native client directly or
substitute an executor. Every trial deploys a fresh network, verifies the
baseline, applies a recorded trusted fault if needed, runs scheduled local
diagnosis, drains, and independently verifies cleanup before the next trial.
Trials cover healthy operation, both isolated link faults, a missing required
s2 flow, and explicit unavailable port-state evidence. No recovery action is
authorized. The outage is an explicitly recorded collector fault injection,
not an invented port state.

Use a disposable root-capable host with the native service already running.
Review the live-model report first: the current one-slot service timed out in
the corrected four-agent constructed-input check. Resolve/review that deployment
workload before repeating the network matrix; do not silently enlarge the
declared deadline or reuse historical 16/16 results as current acceptance.
The model identity below is an example declaration, not a pinned requirement;
replace it with your actual service/model. The runner does not start or alter
the service, silently retry failed trials, or overwrite an evidence directory:

```bash
sudo env PYTHONPATH=. .venv/bin/python \
  examples/diamond-recovery/live_diagnosis.py \
  --endpoint http://127.0.0.1:8080 \
  --model-name ggml-org/Kev-4B-GGUF \
  --model-repository ggml-org/Kev-4B-GGUF \
  --model-revision f4624b6bb7072f1bab726a622c1cad3f96030b9e \
  --model-artifact Kev-4B-Q8_0.gguf \
  --model-sha256 7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da \
  --evidence /tmp/diamond-runtime-diagnosis-$(date -u +%Y%m%dT%H%M%SZ)
```

Each trial retains `invocations.jsonl` (normalized coordination and decision
records), `plan.json`, `ledger.sqlite3`, `runtime-report.json`, and `summary.json`.
Trusted setup/fault records are kept in the matching `*-harness/` directory.
Labels are computed from the exact retained local inputs, including expected
flow matches, not supplied to the model. Missing required ports, unknown/down
states, absent or changed expected flows, and unavailable inputs are degraded.
Readiness verifies setup; the real Substrate owns final teardown. A nonzero exit
or unverified cleanup blocks repetition. All completed invocations, including
extra cadence ticks and failures, count; there is no cherry-picking of a good
response. Startup probes warm the service; this runner does not measure cold
latency. The root provenance file marks unobservable hardware/backend/launch
details and operator-declared hash verification honestly.
The runner requires independently observable idle `/slots` before each trial
and after each passed trial; an unavailable/busy service refuses continuation.
It verifies root before sending any inference and never requests sudo passwords
programmatically.

The historical direct-client live run produced 16/16 expected diagnoses against
port-state labels, including explicit unavailable-evidence cases. The local
artifact hash and server build were recorded. A fresh server first-inference
sample completed in 0.834 seconds; the single cold sample is retained separately
from the warm distribution.

**Acceptance limitation:** those prior samples predate the runtime/collector/
oracle fixes and do not validate this replacement runner. Its privileged live
rerun is pending (#125); passwordless sudo is unavailable to the agent. Offline
regressions exercise the actual continuous runtime with only transport and
Substrate substituted. See the live-network report for current status.
