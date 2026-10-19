# Link-Failure Latency

Generate reproducible ten-switch ring-plus-chords scenarios, with one logical
agent per switch and two independently seeded, distinct host attachments.
Recovery uses supplied minimum-hop forwarding policies while a used switch link
remains down. There is no model comparison.

**Scope:** scenario generation implements ADR-0003 version 1. The live collector
below is still the legacy v1 collector; it does **not** implement the sustained
bidirectional recovery, timing, warm-up, bounded lifecycle, Provider containment,
or v2 evidence contracts in ADRs 0001–0006. Do not treat its results as acceptance
of those contracts. Live recovery acceptance is outside this implementation.

*Primary goal: both — a reproducible latency scenario and a demonstration of
agent-driven explicit forwarding with network-level effect measurement.*

## Files

- `experiment.yaml`: checked-in seed-0 topology, switch-agent placement, and policies.
- `agent-blueprints/ollama-switch-router.yaml`: Ollama model, endpoint, and agent instructions.
- `capabilities/openflow-flow-install.yaml`: allowed forwarding action contract.
- `run.py`: example-local failure injection, measurements, and result collection.

The YAML files are the source of configuration, like the other maintained
examples. The runner loads them rather than defining agents and capabilities
in the installed `mininet_ai` package.

## Validate and inspect (no root or Ollama required)

From the repository root:

```bash
.venv/bin/mininet-ai validate examples/link-failure-latency/experiment.yaml
.venv/bin/mininet-ai plan examples/link-failure-latency/experiment.yaml
.venv/bin/mininet-ai run examples/link-failure-latency/experiment.yaml --dry-run
```

The checked-in topology matches the ADR-0003 seed-0 conformance vector. To generate
another reproducible topology while retaining the YAML agent configuration:

```bash
.venv/bin/python examples/link-failure-latency/run.py --seed 7 --plan
.venv/bin/python examples/link-failure-latency/run.py --seed 7 --spec > link-failure.yaml
.venv/bin/mininet-ai validate link-failure.yaml
```

The generated JSON specification is also valid YAML. Generation requires an
explicit integer seed in `[0, 2^64 - 1]`; the `generator-version` label is
`sha256-counter-v1`. SHA-256 counter streams independently determine the cycle,
1–5 distinct non-cycle chords, host placement, and failure-pass ordering. This is
a **ring-plus-chords distribution**, not uniform sampling of connected graphs.
Ports follow UTF-8 neighbor ordering, with host ports last. All links use
100 Mbps, 1 ms delay, and zero configured loss; switches are controller-free,
secure OpenFlow13, with no learning, flooding, NORMAL, or automatic failover.

Healthy and recovery policies are destination-rooted minimum-hop trees, choosing
the smallest UTF-8 next-hop ID among equally short alternatives. They include
explicit host-traffic and directed ARP rules. Only healthy-path switch links with
both directional recovery routes are eligible faults. The measured scheduler
consumes the entire shuffled eligible set before starting a fresh pass; unsuccessful
attempts still consume their selections.

The rootless `FailurePasses(seed, eligible, kind="warmup" | "measured")` iterator in
`mininet_ai.experiments.scenarios` provides independent per-pass streams for the
future lifecycle coordinator. It does not execute warm-up trials.

For exact replay, the runner's `LinkFailureScenario.to_record()` retains the actual
graph, ports, attachments, selected fault, both directional paths, and every
switch's healthy/recovery policies, together with the compiled specification and
generator configuration. `generator_configuration` describes the version-1
distribution; the retained `experiment` is authoritative for actual network
settings, including saved-YAML overrides. `LinkFailureScenario.from_record(record)` uses those saved
inputs and policies, not regenerated seed output. Manifest and trial JSONL records
include this data under `scenario`. Replay records are trusted experiment inputs,
not an interface for accepting untrusted action policies.

## Run locally

Requires Linux, root, Mininet, running Open vSwitch, iproute2, `ping`, and a
reachable Ollama server with `qwen3.5:latest` installed. The default endpoint is
`http://10.10.10.152:11434`; thinking is disabled and sampling is pinned.

```bash
sudo "$PWD/.venv/bin/python" examples/link-failure-latency/run.py \
  --trials 5 --timeout 180 --poll-interval 0.1 \
  --output "$PWD/link-failure-results.jsonl"
```

Without `--seed`, the runner uses the checked-in topology as written. Edit the
blueprint YAML to change the single configured Provider, or use `--ollama-host`
and `--model` for temporary overrides. Pass a specification path as the first
argument to run a saved generated specification. With `--seed`, only the
topology and generator labels are regenerated; other YAML settings are retained.
The runner is specific to this scenario: two hosts named `h1` and `h2`, attached
to distinct switches in a ten-switch general graph.
Without regeneration, forwarding policies use the addresses and port numbers
from the YAML plan.
Ten agents can reason concurrently; Ollama's own request queueing is included
in reasoning time. Each trial gets a fresh topology and fresh agent sessions.
The harness installs and checks baseline forwarding before injecting the fault.
Baseline setup is outside the measured response cycle. Agent instructions
request the precomputed alternate-path policy; this measures reasoning and
policy installation, **not autonomous route discovery**. Proposals must exactly
match that policy before any are executed. Agents cannot re-enable the link.

Do not use the ordinary `mininet-ai run` command to collect these measurements:
the specification alone does not contain the trial scheduler or
network-recovery collector. Use the example-local `run.py` above.

## Measurement definitions

All experiment timestamps use the process monotonic clock and are exported as
seconds relative to failure-command submission. Wall-clock event timestamps
are not subtracted from monotonic timestamps.

| Field | Definition |
| --- | --- |
| `detection_seconds` | Failure-command submission to a worker observing the failed link DOWN in global topology state. Includes administrative fault application, confirmation of traffic disruption, worker scheduling, and polling. This is not local switch detector latency. |
| `reasoning_seconds` | Existing runtime duration of Provider construction and invocation, including model/server queueing and response parsing. Context construction is outside this field. |
| `action_execution_seconds` | Sum of monotonic wall durations of attempted capability executions, including attempts that raise errors. |
| `action_effect_seconds` | First attempted recovery action by that agent to the shared successful host-to-host connectivity observation, provided the failed link is still DOWN. Includes execution and recovery polling, not just postcondition verification. |
| `recovery_seconds` | Failure-command submission to the successful connectivity observation at trial level. |
| `elapsed_seconds` | Failure-command submission to completion, including draining in-flight workers. Baseline/deployment-failed trials instead measure failed setup. |

The action-effect endpoint is **shared network recovery**, not evidence that an
individual agent caused recovery. A switch may not need to change its rules,
and connectivity may return before every agent finishes. Actions that start
after the recovery observation have no action-effect value. The three primary
phase durations do not partition the full response cycle: context building,
scheduling, and overlapping agent work prevent summing them meaningfully.

The harness first proves baseline connectivity, then proves that the injection
disrupted it before starting the agent workers. A single successful ICMP probe
confirms bidirectional packet delivery; it is not a sustained traffic or
throughput guarantee. Probe duration and the polling interval limit measurement
resolution; failed ICMP probes can take approximately one second.

## Results and unsuccessful trials

The JSONL output contains a versioned manifest (including the full generated
specification and plan digest), one record per trial, and a final summary. Each
trial record is flushed to disk before teardown. Existing files are never
overwritten. Raw agent records include invocation identifiers and statuses.

Missing phases remain `null`, never synthetic zeros. Trial statuses distinguish
`recovered`, `timeout`, `baseline_failed`, `injection_failed`, and `error`.
Deployment failures also produce `error` trial records with a null run ID.
Cleanup failures produce a separate `cleanup_error` record and stop further
trials rather than risk deploying over an incompletely released topology. The
summary of completed trials is still written before the error is propagated.
Summary distributions report measured count, missing count, mean, median, and
nearest-rank p95. Recovery statistics contain observed recovery only; the
status counts and missing counts retain unsuccessful trials. Agent-phase
statistics contain all available measurements, including failed invocations.

A trial timeout stops new actions; already-running reasoning/action calls are
drained before teardown, so wall time can exceed `--timeout`. The configured
reasoning timeout is 120 seconds. Results are not filtered for model warm-up;
record any external Ollama warm-up procedure separately. Interrupted runs may
have trial records but no final summary.

## Tests

```bash
.venv/bin/python -m pytest tests/experiments/test_link_failure.py -q
.venv/bin/python -m pytest tests/experiments/test_seeded_scenarios.py -q
sudo env MININET_AI_LIVE_TESTS=1 "$PWD/.venv/bin/python" -m pytest \
  tests/integration/test_link_failure.py -q
```

The live acceptance test uses deterministic Agno responses, not Ollama, to
isolate actual forwarding and measurement behavior. It is opt-in and skips
without root or required tools. Open vSwitch must already be running.
