# Bounded-decision live candidate evaluation

> **New live-network evidence:** H2O passed the current five-case real Mininet/OVS
> runner matrix, 20/20 correct with independently verified cleanup. See the
> [live H2O report](bounded-decision-live-h2o-acceptance.md). This narrower result
> does not erase the constructed missing-s1 probe failure or establish general
> native-model correctness. Final acceptance/support disposition remains explicit.

> **Latest enriched-state result (2026-10-30):** Explicit context v1alpha2 static
> local flow roles with the original prompt correct the inspected s2 miss and
> preserve both tested port-fault choices, but H2O misses another probe on s1
> (19/20 correct). The matrix stopped with 12 cases unattempted. See the
> [rich-state evaluation](bounded-decision-rich-state-evaluation.md). #124/#125
> remain blocked; prior prompt/model failures below remain historical evidence.

> **Latest prompt clarification (2026-10-30):** Source instructions and choices
> now explicitly require diagnostic-probe/drop rules regardless of possible
> active connectivity. H2O still misclassifies the same missing rule (healthy
> 4/4, missing-probe case 3/4); the matrix stopped, and native down-port behavior
> for the new wording is unverified. See the
> [clarification report](bounded-decision-probe-clarification.md). #124/#125
> remain blocked; this is not an accepted native correctness fix.

> **Latest approved-model result (2026-10-30):** The distinct H2O-Lightning-4B
> native candidate completed eight invocations without timeouts, but misclassified
> missing expected s2 flow as healthy (7/8 correct). The matrix stopped at the
> first failed case; fourteen declared cases remain unattempted. See the
> [H2O evaluation](bounded-decision-h2o-evaluation.md). #124/#125 remain blocked;
> no source prompt was changed or automatic fallback performed.

> **Latest tuning outcome (2026-10-30):** Neither of two prompt revisions passed
> the native matrix: missing-flow choices improved but down-port diagnosis
> regressed. Both failed runs are archived separately; original source wording
> is restored. See the [prompt-tuning report](bounded-decision-prompt-tuning.md).
> #124 and the real-network acceptance gate remain blocked on correctness.

> **Latest remote result (2026-10-30):** On `10.10.10.152:9931`, all 27
> constructed-input runtime invocations completed without timeouts, but two
> missing-flow diagnoses were wrong (25/27 correct). The matrix stopped at that
> failure; wrong-flow and unavailable-input cases were not attempted. #124
> remains open. See the [remote evaluation](bounded-decision-remote-native-evaluation.md)
> and its complete checksummed archive. Earlier local throughput failures below
> remain valid for their own deployment conditions, not the remote service.

> **2026-10-30 status refresh:** A subsequent real-OVS healthy trial retained
> four correct diagnoses and two timeouts across six invocations, with unknown
> network cleanup. This is separate from the constructed-input two-correct /
> four-timeout run below; neither establishes deployment acceptance. All 12
> execution records across the two committed archives validate against the
> strict execution schema. PR #128 merged the later cleanup-observer fix into
> `main`; new live validation is still required. See the
> [live-network refresh](bounded-decision-live-network-evaluation.md) and
> [operator handoff](bounded-decision-acceptance-handoff.md).

> **Scope note (2026-10-29):** The original recovery-model evaluation below is
> retained as historical evidence. Issue #116 and child #124 were amended to
> make the initial evaluation current-only per-switch diagnosis, with no pinned
> model/build requirement and warm-model latency measured separately. The
> historical network-wide recovery attempts do not satisfy the amended labelled
> diagnosis evaluation. The 800 ms universal latency gate has since been
> removed: latency is reported with conditions and distributions, while each
> scenario's declared deadline is an operational safety cutoff. The maintained
> example now declares 5 seconds.

## Amended per-switch evaluation attempt

Port 8080 was not listening. Without changing either server, one attempt was
made against the already-running port-8081 server (`b11669-620d2225c`, one slot,
context 4096, Q8_0 artifact, CPU-only launch recorded in the original provenance
below). The request was an excluded warm-up for the new `s1` local-diagnosis
input; the labelled matrix did not start. The warm-up timed out after **5.0084 s**.
Server logs subsequently showed task cancellation and slot release about **1.367 s**
later (`n_tokens=172`, which is the prompt token count at release, not successful
inference). No choice response was received. No labelled cases were sent and no
retry was made. Thus this run provides no warm-model latency or diagnosis
correctness measurement; port 8080's formerly responsive warm service was
unavailable. Exact warm-up request, timeout, and server properties are retained
at `/home/pedro/.cache/mininet-ai/kev-evaluation/switch-diagnosis-matrix-8081.json`.

On a later user-requested attempt, port 8080 was again available and reported
Q8_0, build `b11429-d812350493`, one slot, and context 8192. The first matrix
  attempt accidentally included case labels in the request state; its apparent
12/12 correctness is invalid and excluded. A corrected run removed all labels
from request payloads, used one excluded warm-up (`healthy`, **0.5161 s**), then
completed all 12 labelled requests: four healthy states, four explicit
local-link faults, and four cases with unavailable port-state evidence. **11/12**
choices matched; `s2-degraded-link` was incorrectly classified as healthy. After
making the local-port-down precedence explicit and representing port states
structurally, a focused s2 regression request returned `degraded` in **0.6696 s**
(probability 0.9687). A subsequent full 12-case run using the revised prompt
matched all 12 labels. Latency was **0.4773–0.6810 s** (mean **0.5800 s**), all
within 800 ms. Because the prompt was revised after inspecting the s2 failure,
this is an iterative prompt-tuning result, not an independent held-out evaluation.
The requests were constructed local-state payloads sent directly to the native
endpoint, not observations collected from live OVS switches; this establishes
prompt-level behavior, not end-to-end runtime/telemetry integration. Backend and
hardware were not verified. No network action was requested or executed. The
corrected evidence is
`/home/pedro/.cache/mininet-ai/kev-evaluation/switch-diagnosis-matrix-8080-v2.json`;
the revised-prompt regression is
`/home/pedro/.cache/mininet-ai/kev-evaluation/s2-degraded-rule-check-8080.json`;
the revised-prompt matrix is
`/home/pedro/.cache/mininet-ai/kev-evaluation/switch-diagnosis-matrix-8080-v3.json`.
The earlier label-leaking run is retained separately at
`/home/pedro/.cache/mininet-ai/kev-evaluation/switch-diagnosis-matrix-8080.json`
and remains invalid.

## Runtime-shaped current-only matrix (2026-10-29)

After the scenario deadline was changed to 5 seconds, the evaluation used the
actual `NativeSystemOneClient` contract and `POST /v1/systemone` question shape
(`questions.next_action`). Requests carried the runtime's
`apiVersion`/scenario/identity/single-switch/diagnosis envelope; each had only
one target and no history, labels, memory, other-switch state, or action
proposals. The excluded warm-up returned `healthy` in **0.7375 s**. The 12
labelled constructed-fixture cases (four healthy, four local-port-down, four
required port-evidence unavailable) all returned the expected choice: **12/12**.
Warm latency was **0.6967–0.7375 s** (mean **0.7133 s**, n=12), below the
scenario's 5-second operational deadline. This is one sequential warm run on an
otherwise idle one-slot service; it is not a cold-start, load, or independent
held-out measurement. Probabilities/confidence remain diagnostic only.

Exact requests, responses, evaluator-side labels, timing, and provenance are
retained at
`/home/pedro/.cache/mininet-ai/kev-evaluation/switch-diagnosis-runtime-8080-v1.json`.
The earlier direct-prompt results and old 800 ms timeout remain historical and
are not acceptance evidence under the revised deadline. The matrix uses
runtime-shaped constructed local-state fixtures, not observations collected
from live OVS switches. Thus native interface and constructed-fixture labelled
correctness are measured; live telemetry integration, cold-start/load behavior,
and real-network diagnosis remain unverified. No network action was requested
or executed.

## Verdict: CURRENT FOUR-AGENT DEADLINE FAILURE / HISTORICAL FIXTURE PASS

The historical native-client, runtime-shaped fixture matrix passed correctness and
the scenario-declared deadline on its earlier inputs. This does not establish live
telemetry/runtime integration or model support. Historical pinned-artifact
claims below are not requirements for this evaluation; provenance gaps remain
as recorded. Offline results are recorded in the [offline acceptance
report](bounded-decision-offline-acceptance.md).

### Corrected four-agent runtime check (2026-10-29)

The corrected `ExperimentRuntime` was tested against the existing port-8080
native service with constructed healthy per-switch observations and the new
complete local port/flow expectations. Only the Substrate was substituted.
The compiled four agents ran on their actual one-second cadence and 5-second
deadlines, with original scheduled ticks, coalescing, and normal coordination.
No controlling chat model or network action was used.

**Result: failed under these deployment conditions.** All four agents produced
records, but only two invocations completed native inference: s1 and s3 both
correctly selected `healthy`, with zero reported output tokens (1,360 and 863
input tokens). The inference/server-queue phases were 2.359 s and 3.736 s;
original-tick-to-choice durations were 2.362 s and 3.739 s. Four other invocations
(s2, s4, and later cadence invocations for s1/s3) timed out at their declared
deadlines. All six samples/failures are retained, not just the two successes.
These timeouts are failures to complete the example's operational workload, not
wrong diagnosis choices or a universal model-latency quality verdict. No cold
measurement, degraded matrix, or OVS telemetry trial was attempted in this run.

The service had one slot, context 8192, model alias `ggml-org/Kev-4B-GGUF`,
build `b11429-d812350493`. `/slots` was idle before the run. Startup interface
probes completed before cadence began, so this is startup-probed/warm rather
than cold inference. Model/hash values are declared configuration, not a fresh
full-artifact audit; backend, hardware and launch provenance remain unavailable.
The service was not restarted, reconfigured or silently substituted.

Evidence: `/tmp/opencode/diagnosis-runtime-native-rdx1sztf/`, including the
compiled source, `healthy/plan.json`, `invocations.jsonl`, `ledger.sqlite3`,
`runtime-report.json` and `summary.json`. Fake Substrate cleanup verifies only
its in-memory model, **not host/network cleanup**. `/slots` still reported active
work immediately after local drain, demonstrating why local timeout is not
remote termination. A later independent read at `2026-10-29T19:44:30.051250Z`
showed idle; retained as `remote-status-after.json`. This establishes no active
slot at that observation, not a per-request termination cause. Post-run service
properties are retained separately. No inference was retried.

A portable copy of the complete raw evidence is committed under
[`evidence/`](evidence/README.md), with an archive SHA-256; this report does not
depend solely on the temporary path remaining available.

Before the privileged network matrix, resolve/review service capacity and the
declared deployment workload. Do not silently enlarge the example's deadline,
drop failed samples, change model/build, or count prior direct-client results
as a pass for these changed inputs.

## Runtime/native follow-up (2026-10-29)

After fixing the diagnosis adapter's empty action bundle, the compiled
`ExperimentRuntime` was exercised against the live service on port 8080 using
the observed model alias and a temporary configuration. Fifteen bounded
invocations completed successfully; intervening one-second ticks were recorded
as coalesced while the one-slot server was busy. The run evidence is retained
at `/tmp/opencode/diamond-diagnosis-run/runtime-evidence.json`.

This run used the `FakeSubstrateRuntime`, so it verifies runtime-to-native
transport wiring and retained decision records, not real OVS telemetry. The
12-case native matrix above remains the constructed-fixture correctness result.
The later direct-client Mininet/OVS telemetry run is recorded in the
live-network report; deployed-runtime diagnosis with real telemetry remains
unverified.

## Historical pinned attempt and observed provenance

| Item | Required / observed value | Status |
| --- | --- | --- |
| Repository | `ggml-org/Kev-4B-GGUF` | Pinned; server does not expose repository provenance |
| Revision | `f4624b6bb7072f1bab726a622c1cad3f96030b9e` | Pinned; server does not expose revision provenance |
| Artifact | `Kev-4B-Q8_0.gguf` | Exact basename and Q8_0 type observed in `/props` |
| Artifact size | `4,483,801,504` bytes | Verified |
| Artifact SHA-256 | `7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da` | Verified locally |
| llama.cpp | `620d2225cc436ab0b001e3200e590e3b286ef9ab` | Checked out and built |
| Server build | `0.7.0-dev`, build `11669`, commit `620d2225c` | Observed from binary and `/props` |
| Backend | CPU-only, GNU 16.2.1, Linux x86_64 | Build configuration and binary output observed |
| Launch | `127.0.0.1:8081`, 8 CPU threads, one slot, context 4096, GPU layers 0 | Recorded |
| Effective batch | 512 (`n_batch` and `n_ubatch` set by decision-model initialization) | Server log observed |
| Model type | `kev` | Server log observed |
| License audit | Not completed | Unverified |

The user's port-8080 service reported Q8_0 at the pinned revision path and its
served file hash matched the expected SHA-256. Its `/props` build was
`b11429-d812350493`, not the pinned build. The user explicitly accepted ignoring
the build-commit requirement for a diagnostic matrix attempt. The result is
therefore recorded with this build deviation and is not represented as a run on
the pinned llama.cpp build. The pinned server separately ran on port 8081.

## Startup/interface inference result

The real client sent one non-mutating `/v1/systemone` request from
`verify_startup()` to port 8081. The exact retained request is in
`/home/pedro/.cache/mininet-ai/kev-evaluation/startup-evidence.json`. It used
`state: "{}"`, the scenario's exact two choice IDs and descriptions, and empty
proposal bundles for the startup check.

| Measurement | Result |
| --- | --- |
| Client deadline | 2.0 seconds |
| Client elapsed | 2.0051 seconds |
| Client result | `TimeoutError: HTTP request timed out` |
| Wrapped error | `agent.bounded-decision.startup-interface-invalid` |
| Native response / choice / probabilities | None received |
| Server log | Task 0 started; at about 2.0 s `cancel task`; at about 2.4 s slot release logged with `n_tokens=104` |
| Remote terminal status | Server log records task cancellation and slot release; no decision response was returned |
| Retry | Not made |

The logged token count is a server diagnostic, not evidence of generated output
tokens or a completed decision. No output-token or decision correctness claim
can be made from this timed-out request.

## Diagnosis

The failure occurred during inference, not startup connectivity or identity
checking: `/health` and `/props` succeeded, `/props` matched the expected alias,
artifact basename, and Q8_0 type, and the server accepted task 0 on
`/v1/systemone`. The client then hit its configured 2-second read deadline;
`HttpxJsonTransport` translated the HTTPX read timeout to `TimeoutError`, and
`verify_startup()` wrapped it as `startup-interface-invalid`. No native response
reached the response parser, so the earlier response-shape issue is not implicated
in this failure.

The leading explanation for the port-8081 timeout is insufficient inference throughput for this deadline
in the selected environment: this exact build was CPU-only (`GGML_CUDA=OFF`,
`--n-gpu-layers 0`, 8 threads), while the RTX 2050 had about 2.94 GiB of its 4 GiB
VRAM occupied by other work at diagnosis time. The server log's cancellation at
the client deadline is consistent with an inference that had not completed.
However, one timed-out cold request does not distinguish CPU compute cost from
cold weight paging or prove how long the request would otherwise have taken.
There is no evidence of a malformed request, identity mismatch, or response
parser failure. A native completion time and the 800 ms matrix budget remain
unmeasured; the latter is stricter than the startup probe's 2-second timeout.

The first labelled case (`healthy`, expected `no_action`) was sent to port 8080
with the exact oracle-derived state and the same two-choice mapping. It first
returned HTTP **400** after **0.0769 s**. The rejected request was 12,361 bytes;
its state string was 10,120 characters. A controlled replay of this definitively
rejected request captured the server error body:

```json
{"error":{"code":400,"message":"request (4748 tokens) exceeds the available context size (4096 tokens), try increasing it","type":"exceed_context_size_error","n_prompt_tokens":4748,"n_ctx":4096}}
```

Thus the confirmed cause of the initial 400 is context overflow: this verbose
structured state and the decision instructions tokenize to 4,748 prompt tokens,
exceeding the server's then-configured 4,096-token context. The user's short
natural-language state fits; the request envelope and choice criteria mapping
were not the cause. The first error record remains at
`live-matrix-8080.jsonl`; the response body and replay request are retained in
`/home/pedro/.cache/mininet-ai/kev-evaluation/http400-response-replay.json`.

After the server's reported default per-slot context changed to 8,192, the
user explicitly requested one exact replay of the original request. The client
timed out at **0.8076 s** (the 800 ms deadline); no response or choice was
received. The user then supplied server logs for the request: cancellation
occurred about 1.000 s after slot launch, followed by slot release about 0.425 s
later. This confirms the server stopped the request; it did not produce a
decision. The matching llama.cpp source log statement records
`prompt.n_tokens()` at release, so `n_tokens=512` is not a generated-token
count or proof the full prompt was evaluated; `truncated=0` does not indicate
successful completion. The logs do not establish whether the request passed the
larger context check before it was cancelled. Exact request and timeout evidence
are in
`/home/pedro/.cache/mininet-ai/kev-evaluation/original-request-retry-8080.json`.

At the user's request, a distinct short-prompt diagnostic was sent after the
server reported an 8,192-token per-slot context. The state was
`Diagnostic smoke test: no network failure evidence is supplied. Preserve the
current policy.` It returned HTTP 200 and `no_action` in **0.3633 s**, within
800 ms; the response reported `no_action=0.972874`,
`install_recovery=0.027126`, and confidence `0.945748`. No action was executed.
This confirms the native endpoint works for a short prompt, but it is not a
labelled oracle case and does not validate the 4,748-token state or establish
decision-matrix correctness. Exact request/response evidence is at
`/home/pedro/.cache/mininet-ai/kev-evaluation/simple-prompt-check-8080.json`.

There is a separate error-reporting defect: `HttpxJsonTransport` raises on
non-success status before reading the response body, and `NativeSystemOneClient`
maps this HTTP 400 to `service-unavailable`. That classification hides both the
actual status and the useful context-limit diagnostic.

## Historical recovery matrix and measurements (superseded scope)

The counts below describe only the original recovery attempts, not the later
12-case diagnosis matrix or 16-case direct-client live-telemetry run. Recovery
acceptance and the universal 800 ms gate were superseded by #116's amendments.

- Labelled oracle cases: 12 planned; first case attempted once and rejected
  with HTTP 400, then replayed once after context increase and timed out; 11
  cases not run.
- Completed decision samples: **0**.
- Separate non-oracle smoke check: **1 completed** (`no_action`, 0.3633 s,
  within 800 ms); excluded from matrix counts and correctness acceptance.
- Correctness, probabilities, generated-output usage, and one-second scheduled
  load: **unmeasured**.
- 800 ms decision deadline: **not evaluated**; the only inference was the
  separate 2-second startup probe.
- Context-limit behavior: **observed** at 4,748 prompt tokens versus a 4,096
  context; option limits, malformed/unavailable service behavior, and
  late-response discard remain **unmeasured**.
- Context 8,192 replay: server cancellation and slot release observed after
  the 800 ms client timeout; no response was captured.
- Live network recovery and recovery time: **not run**; out of scope for the
  amended initial diagnosis slice.

The port-8081 startup request/timeout diagnostic is retained in
`/home/pedro/.cache/mininet-ai/kev-evaluation/startup-evidence.json`. The
port-8080 preflight and first case are retained in
`/home/pedro/.cache/mininet-ai/kev-evaluation/preflight-8080.json` and
`/home/pedro/.cache/mininet-ai/kev-evaluation/live-matrix-8080.jsonl`.
These local cache files are evaluation evidence, not committed repository
artifacts. The port-8081 server log is
`/home/pedro/.cache/mininet-ai/kev-evaluation/server.log`.

## Current remaining conditions

The runtime-shaped constructed-fixture matrix is complete (12/12), as recorded
above. A subsequent direct-client live-telemetry matrix recorded 16/16 matches
against port-state labels and a separate cold first-inference sample; see the
[live-network report](bounded-decision-live-network-evaluation.md). These do
not establish the deployed four-agent runtime with real Substrate telemetry.
The final review identified missing operational port-state inputs and an
undefined expected-flow oracle. Code now fixes those inputs and routes a new
runner through the real four-agent continuous runtime. The changed runtime
matrix needs a privileged live rerun; it is not a requirement to repeat the
superseded recovery matrix. Startup native inference now uses the scenario's
declared deadline, with a distinct timeout outcome; it no longer imposes a
separate universal 2-second inference cutoff.

For further evaluation, retain exact runtime requests/responses, labels,
provenance gaps, scenario-declared deadline outcomes, and separate cold/warm
conditions. Do not retry a timeout until remote termination/release is
independently confirmed. There is no universal model-latency pass threshold.

The historical HTTP error-observability defect is also fixed: shared GET/POST
decoding retains bounded status/body diagnostics (including base64 for exact
malformed bytes), and native 4xx request rejection is distinct from service
unavailability. Invalid native choices/diagnostics/output usage retain their
received response in failed Run Evidence. Earlier reports are preserved, not
rewritten as successful evaluations.

No confidence gate or model-support promotion is authorized by this report.
