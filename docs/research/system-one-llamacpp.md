# Open-weights bounded decisions through llama.cpp

Research resolution for [#107: Establish open-weights decision-model feasibility through llama.cpp](https://github.com/santiagopedro1/mininet-ai/issues/107), child of [#106](https://github.com/santiagopedro1/mininet-ai/issues/106). Investigated 2026-10-24. Research only: no implementation, deployment, architecture selection, or Jev compatibility commitment.

## Answer

**Yes: a source- and artifact-verified model/serving contract exists.** Kev-4B and Laya have native non-generative decision support in upstream llama.cpp, not merely chat completions with thinking disabled. PR #29818 merged on 2026-10-02 as `a4cb4c61fd9d9c2066c7c1747821d3d65b8943bd`; semantic release **v0.6.0** includes it. This investigation checked the **v0.7.0** source, commit `620d2225cc436ab0b001e3200e590e3b286ef9ab`, released October 15. No private patch or external scorer is necessary for Kev/Laya. [S1–S4]

**Recommendation for initial evaluation, not a model selection:** pinned `ggml-org/Kev-4B-GGUF` Q8_0 through `llama-server` v0.7.0, text-only `POST /v1/systemone`, one `choice` question over predefined action IDs including explicit no-action. Kev's custom pointer head and calibration are preserved in GGUF. Its longer trained context is a better starting fit for telemetry plus policy than Laya's root checkpoint's 512-token training budget; this is an engineering hypothesis, not evidence of network-decision accuracy. Final candidate, thresholds, execution boundary, and failure policy remain human planning decisions. [S3–S8]

**Evidence boundary:** verified official repositories, model cards/configs, Hub artifact inventories and small byte ranges of actual GGUF files. Did **not** download full weights, build/run llama.cpp, measure latency/RAM, establish numerical parity locally, or test a network scenario. This resolves feasibility research, not live-model acceptance.

## Evidence-backed comparison

| Candidate | Weights/license and architecture | llama.cpp decision path | Assessment |
| --- | --- | --- | --- |
| **Kev-4B** | Apache-2.0 declared for adapter/head; Qwen3.5-4B-Base is Apache-2.0. LoRA rank 16, alpha 32 on a frozen causal hybrid DeltaNet/full-attention backbone, plus learned query/key pointer projections. Publisher provides `adapter_model.safetensors`, `head.pt`, configs; ggml-org provides merged BF16/Q8_0/Q4_K_M GGUFs. [S5–S8, S14] | **Native.** Converter downloads pinned base, merges LoRA, emits pointer-head tensors, `decision.type=kev`, named `systemone` template and temperature. Server reads option-ending hidden states and final question state, computes scaled pointer dot products, then temperature-softmax over supplied options. Sampling/generation is bypassed. [S3, S4, S8, S9] | Strongest initial candidate here for evaluation; English only; actual network quality and calibration unknown. Not chosen for production. |
| **Laya, root English checkpoint** | Apache-2.0 declared by publisher and GGUF card. ModernBERT-large encoder, fully fine-tuned, plus two transformer head layers and option-marker scorer; 421M total per card. Separate multilingual and typed-decisions checkpoints must not be conflated with root. [S10–S12] | **Native.** ModernBERT decision converter appends head blocks, exports temperatures and head-token budget, scores `[MASK]` option markers. Whole non-causal prompt needs one physical microbatch. No external scorer. [S3, S4, S9, S12] | Small native baseline, but root has 512-token budget and weak zero-shot typed-decision results. Fine-tuning is outside this map's scope. Do not attribute fine-tuned checkpoint's 0.766 accuracy to root. |
| **OneJev-4B** (not OpenJev) | Apache-2.0 card; full fine-tune of Qwen3.5-4B; config remains `Qwen3_5ForConditionalGeneration`, with hybrid text and vision backbone. Full safetensors available; author links community `mradermacher/OneJev-4B-GGUF`. [S13–S15] | **Existing first-party adaptation, not verified native `/v1/systemone` support.** `qev.llamacpp.LlamaCppEngine` renders frozen decision prompts, verifies tokenizer/slot IDs, prefills via `/completion` with `n_predict:0`, then asks for **one token** with `n_probs`. It extracts label-slot log probabilities, falls back to biased slot-only probabilities when top-k omits a slot, and delegates calibrated answer assembly to qev. [S16–S18] | Feasible llama.cpp-backed scoring adapter exists upstream, but adds external scorer/process/runtime and uses a one-token completion internally. Under a strict zero-generated-token requirement it is not a match. Not a generic chat-controller proposal and not proven equivalent to native Kev. No exact minimum llama.cpp version pinned by the inspected adapter/card. |

**Naming correction:** OneJev (`OmniJev/OneJev-*`) and OpenJev (`openjev/openjev`) are different projects. The upstream llama.cpp support list's **OpenJev** is not evidence of native **OneJev** support. The ggml-org announcement declares OpenJev CC BY-NC 4.0, unlike OneJev's Apache-2.0; no need to introduce its non-commercial restrictions or 27B/multimodal footprint when native Apache-licensed text candidates already meet feasibility. Hosted Jev and compatibility testing are excluded. [S1, S13, S16]

License verification here means first-party release declarations plus base license metadata/file availability, not legal clearance of training data or a license audit. Kev and Laya's inspected Hub inventories did not expose a standalone root LICENSE; preserve and review publisher declarations and all applicable notices before redistribution. OneJev's code repository includes an Apache-2.0 LICENSE. [S5, S10, S13, S14, S18]

## Native serving contract

Use a build including #29818; **v0.6.0 is the verified semantic-release floor**, while **v0.7.0 is the inspected evaluation baseline**. v0.6.0 also includes the Laya batch-limit abort fix #29903; v0.7.0 includes further server/cache fixes and exact GELU for ModernBERT. An old distro package or another product using llama.cpp internally does not establish endpoint support. [S1, S2, S9]

Illustrative launch, **not executed** (the Hub shorthand is convenient but does not pin a revision):

```sh
llama-server -hf ggml-org/Kev-4B-GGUF:Q8_0 \
  --host 127.0.0.1 --port 8080 -c 8192 -b 2048 -ub 2048 -np 1
```

For reproducibility, download the exact revision/file in the manifest below, verify SHA-256, and launch with `-m <local-file>` instead. Explicitly set port: v0.7.0 changed the standalone server default to 9931. Batch/context flags above are a proposed starting envelope, not validated sizing. Keep complete rendered prompt within context and all pointer-read positions within the final usable batch; increase physical/logical batch together when needed. [S2, S8, S9]

Illustrative text-only request (action meanings are examples, **not an authorized capability set or policy**):

```json
{
  "state": "Observed telemetry and the permitted-action policy, including units and timestamps.",
  "questions": {
    "next_action": {
      "type": "choice",
      "instructions": "Choose one listed action using only the telemetry and stated policy.",
      "criteria": {
        "no_action": "Leave network configuration unchanged.",
        "inspect_link": "Request the predefined read-only link inspection."
      }
    }
  }
}
```

Send as JSON to `http://127.0.0.1:8080/v1/systemone`. Native response contains `answers.next_action.type`, `choice`, `probabilities` keyed by the supplied IDs, and `confidence`; `usage.output_tokens` is **0**. No response text or arbitrary command is generated. Multiple questions are evaluated independently for Kev/Laya; shared causal prefix reuse is an optimization, not a cross-question planner. Streaming is unsupported. [S3, S4]

### Exact semantics and limits

- `choice`: nonempty option-description object, optional null descriptions; Kev/Laya allow up to **255** options. Returns argmax ID and normalized option probabilities. For these models, confidence is `max(0, (p_max - 1/K)/(1 - 1/K))` for K > 1; K=1 gives 1. **Confidence is not p_max, accuracy, authorization, or an independent abstention signal.** [S3]
- `score`: **2–10** ordered descriptions in this llama.cpp API, returns expected zero-based level index, legend, probabilities, and confidence. Kev's original server/card accepts wider score ranges; do not copy that wider contract to llama.cpp. [S3–S5]
- `noul`: true/false question with optional descriptions, returns probability of true. It is not a thresholded boolean. Laya's card warns of confident label-following failures; evaluate neutral-key binary `choice` as a comparison rather than assuming `noul` is reliable. [S3, S10]
- Native softmax uses stored calibration temperatures; these do **not** establish calibration for network telemetry, domain shifts, or quantization. A closed option set cannot by itself express unknown/out-of-distribution. An explicit no-action option is not automatic abstention or a failure policy. [S3–S5, S10]
- Documentation says non-string state is JSON, but Kev's implementation specifically flattens JSON with labelled keys and escapes special-token spellings. Preserve actual rendered/tokenized semantics in eventual evidence; do not assume identical state serialization across candidates. [S3, S4, S8]
- Kev: publisher validates **8,192-token** context, although its own runtime serves longer. Its network arithmetic, missing-fact handling and option-order sensitivity need local validation. llama.cpp does **not** port Kev's optional `date_facts` preprocessor. Question and option read positions must fit one usable output batch; server rejects or errors when required embeddings cannot be obtained. [S5, S8, S9]
- Laya: root config `max_len=512`, `head_max_len=192`; options/instructions are truncated to trained head budgets in native server. The full non-causal prompt must fit `--ubatch-size`; larger context/microbatch flags do not validate longer-model accuracy. English root is not the multilingual 8k checkpoint. Converter intentionally excludes Laya's `act_head`; its `action.act_probability` is **not** the native serving contract and publisher says it carries no usable signal. [S3, S9–S12]
- Publisher benchmarks are not local observations: ggml-org reports 12 ms Kev / 5 ms Laya for one question on RTX PRO 6000; those figures do not predict VM/CPU or Mininet end-to-end scheduling latency. File size is not peak RAM/VRAM. [S1]

## Reproducible evidence anchors

Hub metadata queried 2026-10-24; sizes/hashes below are Hub LFS declarations, **not checksums computed from full local downloads**. Cards/configs were read and compared with runtime/converter source.

| Artifact | Revision / bytes / SHA-256 |
| --- | --- |
| Kev source adapter/head repository | `jaredpalmer/kev-4b@6cfce5c2fa4b4bd64026336ab649c5ca78857d52`; adapter 129,924,032 bytes, SHA `90e817356246e7f18bfa7ca3d31794cd4fbeb3332a66a84cb51d9ceae925f2b2`; head 5,249,791 bytes, SHA `dd633435998ecc751ac538717a3742e32149500fabf7d7276287dbf0693f347c` |
| Kev base | `Qwen/Qwen3.5-4B-Base@1001bb4d826a52d1f399e183466143f4da7b741b` |
| Kev evaluation GGUF | `ggml-org/Kev-4B-GGUF@f4624b6bb7072f1bab726a622c1cad3f96030b9e/Kev-4B-Q8_0.gguf`; 4,483,801,504 bytes; SHA `7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da` |
| Laya source | `convaiinnovations/laya@7b928d828b7b0e022f929d9bd2e44165aa270148` |
| Laya GGUF | `ggml-org/Laya-GGUF@845f314dd047fc2444a5289a4d50c1e1d59e27e1/Laya-Q8_0.gguf`; 449,397,600 bytes; SHA `c06528c5746d3bb8baa72a27938be95abbfd0b226f8471e8a9e365ed0bb066d2` |
| OneJev source / community conversion | `OmniJev/OneJev-4B@c5ab4dae8d9e3c12cf060db2c6487c08851fed06`; `mradermacher/OneJev-4B-GGUF@5437c6a21a623ed983f9fd45b0ff5ccd2a9b0a80` |
| OneJev adapter code inspected | `OmniJev/OneJev@0b9e163f8114f94a40598c59b0bac473ff446f3c` |

Additional direct artifact check: fetched only the first **1 MiB** of each pinned Q8_0 GGUF through HTTP Range, parsed its GGUF v3 header and available metadata. Kev: architecture `qwen35`, 428 tensors declared, license `apache-2.0`, `qwen35.decision.type=kev`, all three temperatures `2.406049966812134`. Laya: architecture `modern-bert`, 201 tensors declared, license `apache-2.0`, `modern-bert.decision.type=laya`, two head blocks, head budget 192, and per-type/per-option-count temperatures matching publisher config. Parsing stopped when tokenizer metadata exceeded the prefix; tensor contents and remaining metadata were **not** inspected. This checks actual decision metadata beyond README claims, but not artifact completeness or inference correctness. [S6, S11, S12]

## Remaining validation and planning inputs

1. Before any implementation acceptance: build the pinned native server on the target CPU/GPU/backend; verify complete GGUF hash, named template/head loading, endpoint readiness and zero output-token behavior. Run the upstream contract-test cases plus full-model fixtures; upstream unit tests use tiny model fixtures, not proof of Kev network quality. [S19]
2. Compare BF16 and selected quantization against the source reference on identical rendered inputs: all option probabilities, temperature handling, argmax, score expectation, no-action inclusion, option permutations, missing facts and malicious/special-token input. PR #29818 reports worst probability delta `1.3e-3` for Kev and `8.4e-4` for Laya on its example; that is upstream's limited comparison, not a universal tolerance or our result. [S1, S3]
3. Use a labelled text-only network scenario to measure decision errors, calibration and cost/latency/memory under scheduled load, context/option-budget overflow, concurrency, timeouts and server failure. Freeze neither confidence cutoffs nor a fallback rule based on generic benchmarks. No training is proposed in this scope.
4. Leave model selection, scenario/action vocabulary, confidence/abstention/failure policy and adapter/execution-boundary choices to #106's human planning. Preserve Mininet AI scheduling, capability authorization and run evidence regardless of which candidate is eventually selected. Model output never grants authority.

## Primary sources

- **S1:** [Merged native support PR #29818](https://github.com/ggml-org/llama.cpp/pull/29818); [ggml-org announcement](https://huggingface.co/blog/ggml-org/decision-models-in-llamacpp). PR state/merge SHA checked using GitHub API.
- **S2:** [v0.6.0 release](https://github.com/ggml-org/llama.cpp/releases/tag/v0.6.0), [v0.7.0 release](https://github.com/ggml-org/llama.cpp/releases/tag/v0.7.0). Release/version claims checked using GitHub API.
- **S3:** [v0.7.0 server-decision.cpp](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/tools/server/server-decision.cpp): `init`, `parse_questions`, `fill_task`, `fill_task_laya`, `format_answer`, temperature/confidence functions.
- **S4:** [v0.7.0 server API reference](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/tools/server/README.md#post-v1systemone-typesafe-compatible-system-one-api).
- **S5:** [Pinned Kev publisher model card](https://huggingface.co/jaredpalmer/kev-4b/blob/6cfce5c2fa4b4bd64026336ab649c5ca78857d52/README.md).
- **S6:** [Pinned ggml-org Kev artifact tree/card](https://huggingface.co/ggml-org/Kev-4B-GGUF/tree/f4624b6bb7072f1bab726a622c1cad3f96030b9e); [Hub inventory API](https://huggingface.co/api/models/ggml-org/Kev-4B-GGUF?blobs=true).
- **S7:** [Pinned Kev adapter config](https://huggingface.co/jaredpalmer/kev-4b/blob/6cfce5c2fa4b4bd64026336ab649c5ca78857d52/adapter_config.json); [Hub source inventory](https://huggingface.co/api/models/jaredpalmer/kev-4b?blobs=true).
- **S8:** [v0.7.0 conversion/lev.py](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/conversion/lev.py): `_DecisionLoraMixin`, `KevModel`, head export and preprocessing caveat.
- **S9:** [v0.7.0 server-context.cpp](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/tools/server/server-context.cpp): `can_split`, `send_decision`, batch bounds, decision-task dispatch before sampling.
- **S10:** [Pinned Laya publisher model card](https://huggingface.co/convaiinnovations/laya/blob/7b928d828b7b0e022f929d9bd2e44165aa270148/README.md).
- **S11:** [Pinned ggml-org Laya artifact tree/card](https://huggingface.co/ggml-org/Laya-GGUF/tree/845f314dd047fc2444a5289a4d50c1e1d59e27e1); [Hub inventory API](https://huggingface.co/api/models/ggml-org/Laya-GGUF?blobs=true).
- **S12:** [Pinned Laya runtime config](https://huggingface.co/convaiinnovations/laya/blob/7b928d828b7b0e022f929d9bd2e44165aa270148/rl_agent_config.json); [v0.7.0 ModernBERT converter](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/conversion/bert.py).
- **S13:** [Pinned OneJev-4B publisher card](https://huggingface.co/OmniJev/OneJev-4B/blob/c5ab4dae8d9e3c12cf060db2c6487c08851fed06/README.md) and [architecture config](https://huggingface.co/OmniJev/OneJev-4B/blob/c5ab4dae8d9e3c12cf060db2c6487c08851fed06/config.json).
- **S14:** [Kev base card/license](https://huggingface.co/Qwen/Qwen3.5-4B-Base/tree/1001bb4d826a52d1f399e183466143f4da7b741b); [OneJev base card/license](https://huggingface.co/Qwen/Qwen3.5-4B/tree/851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a).
- **S15:** [Pinned community OneJev-4B GGUF inventory](https://huggingface.co/mradermacher/OneJev-4B-GGUF/tree/5437c6a21a623ed983f9fd45b0ff5ccd2a9b0a80).
- **S16:** [Pinned first-party OneJev llama.cpp adapter](https://github.com/OmniJev/OneJev/blob/0b9e163f8114f94a40598c59b0bac473ff446f3c/qev/llamacpp.py).
- **S17:** [Pinned OneJev prompt/slot rendering](https://github.com/OmniJev/OneJev/blob/0b9e163f8114f94a40598c59b0bac473ff446f3c/qev/prompt.py).
- **S18:** [Pinned OneJev calibration code](https://github.com/OmniJev/OneJev/blob/0b9e163f8114f94a40598c59b0bac473ff446f3c/qev/calibrate.py); [code license](https://github.com/OmniJev/OneJev/blob/0b9e163f8114f94a40598c59b0bac473ff446f3c/LICENSE).
- **S19:** [Upstream v0.7.0 systemone tests](https://github.com/ggml-org/llama.cpp/blob/v0.7.0/tools/server/tests/unit/test_systemone.py).
