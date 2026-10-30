# H2O-Lightning native diagnosis evaluation (2026-10-30)

> **Follow-up prompt clarification:** The user requested explicitly classifying
> missing probe rules as degraded even when active traffic could still pass.
> The narrow clarification is now in the source YAML, but H2O still misses the
> same rule (7/8 correct); a separate broad rewrite also failed healthy cases.
> See the [clarification report](bounded-decision-probe-clarification.md). The
> original scenario/run described below remains historical evidence unchanged.

## Verdict: interface and measured timing pass / missing-flow correctness failure

The operator explicitly approved evaluating a different trained model after the
[candidate research](native-decision-model-candidates.md). The operator then
reported the remote service ready at `http://10.10.10.152:9931`.
No automatic fallback or model substitution occurred.

Readiness observed:

- `/health`: ok.
- `/props`: alias `h2oai/h2o-lightning-4b`, Q8_0, build `b11941-19ff817fb`.
- Model path:
  `/home/lospampa/models/h2o-lightning-4b-aba20a24/h2o-lightning-4b-Q8_0.gguf`.
- Four idle slots, each with 8192 context tokens, before every attempted case.
- Expected GGUF repository/revision:
  `h2oai/h2o-lightning-4b-gguf@aba20a244eb1038b8546cd25c2d6046f0c3e4256`.
- Expected publisher artifact SHA-256:
  `4127b2ae7710c3864c21c3d946c316f28a654a53d3a4ba99f383bbcaf2768e81`.

The supplied remote download command includes hash verification, but the
operator only replied ready, without separately confirming the checksum result.
Artifact hash/revision verification remains unconfirmed, not inferred from the
path. Remote hardware is the earlier operator-supplied RTX 3090/RAM snapshot,
not an independent measurement; actual launch/backend/offload placement are
unobserved. The requested launch command is retained separately from actual
observed properties. This is not a pinned-build support claim.

## Conditions and results

Use the original Maintained Example scenario, not either rejected prompt-tuning
revision. Only deployment/model identity changes. Current-only local inputs,
choice descriptions/instructions, empty proposals, one-second cadence and
five-second operational deadline remain unchanged.

Runs enter the real four-agent `ExperimentRuntime`, coordination, native client
and normalized Run Evidence. Only Substrate observations are constructed. No
real Mininet/OVS network, root operations or model-proposed mutations occur.
An injected transport delegate checks the exact canonical state's UTF-8 byte
length before sending each native POST and otherwise forwards the payload
unchanged. A value above 32,000 bytes is refused rather than silently exposed
to native H2O's 32,000-token head/tail cutting behavior. The byte bound is
sufficient to avoid that cut in the inspected server implementation. All actual
states were 1584–2754 bytes and native requests fit the slot context.

| Case | Correct / retained | Result |
| --- | --- | --- |
| Healthy, all four switches | 4 / 4 | Passed |
| Missing expected s2 flow | 3 / 4 | Failed: s2 selected healthy |
| Remaining 14 declared cases | Not attempted | Stopped after first failed case |

The full matrix is retained in `conditions.json` and `evaluate.py`; unattempted
cases include both link faults, missing flows on other switches, missing first
rule, equal-count incorrect inventory, wrong actions/cookies, reordered healthy
flows, unavailable port/flow inputs, unknown port and missing port. Offline
coverage or another model's results do not constitute H2O passes for them.

All eight native invocations completed successfully as transport/runtime
operations with zero output tokens and no timeouts. Seven matched the labels.
Inference-phase `decisionSeconds`: min 0.1419, max 0.6672, mean 0.4837 seconds,
n=8. These are inference phases, not whole-lifecycle or connectivity-recovery
measurements. The operational deadline was met in these observations, not
established for all possible loads. Startup probes precede decisions; cold-start
latency is unmeasured and cache state may persist across cases.

The incorrect request actually lacks this expected rule:

```json
{"cookie":"0x2b67","table":0,"priority":100,"match":{"in_port":"2","ip":true,"nw_dst":"10.0.0.11","nw_src":"10.0.0.12"},"actions":"output:1"}
```

For invocation `invoke-d04c3365-9a2b-4910-b1a7-cdef401e6321`, the service selected
healthy (probability 0.7705375258, degraded 0.2294624742, confidence
0.5410750515; 1087 input tokens, zero output tokens). The independent label is
degraded. Probabilities remain diagnostic-only; no threshold, answer rewrite,
prompt change, input hint or inference retry was introduced.

All slots were observed idle after each attempted case. This is not a claim
about individual historical termination causes. Fake cleanup verifies the
in-memory model only; actual network/host cleanup is outside this run.

## Retained evidence and next gate

Complete preflight properties/slot observations, exact source configuration,
compiled plans, requests/responses, labels, timings, SQLite ledgers, normalized
records, runtime reports and the reproducible driver are archived in
`evidence/diagnosis-h2o-native-20261030.tar.gz`.

- SHA-256: `77dbc72d960ecd81526cca86d74754c27819f6e620367e207c3fb8896425b701`.
- All eight execution records validate against the strict published schema.
- Source run: `/tmp/opencode/diagnosis-h2o-20261030T172648Z`.
- Initial readiness: `/tmp/opencode/diagnosis-h2o-preflight-20261030T172517Z`.

The source scenario is unchanged, all previous Kev/prompt failures remain
preserved, and no fallback is performed. #124 remains blocked on native
correctness; #125's privileged real-network matrix must not start from this
failed prerequisite. Further candidate or request-design work requires an
explicit decision, not more deadline or an automatic model swap.
