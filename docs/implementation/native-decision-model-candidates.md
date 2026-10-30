# Native decision-model candidates for #124

> **Subsequent evaluation:** The operator approved H2O-Lightning-4B Q8_0 and
> deployed it separately. Its native interface and eight measured deadlines
> passed, but s2 missed an expected-flow absence (7/8 correct); the matrix stopped
> at that failure. See the [evaluation report](bounded-decision-h2o-evaluation.md).
> The recommendation below is the historical selection rationale, not evidence
> of accepted diagnosis correctness.

Research date: 2026-10-30. **Research only; no model inference, weight downloads,
remote endpoint access, server changes, commits, or issue edits.**

## Finding

**Distinct open-weight alternatives exist with source-level support in the
reported deployed llama.cpp commit.** This is an interface/architecture finding,
not a claim that any candidate correctly diagnoses this network, fits the remote
hardware, or meets the four-agent timing requirement. **Recommended first
candidate: native H2O-Lightning-4B Q8_0**, a different trained 4B model with an
explicit native artifact, Apache-2.0 declaration and publisher hash manifest.
This is a recommendation for evaluation, not a prediction of better diagnosis.
Alternatives include **Lev**, subject to its training-data license caveat;
**Kev-9B** for a different trained checkpoint in the same family; and
**Laya/Julia-1** for a substantially different encoder architecture, subject to
input-budget checks. No candidate is selected or authorized for deployment here.
[S1][S2][S3][S4][S5]

The existing evaluated identity is `ggml-org/Kev-4B-GGUF`, revision
`f4624b6bb7072f1bab726a622c1cad3f96030b9e`, artifact `Kev-4B-Q8_0.gguf`.
Its expected Hub SHA-256 is
`7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da`;
Hub size is 4,483,801,504 bytes. Remote hashing remains unverified. The
[remote evaluation](bounded-decision-remote-native-evaluation.md) records healthy
answers for missing expected flow; the [prompt trials](bounded-decision-prompt-tuning.md)
corrected that case but regressed down-port diagnosis. Those failures remain
failures. More time or another quantization is not established to repair them.
[S6]

Scope remains text-only current local ports/flows, exact `healthy` / `degraded`
choices, four concurrent agents, one-second cadence and five-second operational
deadline. The operator subsequently supplied a remote hardware snapshot: NVIDIA
GeForce RTX 3090, 24,576 MiB total VRAM and 17,790 MiB free (about 17.4 GiB);
system RAM 125 GiB total / 114 GiB available, no swap. These are **operator-supplied
facts, not independently verified measurements**. The earlier local RTX 2050's
4 GiB is not a remote sizing constraint. See the
[bounded-decision contract](../bounded-decision.md).
Existing reports live in `docs/implementation/`; no `docs/research/` directory
existed when inspected, so this report follows that convention.

## What compatibility means here

The reported build is `b11941-19ff817fb`; GitHub resolves its source commit to
`19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4` (2026-10-27). At that exact commit,
`server-decision.cpp` initializes `kev`, `laya`, `lev`, `clef`, `openjev`,
`vllm-d3`, and `h2o-lightning` native decision types. It requires model decision
metadata and a named `systemone` template. Merely loading a generic GGUF with the
same backbone does **not** establish native decision support. Although templates
are stored using llama.cpp's template machinery, these are native decision
paths, not an external chat-to-decision wrapper. [S1][S7]

Architecture checks at that commit:

- **Kev:** converter merges a decision LoRA into Qwen3.5 and includes pointer-head
  weights and temperature; native setup reads option-ending marker hidden states.
  The 0.8B and 9B releases use different backbones and separately trained
  adapters/heads, not alternate quantizations of Kev-4B. [S1][S2][S8][S9]
- **Laya and Julia-1:** `conversion/bert.py` explicitly recognizes both publishers'
  checkpoints, includes their decision-head layers, and writes `LAYA` decision
  metadata. Native setup requires mask/separator tokens and `max_head_tokens`.
  Thus Julia's shared runtime type does not mean Julia and Laya share trained
  weights. Laya uses ModernBERT-large; Julia uses mmBERT-small. [S1][S3][S10][S11]
- **Lev:** Qwen3.5-4B decision LoRA, native label-token scoring; the server also
  implements choice-order averaging. It is separately trained from Kev despite
  a similar backbone size. Do not assume every publisher Python readout mode
  (e.g. its large-option candidate head) is provided by the native path. Two
  choices fit the native label mechanism. [S1][S2][S4]
- **Clef/Clef-Flash:** dedicated Clef converter and model graph with joint schema
  head; native joint-prompt inference and sorted choice mapping. Their respective
  publisher backbones are Qwen3.8-27B and Qwen3.5-9B. [S1][S12][S13][S14]
- **Decision 3.0 nano:** converter explicitly names `vllm-sr/d3-nano`, checks
  `noncausal_full_attention` and `last` pooling, and writes `VLLM_D3` metadata;
  it is not a stock Qwen chat model substituted behind an API wrapper. [S1][S15][S16]
- **H2O-Lightning:** dedicated Qwen3.5 converter validates `serve_config.json`,
  writes H2O decision metadata, labels, prompt and calibration. Native serving
  reads label logits with the publisher's space-prefixed labels. Publisher says
  native files require build 11773 / `7e3eeee04` or later; support is also
  directly present in the reported build's source. [S1][S5][S17]

These checks do not inspect or attest the actual remote executable, downloaded
GGUF metadata/tensors, GPU backend, or artifact hashes. Especially for artifacts
published after the binary, metadata/tokenizer differences still need a real
operator-approved load/interface check. Source compatibility is not deployment
verification.

## Pinned artifact inventory

All sizes below are **exact file bytes**, not RAM/VRAM requirements. Revisions
and sizes were read from public Hugging Face model APIs (`?blobs=true`); every
listed repository reported `gated: false`. A selected artifact below is a
reproducible research reference, not an accuracy recommendation. Except H2O's
publisher-owned GGUF repository, the converter/publisher is **ggml-org**, whose
cards link the upstream trained-model publishers and state that conversion is
automated with `ggml-org/convert`. Hub provenance is not a local hash check.
[A1–A10]

| Distinct trained release; original publisher | GGUF repository | Exact repository revision | Reference artifact | Bytes | Declared weight license |
| --- | --- | --- | --- | ---: | --- |
| Kev-0.8B; Jared Palmer | `ggml-org/Kev-0.8B-GGUF` | `9231cb757e3320120077e42e52371971968f008d` | `Kev-0.8B-Q8_0.gguf` | 812,406,304 | Apache-2.0 |
| Kev-9B; Jared Palmer | `ggml-org/Kev-9B-GGUF` | `6927aa248a75f163aa8fc46260f7cad718d1565b` | `Kev-9B-Q8_0.gguf` | 9,529,735,648 | Apache-2.0 |
| Laya; ConvAI Innovations | `ggml-org/Laya-GGUF` | `845f314dd047fc2444a5289a4d50c1e1d59e27e1` | `Laya-Q8_0.gguf` | 449,397,600 | Apache-2.0 |
| Julia-1; SupersonicLabs | `ggml-org/Julia-1-GGUF` | `1e6b48a6983d7467f8eedc2cb0d21267b5fc052d` | `Julia-1-Q8_0.gguf` | 168,166,496 | Apache-2.0 |
| Lev; Interfaze AI | `ggml-org/lev-GGUF` | `2ef3b3c3ab3defcab1058ab34cac52c467fa9304` | `lev-Q8_0.gguf` | 4,482,405,312 | Apache-2.0; see training-data caveat |
| Clef-Flash; Cloudflare | `ggml-org/Clef-Flash-GGUF` | `6e2a164d70aae2af7fce86005da3f91805565b1d` | `Clef-Flash-BF16.gguf` | 18,164,488,448 | Apache-2.0 |
| Clef; Cloudflare | `ggml-org/Clef-GGUF` | `d7492270abd7af222e98dca986580772e6229242` | `Clef-BF16.gguf` | 54,064,664,736 | Apache-2.0 |
| Decision 3.0 nano; vllm-sr | `ggml-org/vllm-d3-nano-GGUF` | `50dea2057df10c4b393559a155ab3bf0bc7532c6` | `vllm-d3-nano-Q8_0.gguf` | 2,552,358,464 | Apache-2.0 |
| H2O-Lightning-4B; H2O.ai | `h2oai/h2o-lightning-4b-gguf` | `aba20a244eb1038b8546cd25c2d6046f0c3e4256` | `h2o-lightning-4b-Q8_0.gguf` | 4,482,407,456 | Apache-2.0 |
| OpenJev; openjev | `ggml-org/OpenJev-GGUF` | `d2c376d11b6c466ab7bdc9fa458ace41cdf3e776` | `OpenJev-Q8_0.gguf` | 28,595,765,408 | CC-BY-NC-4.0, non-commercial only |

H2O's pinned `artifact_manifest_v5.json` additionally identifies its source model
as `h2oai/h2o-lightning-4b@cc18ee330e6895c41b9a60f37a0dd122dc4c3461`, and the
Q8_0 artifact SHA-256 as
`4127b2ae7710c3864c21c3d946c316f28a654a53d3a4ba99f383bbcaf2768e81`.
For the ggml-org conversions, upstream model-card revisions cited below are
document snapshots, **not verified exact conversion input commits**; that lineage
must be checked before claiming a particular upstream training release. [S18]

### Sizing against the operator-supplied snapshot

The following is arithmetic on artifact bytes and the supplied memory snapshot,
not measured runtime residency, fit verification or latency evidence:

| Reference artifact group | Approximate weight-file GiB | Sizing implication |
| --- | ---: | --- |
| Julia-1 / Laya / Kev-0.8B Q8_0 | 0.16 / 0.42 / 0.76 | Small file footprint; request/head budgets and correctness still gate suitability. |
| d3-nano Q8_0 | 2.38 | Plausible weight headroom on this GPU; noncausal processing and runtime allocations remain unmeasured. |
| Lev / H2O-Lightning-4B Q8_0 | 4.17 each | Plausible weight headroom; neither file size nor Kev-4B's prior timings establishes four-agent latency for these models. |
| Kev-9B Q8_0 | 8.88 | More weight headroom than either BF16 Clef reference, but context/state/buffer allocation still needs confirmation. |
| Clef-Flash BF16 | 16.92 | Almost consumes the reported 17.37 GiB free before runtime state/buffers; do not assume full-GPU fit. |
| OpenJev Q8_0 / Clef BF16 | 26.63 / 50.35 | Exceed even total 24 GiB GPU capacity as file footprints; would require a different artifact or offload/multi-device plan, not a presumed full-GPU deployment. |

Available host RAM makes host placement/offload a possible operator investigation,
not a promise of deadline compliance. No swap does not provide additional capacity.
The free-VRAM snapshot may include the currently loaded service or unrelated
allocations; its accounting and behavior after a service change are unknown.
Do not add current model size to free VRAM or treat all free memory as reserved
for new weights. The publisher-reported Blackwell-specific IQ3 warning below
does not describe the supplied RTX 3090, but the actual driver/toolkit/backend
and any other issues still require confirmation. [S5]

## Deployment caveats and exclusions

1. **Do not equate smaller with better or faster enough.** Kev-0.8B is genuinely
   different, but no evidence here shows it repairs logical inventory checks.
   Kev-9B is larger, not a guaranteed improvement. Publisher benchmarks are not
   the current local-port/flow task. No latency numbers from cards are promoted
   into a four-concurrent-agent, one-second-cadence, five-second-deadline claim.

2. **Encoder budgets are a serious gate.** Laya's root English model card gives
   512 tokens per question, including a 192-token head budget. Julia's current
   card describes an 8,192 combined-token runtime limit and strict overflow
   rejection, but historical task accuracy used 1,024 tokens; its 8k smoke test
   does not establish 8k accuracy. The pinned GGUF's actual context/head budgets
   must be inspected; newer Python runtime limits cannot be assumed to apply.
   No automatic truncation, state selection, multilingual routing, or fallback
   is authorized for the mininet-ai request. [S10][S11]

   **Native H2O also has an automatic state cut**, not just its Python shim:
   the inspected server's `h2o_cut_state()` keeps head/tail text above 32,000
   state tokens. A canonical UTF-8 state at most 32,000 bytes cannot reach that
   cut (the source explicitly uses that sufficient byte-bound check), but the
   complete question/template must separately fit the per-slot context. Inspect
   every retained request and reject silent truncation; native endpoint support
   alone does not establish preservation of the input. [S1]

3. **Use native H2O files, not `stock-` files.** The publisher distinguishes
   identical trained tensors with different metadata: `stock-` requires a
   Python shim and alone gives ordinary chat, whereas the listed non-stock
   artifact serves native `/v1/systemone`. Its native path reads one-way;
   publisher two-way vLLM/shim benchmark results are not native llama.cpp
   results. Avoid importing the publisher's optional re-reading profile,
   long-record selection or other shim behavior. The card reports Blackwell
   CUDA 13.2.0/13.2.1 faults for IQ3-family files; the Q8_0 reference is not one
   of those affected files. Remote GPU/toolkit still need confirmation. [S5]

4. **Clef quantization needs caution.** Upstream issue #30064 reports badly
   distorted Q8_0 decision probabilities compared with BF16 and reports that
   retaining decision-head BF16 fixes the author's probes. This is a reported
   artifact/quantization sensitivity, not our measurement or a general
   correctness proof. BF16 is listed conservatively; no claim is made that the
   current Q8_0 revision is broken or fixed without checking its lineage and
   head tensor types. Do not generalize the report to Clef-Flash without
   evidence. Both BF16 references require substantial runtime memory. [S19]

5. **Text-only means no projector is needed for this workload.** Several cards
   advertise multimodal support; llama.cpp's `-hf` normally auto-downloads
   projectors when available. An operator can explicitly disable that behavior
   with `--no-mmproj` when preparing a text-only deployment. No command was run
   here, and no automatic artifact/quantization choice is recommended: the
   documented default `-hf` quant choice is not necessarily the artifact above.
   Pin repository revision, exact filename, and hash. [S20]

6. **Licensing is not uniformly permissive.** OpenJev is open-weight but
   non-commercial, not a permissively licensed replacement. Lev declares
   Apache-2.0 for the adapter/base but explicitly warns that some training
   datasets have non-commercial terms; obtain operator/legal confirmation for
   intended use. Preserve notices and check applicable upstream terms for every
   selected artifact. [S4][S21]

7. **Same-weight alternatives are not new reasoning capability.** The existing
   Kev-4B repository also exposes BF16 (8,427,020,704 bytes) and Q4_K_M
   (3,033,489,824 bytes). Those are numerical-format comparisons of the same
   trained release, not distinct trained candidates. Other Kev-4B conversions
   likewise need evidence of a new training checkpoint before counting as new
   models. Different quantizations can change answers, but this does not imply
   improved reasoning. [S6]

8. **Out of scope/not shortlisted:** hosted TypeSafe Jev availability is not
   assumed. Chat/logprob wrappers such as `llamacpp-jev` are excluded. Tiny
   random/testing models are not diagnosis candidates. The inspected source's
   MiniCPM-OCR-Jev path requires two images and fixed binary consistency semantics,
   so its endpoint name alone does not make it fit text-only port/flow diagnosis.
   Other decision families exist in upstream inventory; this is a verified
   shortlist, not an exhaustive survey of every checkpoint or fine-tune. [S1]

## Operator confirmations before any separate evaluation

- Explicitly choose a distinct trained model and exact pinned artifact; approve
  licensing and any download/service replacement. Do not silently fall back or
  automatically switch the running service.
- Independently confirm the supplied GPU/VRAM/RAM snapshot and record
  driver/toolkit, CPU, backend/build,
  offload placement, full launch configuration, context/batch limits and slots.
  Allow headroom beyond file size for model state, buffers and four requests.
- Verify downloaded SHA-256 and artifact metadata, upstream conversion lineage,
  native decision type/template, tokenizer behavior and text-only setup. Verify
  actual remote executable provenance rather than relying only on version text.
- Confirm an unchanged canonical mininet-ai request fits without truncation and
  maps `questions.next_action` to exact `healthy` / `degraded` choice IDs. Shared
  runtime types do not establish output parity with publisher Python servers.
- Only after separate authorization, run the complete declared constructed-case
  matrix under real four-agent scheduling, retaining wrong choices, unknowns,
  timing and server provenance. Include healthy, down ports, missing first/last
  flow, equal-count wrong/duplicate inventories, wrong actions/cookies, reordered
  healthy flows and unavailable observations. Do not tune away individual failed
  cases or change cadence/deadline to claim correctness.
- Keep confidence diagnostic-only, failed Kev evidence intact and #124 unresolved
  until acceptance is actually met; research cannot unblock #125's real-network
  gate. No inference or deployment validation was performed for this report.

## Primary sources

Source-code links pin the deployed commit; model-card links pin inspected
documentation where available. API links are metadata retrieval endpoints and
are mutable; the table records their returned immutable revision identifiers.

- [S1] [Native decision initialization, parsing, rendering and responses](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/tools/server/server-decision.cpp), especially lines 40–220 and 243–417.
- [S2] [Lev/Kev conversion and LoRA merging](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/conversion/lev.py).
- [S3] [Laya/Julia ModernBERT conversion](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/conversion/bert.py#L655-L745).
- [S4] [Interfaze AI Lev card](https://huggingface.co/interfaze-ai/lev/blob/8721908e50dffd5fe8726507c09498e3def5b2f6/README.md).
- [S5] [H2O native versus stock GGUF, versions, quantization and engine caveats](https://huggingface.co/h2oai/h2o-lightning-4b-gguf/blob/aba20a244eb1038b8546cd25c2d6046f0c3e4256/README.md).
- [S6] [Existing Kev-4B Hub artifact metadata](https://huggingface.co/api/models/ggml-org/Kev-4B-GGUF?blobs=true), [pinned conversion card](https://huggingface.co/ggml-org/Kev-4B-GGUF/blob/f4624b6bb7072f1bab726a622c1cad3f96030b9e/README.md).
- [S7] [Exact commit identity/date](https://api.github.com/repos/ggml-org/llama.cpp/commits/19ff817fb), [native decision capabilities](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/tools/server/server-decision.h).
- [S8] [Kev-0.8B publisher card](https://huggingface.co/jaredpalmer/kev-0.8b/blob/bf75a6a8848ea6960ff2ed108d9ed44c2941174f/README.md).
- [S9] [Kev-9B publisher card](https://huggingface.co/jaredpalmer/kev-9b/blob/db029f08b290afd9fee4aa4bbcd9ae48602d1eb0/README.md).
- [S10] [Laya publisher card and distinct checkpoint budgets](https://huggingface.co/convaiinnovations/laya/blob/7b928d828b7b0e022f929d9bd2e44165aa270148/README.md).
- [S11] [Julia-1 publisher card and limitations](https://huggingface.co/SupersonicLabs/Julia-1/blob/a85b127321d580d65176c89ced8273f305745d85/README.md).
- [S12] [Clef converter](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/conversion/clef.py), [Clef graph](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/src/models/clef.cpp).
- [S13] [Cloudflare Clef card](https://huggingface.co/Cloudflare/clef/blob/cfddda043dd190ef3b01cff449d5ee8cff1e8ec6/README.md).
- [S14] [Cloudflare Clef-Flash card](https://huggingface.co/Cloudflare/clef-flash/blob/fb4417d31c1d0f9e7e77f1141b40aaede7491e00/README.md).
- [S15] [Decision 3.0 converter and attention contract](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/conversion/vllm_d3.py).
- [S16] [vllm-sr d3-nano publisher card](https://huggingface.co/vllm-sr/d3-nano/blob/462efc366229ea268cf4eed3738b3fc4415e4817/README.md).
- [S17] [H2O converter](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/conversion/h2o_lightning.py), [publisher native-support PR #30361](https://github.com/ggml-org/llama.cpp/pull/30361).
- [S18] [H2O pinned artifact/source manifest](https://huggingface.co/h2oai/h2o-lightning-4b-gguf/blob/aba20a244eb1038b8546cd25c2d6046f0c3e4256/artifact_manifest_v5.json).
- [S19] [Upstream Clef Q8_0 report #30064](https://github.com/ggml-org/llama.cpp/issues/30064) (reporter observations, not an independent measurement here).
- [S20] [Pinned server CLI documentation](https://github.com/ggml-org/llama.cpp/blob/19ff817fb25e9a1b838b1d7f9f4325f7bf9779c4/tools/server/README.md).
- [S21] [OpenJev publisher card/license](https://huggingface.co/openjev/openjev/blob/b4a89f5884f3482a5e894b2e7ff01db8ea033726/README.md).

Artifact APIs and pinned converter cards (the latter provide publisher lineage
and explicit native endpoint statements):

- [A1] [Kev-0.8B API](https://huggingface.co/api/models/ggml-org/Kev-0.8B-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/Kev-0.8B-GGUF/blob/9231cb757e3320120077e42e52371971968f008d/README.md).
- [A2] [Kev-9B API](https://huggingface.co/api/models/ggml-org/Kev-9B-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/Kev-9B-GGUF/blob/6927aa248a75f163aa8fc46260f7cad718d1565b/README.md).
- [A3] [Laya API](https://huggingface.co/api/models/ggml-org/Laya-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/Laya-GGUF/blob/845f314dd047fc2444a5289a4d50c1e1d59e27e1/README.md).
- [A4] [Julia-1 API](https://huggingface.co/api/models/ggml-org/Julia-1-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/Julia-1-GGUF/blob/1e6b48a6983d7467f8eedc2cb0d21267b5fc052d/README.md).
- [A5] [Lev API](https://huggingface.co/api/models/ggml-org/lev-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/lev-GGUF/blob/2ef3b3c3ab3defcab1058ab34cac52c467fa9304/README.md).
- [A6] [Clef-Flash API](https://huggingface.co/api/models/ggml-org/Clef-Flash-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/Clef-Flash-GGUF/blob/6e2a164d70aae2af7fce86005da3f91805565b1d/README.md).
- [A7] [Clef API](https://huggingface.co/api/models/ggml-org/Clef-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/Clef-GGUF/blob/d7492270abd7af222e98dca986580772e6229242/README.md).
- [A8] [d3-nano API](https://huggingface.co/api/models/ggml-org/vllm-d3-nano-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/vllm-d3-nano-GGUF/blob/50dea2057df10c4b393559a155ab3bf0bc7532c6/README.md).
- [A9] [H2O API](https://huggingface.co/api/models/h2oai/h2o-lightning-4b-gguf?blobs=true), [card](https://huggingface.co/h2oai/h2o-lightning-4b-gguf/blob/aba20a244eb1038b8546cd25c2d6046f0c3e4256/README.md).
- [A10] [OpenJev API](https://huggingface.co/api/models/ggml-org/OpenJev-GGUF?blobs=true), [card](https://huggingface.co/ggml-org/OpenJev-GGUF/blob/d2c376d11b6c466ab7bdc9fa458ace41cdf3e776/README.md).
