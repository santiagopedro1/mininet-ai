# Bounded-decision live-network evaluation

## Verdict: DIRECT-CLIENT TELEMETRY PASS / DEPLOYED RUNTIME UNVERIFIED

The real Mininet/OVS topology and both isolated primary-link fault conditions
were exercised successfully. The harness evidence is retained at
`/tmp/diamond-recovery-evidence/harness.jsonl` and
`/tmp/diamond-recovery-evidence/rounds.jsonl`.

This establishes topology, link-state, probe, reset, and cleanup evidence. A
later direct-client diagnosis runner also supplied live local observations to
the native model, as recorded below. Neither runner feeds those observations
through the deployed four-agent `ExperimentRuntime`. No recovery or accepted
deployed-runtime support claim is made.

The initial Maintained Example scope was amended to current-only local switch
diagnosis. Consequently, the historical diamond recovery matrix is not an
acceptance target for this slice. The live harness remains an opt-in topology
and evidence tool; it does not make a model decision, install recovery rules,
or bypass capability authorization.

## Evidence inventory

| Evidence | Result |
| --- | --- |
| Offline contract and planning | Passed; see [offline acceptance](bounded-decision-offline-acceptance.md) |
| Native live-model evaluation | 12/12 runtime-shaped constructed fixtures correct; see [live-model report](bounded-decision-live-model-evaluation.md) |
| Compiled runtime against native service | 15 successful invocations using the fake substrate |
| Direct native client with real Mininet/OVS observations | **16/16 correct** against the runner's port-state labels across healthy, both isolated faults, and unavailable evidence |
| Four deployed agents with real Substrate telemetry | Unverified; direct-client runner bypasses continuous runtime |
| Diamond recovery run | Not applicable to the amended initial scope; historical fixtures retained |
| Trial reset and cleanup verification | Recorded as verified for the topology and direct-client trials |
| Remote request termination/isolation | Completed native responses recorded for the successful matrix; cancellation/late-result containment not exercised by this matrix |

## Remaining acceptance blockers (reviewed 2026-10-29)

The service was usable and model/server provenance was recorded for the
successful matrix. This is exploratory evaluation, not pinned-build support;
unavailable provenance must remain explicit. Final code review identified:

- `live_diagnosis.py` calls `NativeSystemOneClient.decide()` sequentially using
  the first compiled scenario and manually assembled observations. It does not
  exercise the four deployed agents, coordination, scheduled deadlines, or
  normalized retained Run Evidence.
- The Maintained Example binds `portState` to `ovs.port-counters`, whose real
  collector supplies traffic counters, not operational up/down state. The
  direct runner instead uses `interface.isUp()`, masking that missing input.
- Instructions require detecting missing expected flows, but do not declare
  an inspectable local expected-flow set. The runner's correctness labels
  consider only port availability/state; missing-flow correctness is untested.

Resolve these gaps and evaluate the actual continuous runtime with real local
telemetry before accepting deployed diagnosis support. See the
[final review](bounded-decision-final-review.md). No live inference or network
trial was repeated during this documentation/check refresh.

The installed Mininet/OVS binaries were detected, but the harness could not be
started by this agent because the session has no passwordless root access; the
operator nevertheless ran it interactively and retained the evidence listed
above.

The observed matrix was:

| Trial | Fault | Primary probes | Recovery probes | Reset/cleanup |
| --- | --- | --- | --- | --- |
| 1 | `s1-s2` down | Three rounds timed out in both directions | Successful in both directions | Verified |
| 2 | `s2-s4` down | Three rounds timed out in both directions | Successful in both directions | Verified |

The direct-client telemetry run completed using the documented runner. Its
final evidence is retained at
`/tmp/diamond-live-diagnosis-final-20261029T190536Z` and includes 16 decision
records: four healthy, four explicit unavailable-evidence, four `s1-s2` fault,
and four `s2-s4` fault cases. All runner labels matched the observed choices;
those labels do not independently test forwarding-rule completeness.
Decision latency was **1.173–1.486 s**, mean **1.352 s**, n=16; output tokens
were zero. A separate fresh-server first inference using the documented launch
command completed in **0.834 s** (n=1), returned `healthy`, and generated zero
output tokens. The server log records model loaded/listening at about 4.86 s
after process start. Cold evidence is retained at
`/tmp/opencode/cold-start/cold-evidence.json`.

The runner can be repeated with:

```bash
sudo env PYTHONPATH=. .venv/bin/python \
  examples/diamond-recovery/live_diagnosis.py \
  --endpoint http://127.0.0.1:8080 \
  --evidence /tmp/diamond-live-diagnosis
```

Its `decisions.jsonl` must be retained with the harness JSONL files. The
runner performs diagnosis only and does not install recovery rules. The local
artifact hash was independently recorded as
`7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da`; the
server build was `b11429-d812350493`. The cold run used:
`llama-server -hf ggml-org/Kev-4B-GGUF -hff Kev-4B-Q8_0.gguf --ctx-size 8192
--parallel 1`.

For the pending deployed-runtime evaluation, use a new configuration/run and retain:

- readiness, actual switch/port mapping, baseline policy, and probe evidence;
- current-only local inputs for each target switch, including explicit missing
  fields and no history, memory, or other-switch state;
- exact native requests/responses, labelled diagnosis outcomes, warm/cold
  latency distributions, sample counts, and all failures;
- independent reset and cleanup outcomes between trials; and
- local cancellation versus independently verified remote termination.

Do not repeat a trial while reset, cleanup, or remote work remains unknown.
The work outcome and cleanup outcome must remain separate in the retained
evidence.

Configuration changes require a new run. No fallback, candidate substitution,
recovery action, or model-support promotion is authorized by this report.
