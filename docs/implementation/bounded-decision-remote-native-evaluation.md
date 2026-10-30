# Remote native diagnosis evaluation (2026-10-30)

> **Follow-up:** Two authorized prompt revisions corrected missing-flow choices
> but regressed down-port diagnosis (29/30 and 23/26 overall). Both were rejected
> and the original source wording restored. See the
> [prompt-tuning report](bounded-decision-prompt-tuning.md) and separate archive.
> No accepted prompt fix or real-network pass is claimed.

## Verdict: measured deadline success / missing-flow correctness failure

The operator moved the service to another computer. The new endpoint is
`http://10.10.10.152:9931`; `/health`, `/props` and `/slots` were checked before
inference. It serves Kev-4B Q8_0 with observed build `b11941-19ff817fb` and four
idle slots, each with 8192 context tokens. The observed artifact path includes
revision `f4624b6bb7072f1bab726a622c1cad3f96030b9e`. The remote artifact was not
independently hashed; its SHA-256 remains an expected declaration. Remote
hardware, backend, layer placement and full launch configuration are unavailable
and are not inferred from the earlier local machine.

All runs use the real four-agent `ExperimentRuntime`, native client, scheduling,
coordination and retained execution records. Only the Substrate is replaced by
constructed current-only local observations. No real OVS network, root access,
model-proposed mutation, controlling chat model or oracle-selected production
answer is used. Cadence remains one second and the decision deadline five
seconds. Startup interface probes precede scheduled decisions; no cold-start
latency is measured. Extra scheduled invocations count, including mistakes.

| Constructed case | Correct / retained | Result |
| --- | --- | --- |
| Healthy, all four switches | 6 / 6 | Passed |
| s1-s2 local ports down (s1-eth2, s2-eth1) | 7 / 7 | Passed |
| s2-s4 local ports down (s2-eth2, s4-eth2) | 7 / 7 | Passed |
| Missing expected s2 flow | 5 / 7 | Failed: s2 selected healthy twice |
| Wrong local flow | Not attempted | Stopped after failure |
| Unavailable port evidence | Not attempted | Stopped after failure |

All 27 native invocations succeeded as transport/runtime operations, with zero
generated output tokens and no timeouts. They are not all correct diagnoses:
25/27 matched the labels. Inference-phase `decisionSeconds` ranged from
0.3829 to 1.8903 seconds (mean 0.8172, n=27). These are not full lifecycle or
network recovery durations. The declared operational deadline was met in the
retained runs; this is not a universal latency benchmark or an extended soak
test. Probabilities/confidence remain diagnostic-only.

Both incorrect s2 inputs actually lack the same expected flow:

```json
{"cookie":"0x2b67","table":0,"priority":100,"match":{"in_port":"2","ip":true,"nw_dst":"10.0.0.11","nw_src":"10.0.0.12"},"actions":"output:1"}
```

The model returned healthy with probabilities 0.623656 and 0.634026. The missing
entry is present in the supplied expectations and absent from current flows;
the evaluator's expected diagnosis is degraded. No confidence threshold,
deterministic production override, prompt change, model substitution or replay
was applied after observing this failure.

All slots were observed idle before and after each attempted case. This does
not explain individual historical request termination. Fake cleanup verifies
only its in-memory model, not host/network cleanup. Each case starts a fresh
runtime, but server cache state persists: this is not independent cold or
held-out evaluation.

## Evidence

Complete requests, responses, contexts, labels, timing, startup provenance,
pre/post slot observations, compiled plans, SQLite ledgers and runtime reports
are retained in `evidence/diagnosis-remote-native-20261030.tar.gz`:

- SHA-256: `68bce436b784662dd5f3ed219d7dd776a9e1a43dc081230310b06fd3ad3c03f5`
- `healthy/`: initial healthy deployment check (six records).
- `labelled/`: both link-fault cases and the failed missing-flow case (21
  records); unattempted cases have no fabricated evidence.
- All 27 execution records validate against the strict published schema.

Source run directories are `/tmp/opencode/diagnosis-remote-9931-20261030T155019Z`
and `/tmp/opencode/diagnosis-remote-labelled-20261030T155104Z`.

## Next gate

#124 remains open for missing-flow correctness and the unattempted cases.
Review the retained failure before agreeing on any prompt/model/service change;
any such change requires new declared conditions and a new complete evaluation,
with this failed result preserved. No automatic retry was performed. #125's
real-network matrix remains blocked, not passed by constructed observations.
