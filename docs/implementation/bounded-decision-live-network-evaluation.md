# Bounded-decision live-network evaluation

## Verdict: PARTIAL PASS / DIAGNOSIS INTEGRATION UNVERIFIED

The real Mininet/OVS topology and both isolated primary-link fault conditions
were exercised successfully. The harness evidence is retained at
`/tmp/diamond-recovery-evidence/harness.jsonl` and
`/tmp/diamond-recovery-evidence/rounds.jsonl`.

This establishes topology, link-state, probe, reset, and cleanup evidence. It
does not yet establish bounded-model diagnosis support because the harness does
not feed its live observations into the decision runtime. No recovery or model
support claim is made.

The initial Maintained Example scope was amended to current-only local switch
diagnosis. Consequently, the historical diamond recovery matrix is not an
acceptance target for this slice. The live harness remains an opt-in topology
and evidence tool; it does not make a model decision, install recovery rules,
or bypass capability authorization.

## Evidence inventory

| Evidence | Result |
| --- | --- |
| Offline contract and planning | Passed; see [offline acceptance](bounded-decision-offline-acceptance.md) |
| Native live-model evaluation | Partial pass, integration unverified; see [live-model report](bounded-decision-live-model-evaluation.md) |
| Compiled runtime against native service | 15 successful invocations using the fake substrate |
| Real Mininet/OVS diagnosis run | **16/16 correct** across healthy, both isolated faults, and unavailable evidence |
| Diamond recovery run | Not applicable to the amended initial scope; historical fixtures retained |
| Trial reset and cleanup verification | Not measured |
| Remote request termination/isolation | Unknown; no live-network trial was started |

## Blocking prerequisites

Before a live evaluation can be accepted, review the live-model report and
establish a usable native service with retained model/server/build/backend and
hardware provenance. A short smoke response or constructed-fixture matrix is
not evidence of live telemetry integration. If any required provenance or
runtime evidence remains unavailable, the result must stay unverified.

The installed Mininet/OVS binaries were detected, but the harness could not be
started by this agent because the session has no passwordless root access; the
operator nevertheless ran it interactively and retained the evidence listed
above.

The observed matrix was:

| Trial | Fault | Primary probes | Recovery probes | Reset/cleanup |
| --- | --- | --- | --- | --- |
| 1 | `s1-s2` down | Three rounds timed out in both directions | Successful in both directions | Verified |
| 2 | `s2-s4` down | Three rounds timed out in both directions | Successful in both directions | Verified |

The integrated run completed successfully using the documented runner. Its
final evidence is retained at
`/tmp/diamond-live-diagnosis-final-20261029T190536Z` and includes 16 decision
records: four healthy, four explicit unavailable-evidence, four `s1-s2` fault,
and four `s2-s4` fault cases. All expected labels matched the observed choices.
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

When the prerequisite is satisfied, use a new configuration/run and retain:

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
