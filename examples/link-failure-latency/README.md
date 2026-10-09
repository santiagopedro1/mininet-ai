# Link-Failure Latency

Measure an agent response cycle after a link failure on a seeded random
10-switch ring, with one logical agent per switch. The two hosts initially use
the direct link between their attachment switches. Recovery must restore ICMP
connectivity through the other nine links while the injected failed link remains
down. There is no model comparison.

*Primary goal: both — a reproducible latency scenario and a demonstration of
agent-driven explicit forwarding with network-level effect measurement.*

## Generate and inspect (no root or Ollama required)

From the repository root:

```bash
.venv/bin/python -m mininet_ai.experiments.link_failure --seed 7 --plan
.venv/bin/python -m mininet_ai.experiments.link_failure --seed 7 --spec > link-failure.yaml
.venv/bin/mininet-ai validate link-failure.yaml
.venv/bin/mininet-ai plan link-failure.yaml
```

The generated JSON specification is also valid YAML. A seed fixes the ring's
switch order, host attachments, port numbers, and failed link. This is random
**ring generation**, not sampling arbitrary graph shapes. Rings guarantee an
alternate route without introducing automatic spanning-tree or fast failover.

## Run locally

Requires Linux, root, Mininet, running Open vSwitch, iproute2, `ping`, and a
reachable Ollama server with `qwen3.5:latest` installed. The default endpoint is
`http://10.10.10.152:11434`; thinking is disabled and sampling is pinned.

```bash
sudo "$PWD/.venv/bin/python" -m mininet_ai.experiments.link_failure \
  --seed 7 --trials 5 --timeout 180 --poll-interval 0.1 \
  --output "$PWD/link-failure-results.jsonl"
```

Use `--ollama-host` or `--model` to override the single configured Provider.
Ten agents can reason concurrently; Ollama's own request queueing is included
in reasoning time. Each trial gets a fresh topology and fresh agent sessions.
The harness installs and checks baseline forwarding before injecting the fault.
Baseline setup is outside the measured response cycle. Agent instructions
request the precomputed alternate-path policy; this measures reasoning and
policy installation, **not autonomous route discovery**. Proposals must exactly
match that policy before any are executed. Agents cannot re-enable the link.

Do not use the ordinary `mininet-ai run` command to collect these measurements:
the generated specification alone does not contain the trial scheduler or
network-recovery collector. Use the Python module above.

## Measurement definitions

All experiment timestamps use the process monotonic clock and are exported as
seconds relative to failure-command submission. Wall-clock event timestamps
are not subtracted from monotonic timestamps.

| Field | Definition |
| --- | --- |
| `detection_seconds` | Failure-command submission to a worker observing the failed link DOWN in global topology state. Includes administrative fault application, confirmation of traffic disruption, worker scheduling, and polling. This is not local switch detector latency. |
| `reasoning_seconds` | Existing runtime duration of Provider construction and invocation, including model/server queueing and response parsing. Context construction is outside this field. |
| `action_execution_seconds` | Existing runtime duration of attempted capability execution. |
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
sudo env MININET_AI_LIVE_TESTS=1 "$PWD/.venv/bin/python" -m pytest \
  tests/integration/test_link_failure.py -q
```

The live acceptance test uses deterministic Agno responses, not Ollama, to
isolate actual forwarding and measurement behavior. It is opt-in and skips
without root or required tools. Open vSwitch must already be running.
