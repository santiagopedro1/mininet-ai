# Finish bounded-decision acceptance (#116)

> **Live update (2026-10-30, supersedes network status below):** The operator's
> new real Mininet/OVS H2O run passed all five cases, **20/20 correct**, with
> independent cleanup verified and exit 0. Plans, SQLite integrity, strict
> execution/state schemas and labels were audited. See the
> [live H2O report](bounded-decision-live-h2o-acceptance.md). Generic runtime
> cleanup still reports unknown; the separate observer supplies verification.
> #124's broader native-quality limitation remains; no final support-gate waiver
> or issue closure is implied. Earlier failed/unattempted network statuses below
> are historical, not the latest matrix result.

> **Owner direction:** Stop further model sweeps and tuning for now and finish
> implementation. Record the original case missed by both models and the possible
> information/representation limitation on #124, without declaring its cause
> proven. Rich-state results are H2O-only. Implementation proceeds with these
> known limitations; neither native correctness nor real-network acceptance is
> reported as passed.

> **Latest state experiment:** The example explicitly opts into context v1alpha2
> with static local flow roles and the original prompt. H2O passes the inspected
> s2 miss and both port-fault cases but fails another missing s1 probe (19/20
> correct; 12 later cases unattempted). See the
> [rich-state evaluation](bounded-decision-rich-state-evaluation.md). Offline
> regression: **1218 passed, 3 opt-in skips, 179 subtests passed**. Native
> correctness and #125's privileged network matrix remain blocked.

> **Current wording:** The user-requested narrow prompt clarification explicitly
> includes diagnostic-probe/drop rules even if active traffic could still pass.
> Offline regression is **1180 passed, 3 opt-in skips, 179 subtests passed**.
> H2O still misses the same rule (healthy 4/4, missing-probe case 3/4); later native
> cases were not attempted. See the
> [clarification report](bounded-decision-probe-clarification.md). Earlier
> original-wording and rewrite results below remain historical, not acceptance.

> **Latest native correctness gate:** The explicitly approved H2O-Lightning-4B
> Q8_0 evaluation also failed missing-flow diagnosis (7/8 correct, no timeouts).
> [Evidence and limitations](bounded-decision-h2o-evaluation.md) are retained;
> fourteen later cases were not attempted. Do not proceed to the real-network
> matrix on this failed prerequisite. The source scenario is unchanged.

> **CLI repair completed (2026-10-30):** The offline CLI/results integration
> blocker described below is resolved on `fix/cli-results-integration`: full
> suite **1178 passed, 3 opt-in skips, 179 subtests passed**; lint, formatting,
> Pyright and diamond/iperf planning checks pass. See the
> [repair report](cli-results-integration.md). Earlier failing counts below are
> retained as history. Native correctness and privileged network acceptance
> remain blocked; the offline repair is not a model-support pass.

> **Prompt tuning attempted:** Two revisions improved missing-flow choices but
> regressed local port-fault diagnosis. Both were rejected and original wording
> restored; [all tuning evidence is retained](bounded-decision-prompt-tuning.md).
> Do not repeat those candidates or proceed to the real-network matrix as if
> correctness had passed. A new model/request-design evaluation needs an explicit
> decision, not an automatic fallback.

> **Latest native gate (2026-10-30):** The new remote endpoint
> `http://10.10.10.152:9931` completed 27 constructed-input runtime invocations
> without timeouts, but s2 twice misclassified a missing expected flow as
> healthy (25/27 correct overall). Remaining cases were not attempted after
> that failure. See the [remote evaluation](bounded-decision-remote-native-evaluation.md).
> Review this correctness failure before following the live-network steps below;
> moving the service resolved the observed timeout blocker, not acceptance.

## Status refreshed 2026-10-30

The committed implementation is already on `main` through PR #128
(`a0f5224`). PR #126 is closed without a merge; it is not a pending draft.
The integration branch's committed tree matches that `main` commit. Issues
#116, #124 and #125 remain open for live acceptance, not another feature merge.
An existing uncommitted edit in `mininet_ai/specification/models.py` was left
untouched; checks of the working tree include that edit.

The latest archived real-OVS healthy trial is **failed**, not unattempted:
four correct diagnoses and two timeouts across six invocations. Its cleanup
status is unknown. PR #128 subsequently added independent cleanup verification;
that fix has offline coverage but still needs a new privileged live run.
The historical direct-client 16/16 and constructed-fixture 12/12 results do not
establish acceptance of the corrected deployment.

Current scope: four current-only local diagnosis agents, healthy/degraded
choices, no history, no action proposals, one-second cadence, and the example's
five-second operational deadline. Neither pinned builds nor a universal
800 ms model benchmark are acceptance requirements.

### Checks repeated during this handoff

| Check | 2026-10-30 result |
| --- | --- |
| Full `uv run pytest` | **137 failed, 1032 passed, 3 skipped**; not a clean regression pass |
| Focused current diagnosis/runtime/cleanup/guards/evidence tests | **57 passed** |
| `uv run ruff check .` / `uv run ruff format --check .` | Passed; 230 files formatted |
| `uv run pyright` | 0 errors, warnings or information messages |
| Diamond and iperf `validate` / `plan` | Passed; 4 and 2 agents respectively |
| Both committed diagnosis evidence archives | All 12 execution records pass the strict execution schema |

The earlier 1049-pass report is historical, not a result for this merged tree.
A fast failing reproduction is:

```bash
uv run pytest -q tests/test_results_acceptance.py::test_run_help_explains_inspection_and_independent_output_controls --tb=short
```

It fails because CLI help lacks the expected results guidance (`summary.md`).
Other failures reject `--results-dir`; the checked-in CLI lacks that option.
Imports resolve to this checkout, not an unrelated installed package. The
committed tree matches current `main`; this is not a docs-only modification or
the existing blueprint-validator edit. The merge at `022a786` changed the CLI
relative to its `main` parent while retaining acceptance tests. Review that
integration before restoring either side: do not discard tests or blindly
replace the CLI and lose bounded-execution changes. These observations explain
a reproducible failure class, not a diagnosis of every one of the 137 failures.
Resolving and reviewing this regression is an additional agent-runnable task
before a clean completion claim; it does not require root or model inference.

## 1. Review the service configuration with the operator

Before any more inference, provide the actual server launch command (redact
credentials), endpoint, model/artifact identity, build, backend, hardware,
context allocation and slot count. Declare any unrelated service load. Record
unobservable fields as unavailable rather than guessing them.

The existing one-slot service has timed out under the four-agent workload.
Choose and explicitly record a deployment suitable for that workload: for
example, faster inference or adequate parallel capacity on available hardware.
Adding slots alone is not a guaranteed fix: per-slot context and memory must
still accommodate the exact requests. Do not change the model, cadence,
deadline or request shape silently. Any agreed change starts a new run with
new provenance; keep failed runs.

Read-only checks (these do not warm the model or establish inference support):

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/health
curl --fail --silent --show-error http://127.0.0.1:8080/props
curl --fail --silent --show-error http://127.0.0.1:8080/slots
```

Every slot must explicitly report `is_processing: false` before a trial.
Unavailable/busy status blocks continuation. An idle observation does not
explain how individual earlier requests terminated. Separately reconcile the
old run's unknown network cleanup on its disposable host; do not assume an
idle model service proves network cleanup. Do not use broad `mn -c` cleanup on
a shared host or kill unrelated processes.

**Handoff to the agent:** server command/configuration and the three read-only
responses. Review this before launching the network matrix. A successful live
matrix can also provide native-runtime evidence, but #124's applicable evidence
must be reviewed before claiming #125 accepted.

## 2. Run the matrix from your own terminal

Only on a disposable Linux host with Mininet, OVS and the repository's Python
3.14 environment installed. The agent cannot enter your sudo password; no
passwordless-sudo configuration change is required. From the repository root,
check the runner and authenticate interactively:

```bash
PYTHONPATH="$PWD" uv run python examples/diamond-recovery/live_diagnosis.py --help
sudo -v
```

After service/deployment review and cleanup reconciliation, run the following
**once** if these identity values still describe your actual service. Otherwise
replace every identity argument with the agreed service's values first. The
hash is an operator declaration unless independently verified; preserve that
distinction. Do not execute the YAML experiment directly: the runner owns the
trusted network setup and evidence collection.

```bash
EVIDENCE="$PWD/diagnosis-evidence-$(date -u +%Y%m%dT%H%M%SZ)"
sudo env PYTHONPATH="$PWD" .venv/bin/python \
  examples/diamond-recovery/live_diagnosis.py \
  --endpoint http://127.0.0.1:8080 \
  --model-name ggml-org/Kev-4B-GGUF \
  --model-repository ggml-org/Kev-4B-GGUF \
  --model-revision f4624b6bb7072f1bab726a622c1cad3f96030b9e \
  --model-artifact Kev-4B-Q8_0.gguf \
  --model-sha256 7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da \
  --evidence "$EVIDENCE"
RESULT=$?
printf 'Runner exit: %s\nEvidence: %s\n' "$RESULT" "$EVIDENCE"
```

The root-owned output directory must not already exist. Do not run the whole
agent or `uv` as root. The runner uses all four compiled agents and performs
healthy, s1-s2 fault, s2-s4 fault, missing-flow and unavailable-input trials,
stopping at the first failure. It checks remote idle status between passed
trials and independently observes network cleanup. Startup probes precede
scheduled decisions; this measures warmed operation, not cold-start latency.

**If nonzero, interrupted, or cleanup is unknown: stop.** Keep the partial
directory, terminal error and all failures. Do not rerun, bypass the idle gate,
extend deadlines, or resume at a later trial to obtain a passing subset.
Read-only service status may help diagnose the blocker; resource cleanup and
remote isolation must be established independently before any new run.

## 3. Bring back evidence for review

Send the agent the evidence directory path (or transfer the complete directory
to the review checkout), server launch/configuration, terminal output and exit
status. Do not send only a success screenshot. Required contents include:

- root experiment/provenance, observed properties and remote-idle observations;
- each attempted trial's `plan.json`, `invocations.jsonl`, `ledger.sqlite3`,
  `runtime-report.json` and `summary.json` (or its startup failure);
- matching `*-harness/` setup, fault and independent cleanup evidence.

The agent can then validate retained records against strict schemas, reconcile
all samples/labels/failures, report warm latency with sample counts separately
from any cold measurements, audit cleanup and isolation, and publish the final
reports. Every trial must pass; missing/unattempted evidence is not success.

Only after reviewed applicable native and network evidence should #124 and
#125 be resolved and #116 receive its final acceptance/support handoff. If the
deployment still fails, publish that verdict and leave acceptance blocked.
Any follow-up changes need their own checks/review/PR; no merge is authorized
by this runbook.
