# Link-Failure Latency

**Primary goal: both** — reproducible latency experiments and agent-controlled
forwarding. Ten switch agents install exact supplied alternate-path policies;
this is not autonomous route discovery or a model comparison.

## Supported invocation

From the repository root, with Python 3.14 and project dependencies installed:

```bash
uv run python examples/link-failure-latency/run.py --seed 0 --plan
uv run python examples/link-failure-latency/run.py --seed 7 --spec > link-failure.yaml
uv run mininet-ai validate link-failure.yaml
```

Live runs require Linux, root, Mininet, running Open vSwitch, iproute2, `ping`,
and the configured Ollama model/server. The default blueprint requests
`qwen3.5:latest` at `http://10.10.10.152:11434`. No live execution was performed
as part of rootless automated acceptance.

```bash
sudo "$PWD/.venv/bin/python" examples/link-failure-latency/run.py \
  --seed 0 --generator-version sha256-counter-v1 \
  --measured 10 --warmup 1 \
  --setup-budget 120 --injection-budget 10 --response-budget 120 \
  --cleanup-budget 30 --sample-period 0.1 --probe-period 1 \
  --output "$PWD/latency-run-001"
```

`--seed` is mandatory (unsigned 64-bit). `--generator-version` currently accepts
only `sha256-counter-v1`. `--output` must name a **new directory**. Budgets/periods
must be finite and positive; designated probe slots remain fixed at one second.
`--trials` is an alias for `--measured`. `--model` and `--ollama-host` override
the blueprint. A positional specification retains agent configuration, but the
explicit seed regenerates the topology. The old `--timeout`/`--poll-interval`
options and single-file output are not the supported v2 CLI.

The YAML blueprint/capability files remain configuration sources. The runner
rejects nonconforming network/address settings rather than recording false v2
provenance. The ordinary `mininet-ai run` command does not run this scheduler.

## Lifecycle and measurement

SHA-256 streams produce the ADR-0003 ten-switch ring plus 1–5 chords, distinct
host placements, canonical ports, and independent warm-up/measured failure
passes. Healthy-path faults have alternate paths in both directions. Actual
graph, paths, ports, policy rules, configuration, code revision/dirty state,
Python/package/platform provenance are retained in `run.json`. The full compiled
experiment and example replay record are retained under provenance environment;
the normalized scenario covers recovery policies for every eligible fault.
Resolved model identity is explicitly null when unknown: a tag is not an artifact ID.

Each attempt owns a fresh deployment and agent sessions. Baseline requires three
consecutive successful bidirectional rounds. After injection, both link endpoints
must be freshly observed down, and a designated probe must fail within the
injection window. Agents need not wait for that failed probe. No observed loss
means `no_observed_disruption`, not recovery. Recovery requires three consecutive
timely bidirectional all-success rounds, each bracketed by down checks.

| Measurement | Boundaries |
| --- | --- |
| Detection | Confirmed link-down → first agent recognition of explicitly global/harness evidence |
| Reasoning | Before Provider construction → decision/failure |
| Agent action-effect | First attempted action → shared confirmed recovery |
| Recovery | Confirmed link-down → third complete recovery round |
| Execution / verification | Individual capability execution / postcondition phases |

Raw ticks are exact monotonic nanosecond **strings**, not accuracy claims. Probe
and observation overhead, fixed-slot misses, Provider queueing, and sampled
non-atomic endpoint checks limit resolution. Shared recovery does not prove an
individual agent caused it. Overlapping phase durations must not be summed.
SDK `eventDetectionSeconds`, `reasoningSeconds`, and `actionEffectSeconds` retain
their original meanings separately; SDK action-effect is verification timing.

## V2 Run Evidence

* Immutable atomic `run.json`.
* Append-only/fsynced `events.jsonl`: transitions, readiness, observations, probes,
  admissions, Provider request proof, frozen trial end, cleanup, run terminal.
* `trials.jsonl` and ten `agents.jsonl` projections per finalized attempt, written
  after bounded cleanup. Trial outcome and cleanup outcome remain separate.
* Atomic `summary.json`, reconciled by the artifact validator.

Summaries exclude warm-up and late work from primary measured populations while
retaining their evidence. Counts distinguish requested, started, finalized,
cleanup-completed, unknown outcomes and unstarted remainder. Statistics are
count/mean/median/min/max, null when empty; **no percentiles**. Availability,
missing reasons and late exclusions accompany each grouping. Recovery rates use
all started measured attempts and, separately, valid disrupted recovered/timeout
attempts. Timing of recovery is **conditional on recovery**. Pooled agent values
are **descriptive correlated observations**, not independent trial repetitions.
Per-agent, failed-link, trial-outcome and phase-outcome breakdowns are labelled.

A failed warm-up or uncertain cleanup/Provider containment aborts before another
attempt. A recovered trial followed by cleanup failure remains recovered. The
legacy Mininet adapter can report unknown removal evidence; that is a safe abort,
not a passing teardown. Local cancellation/factory closure does not prove remote
termination. Ollama supports normal-response proof, not remote cancellation proof.

Storage/recorder failure is reported, never converted to success. Interruption or
crash may leave only a prefix, without projections or summary. Never infer
completion without terminal evidence; inspect residual resources before retrying.
Collection is bounded at cleanup expiry: outstanding operations remain incomplete;
callbacks arriving after projection finalization cannot rewrite saved outcomes.

```python
from mininet_ai.experiments.latency import validate_artifact
result = validate_artifact("latency-run-001")
print(result.state, result.integrity, result.issues, result.incomplete_reasons)
```

Historical v1 is one JSONL stream with embedded agents, command-submission timing
and pooled p95. It is **not interchangeable** with v2. Legacy Python helpers are
retained for compatibility tests, not evidence of ADR-0001–0006 acceptance.

## Acceptance

See [the automated/live acceptance checklist](../../docs/latency-acceptance.md).
Rootless tests do not certify live recovery, teardown, or remote termination.
