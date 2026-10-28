# Bounded-decision live candidate evaluation

## Verdict: UNVERIFIED / BLOCKED

The pinned Kev-4B candidate was **not run** in this environment. No native
inference requests, decision-quality samples, latency measurements, or remote
termination observations were collected. This is an environment blocker, not a
model pass or fail, and it does not satisfy live-model acceptance or authorize a
supported-model claim. The offline results are recorded separately in the
[offline acceptance report](bounded-decision-offline-acceptance.md).

## Required candidate and server

The evaluation target remains exactly:

| Item | Required value | Evaluation status |
| --- | --- | --- |
| Repository | `ggml-org/Kev-4B-GGUF` | Pinned, not downloaded |
| Revision | `f4624b6bb7072f1bab726a622c1cad3f96030b9e` | Pinned, not checked out |
| Artifact | `Kev-4B-Q8_0.gguf` | Not downloaded; full SHA-256 not verified |
| Expected SHA-256 | `7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da` | Not measured |
| llama.cpp | v0.7.0, commit `620d2225cc436ab0b001e3200e590e3b286ef9ab` | Not built or run |

No model or server substitution was made. The installed `/usr/bin/llama-server`
reported `0.6.0-dev`, build `11429`, commit `d812350493`; it is not the pinned
server and was not used for inference. Public model metadata lists the Q8_0
artifact at approximately 4.48 GB; that metadata is not a downloaded-artifact
integrity check.

## Environment observations on 2026-10-28

These are point-in-time observations, not evaluation measurements:

- Host: Linux x86_64, 12 logical CPUs, 15 GiB RAM.
- At inspection, available RAM was about 2.5 GiB; the 4 GiB swap was fully
  occupied. The required artifact alone is approximately 4.48 GB, before runtime
  and context memory.
- GPU: NVIDIA GeForce RTX 2050, 4 GiB total. `nvidia-smi` reported about
  3,070 MiB already allocated and 99% utilization by an unrelated foreground
  process. That process was not stopped or disturbed.
- No matching `Kev-4B-Q8_0.gguf` file was found under `/home/pedro` during the
  local search.

The installed server version, unavailable model artifact, insufficient free host
memory, and occupied low-memory GPU make a trustworthy pinned evaluation
unavailable in this session. Building another server, substituting a model, or
interrupting the unrelated workload would not satisfy the ticket's evaluation
contract.

## Unperformed acceptance work

No live endpoint readiness or `/props` identity check was made. In particular,
there is no observed artifact hash, served identity, build/backend, launch
configuration, context/batch limit, or applicable artifact-license audit. No
`POST /v1/systemone` call was sent. Therefore there are no exact native inputs,
responses, probabilities, usage fields, generated-token observations, failure
samples, or request correlations to replay.

The following remain **unmeasured**, not passed:

- The labelled healthy, startup, transient, sustained (both primary links),
  stale/conflicting/incomplete, recovery-probe-failure, and post-recovery matrix.
- Choice correctness, diagnostic probabilities, zero generated output tokens,
  one-second scheduled-load behavior, 800 ms end-to-end latency, queueing,
  context/option limits, and unavailable/malformed service behavior.
- Local timeout/cancellation behavior against this exact server and any remote
  terminal status. Local cancellation or a closed connection would not by itself
  prove remote inference termination.

Sample count: **0 live inference samples**. Decision latency and network recovery
time: **not measured**. Remote request status: **not observed / unknown**.

## Conditions to resume

Run on a host with sufficient free system memory and an available supported GPU
or a separately budgeted CPU evaluation environment. Do not proceed while the
GPU or remote service has unresolved work from another trial. Then:

1. Fetch only the pinned repository revision and exact artifact; verify the
   complete artifact SHA-256 before launch and record the verification source.
2. Build llama.cpp at the exact pinned commit and record build/backend, hardware,
   launch flags, context/batch limits, and all observable identity fields.
3. Exercise the real native client and exact two-choice `POST /v1/systemone`
   contract with correlated retained inputs/outputs. Report every sample and
   failure; do not infer correctness from scripted transports.
4. Measure one-second scheduled load and the 800 ms budget separately from any
   later live-network recovery evaluation. Keep diagnostics non-authoritative.
5. Record remote terminal status only with independent authoritative evidence;
   otherwise mark it unknown and do not claim isolated repeated trials.

No candidate substitution, relaxed deadline, confidence gate, or support
promotion is authorized by this report.
