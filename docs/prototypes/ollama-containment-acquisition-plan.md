# Acquisition planning — THROWAWAY GH-99, NEVER MERGE

Metadata-only continuation of [Validate dedicated Ollama Provider and runner containment](https://github.com/santiagopedro1/mininet-ai/issues/99).
2026-10-22. No architecture acceptance, real deployment, download authorization,
model selection by the human or inference authorization follows from this note.
The live human authorized public server/model metadata queries and free-space
inspection under `/tmp/opencode`, explicitly without artifact acquisition or services.

## Evidence and candidate recommendation

[Metadata collector](ollama_containment_metadata_prototype.py) and
[captured metadata](ollama-containment-acquisition.metadata.json) retain source
URLs, HTTP statuses, descriptor sizes and hashes, plus the exact raw model
manifest/config strings needed to independently check their SHA-256 values.
The collector disables inherited HTTP proxies, has a 2 MB metadata response
bound and never requests a model weight layer or release archive.

- **Selected server candidate remains Ollama v0.40.8**, Linux amd64, publisher
  asset ID `663838027`, archive `ollama-linux-amd64.tar.zst`, 1,505,219,659 bytes.
  Publisher digest is still `sha256:0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a`.
  This is refreshed release metadata, not a hash of acquired archive bytes.
  Release metadata still says `immutable=false`.
- **Propose model `qwen2.5:0.5b-instruct-q4_K_M`**, rather than a thinking-model
  route that might introduce a separate reasoning capability probe. Its registry
  config reports GGUF, qwen2, 494.03M, Q4_K_M, Linux amd64. These are metadata
  claims; actual GGUF, loader behavior, inference and tool/learning quality have
  not been tested. Small footprint motivates the proposal, not production quality.
- Raw manifest SHA-256:
  `a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`.
  The registry returned **404** for retrieval by digest (including the first
  metadata-collector attempt). Retrieval by mutable tag returned bytes matching
  that exact previously observed hash. Future acquisition must reject drift;
  a tag name or a digest-shaped URL alone is not a successful immutable retrieval.
- Config metadata: 490 bytes, fetched and verified against descriptor digest
  `005f95c7475154a17e84b85cd497949d6dd2a4f9d77c096e3c66e4d9c32acaf5`.
  Its metadata request redirects to a public registry object-store host; no signed
  URL query is retained. This was **config metadata**, not weights or executable
  bytes. It says nothing about whether a running Provider would offload compute.
- Weight descriptor: 397,807,936 bytes,
  `sha256:c5396e06af294bd101b30dce59131a76d2b773e76950acc870eda801d3ab0515`.
  System/template/license layer descriptors and hashes are captured alongside it.
  Actual layer contents remain unverified. Config `rootfs.diff_ids` differs from
  manifest descriptors for two text layers; that does not by itself establish
  corruption (these fields have different roles), and loader semantics/provenance
  have not been audited here. Do not silently follow extra references or fetch
  additional unapproved blobs during acquisition.
- [Ollama model listing](https://ollama.com/library/qwen2.5:0.5b-instruct-q4_K_M)
  advertises this quantization, ~398 MB, and Apache 2.0 licensing.
  [Upstream model card at pinned revision](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/blob/7ae557604adf67be50417f59c2c2f167def9a775/README.md)
  identifies a 0.49B instruction-tuned model and Apache 2.0 metadata.
  The upstream config at that revision was retrieved, not weights or Python model
  code. Upstream-to-Ollama conversion/build provenance is **not established**.
  General family advertising is not proof of this artifact's exact capabilities.

## Storage observations and payload budget

`statvfs('/tmp/opencode')` observed available space of 8,216,715,264 bytes in the
captured run (about 7.65 GiB); the earlier inspection differed slightly, as free
space is a moving observation. `/proc/self/mountinfo` identified `/tmp` as
`tmpfs` and `/` as `btrfs`. These are capacity/type observations only.

| Item | Publisher payload bytes |
| --- | ---: |
| Compressed server archive | 1,505,219,659 |
| All four model layers plus config | 397,821,319 |
| Combined, before extraction | 1,903,040,978 |

The combined number is ~1.90 GB decimal / 1.77 GiB, **not a total workspace-size
or RAM estimate**. It excludes extraction expansion, temporary copies, retained
partial downloads, logs, evidence, filesystem overhead and runtime model/KV data.
Archive expanded size and exact needed installed library set cannot be established
from the metadata queried here. No archive was fetched, listed or unpacked.

**Do not use apparent tmpfs free capacity as memory admission.** Current available
RAM/swap and effective ancestor memory limits were not inspected in this metadata
step. The earlier 128 MiB harmless fixture says nothing about a model's footprint.
Prefer a uniquely named directory under prospective workspace-local
`.prototype-gh99-artifacts/`, subject to a read-only filesystem/capacity check and
explicit human storage selection. This directory has **not** been created.
Keep binary/model bytes out of commits; no ignore rule or exclusion was changed.

## Proposed experimental bounds — not runtime authorization

For a future **initial smoke phase only**, propose CPU-only, native `/api/chat`,
`stream=false`, `num_gpu=0`, `num_ctx=2048`, `num_predict=32`, `num_thread=2`,
one loaded model, up to two parallel requests and queue limit four. All foreground
and learning calls still need durable per-attempt accounting. Do not add reasoning
probes, automatic replay, remote tools or unsupported learning paths.

Provisional resource ceilings: Provider 2 GiB, 200% CPU, 64 tasks; separate
reasoning-worker fixture 512 MiB, 100% CPU, 32 tasks. At most four native chat
attempts in that smoke phase, up to 30 seconds per request and 120 seconds per
owned service incarnation, with bounded stop/escalation observation. These are
**planning ceilings**, not demonstrated requirements, production defaults or a
guarantee of success; revise or reject them after memory/storage feasibility and
artifact inspection. A timeout counts as a failed/unknown attempt, never an
automatic replay. The full failure matrix needs its own agreed scope and budget.

The harmless fixture did not establish usable private loopback, complete socket
ownership, separately contained worker/Provider communication or timed escalation.
Those remain gates before a live smoke test; service limits do not replace them.

## Next human decisions / authorization boundaries

1. Human select or reject the exact proposed model manifest; pin descriptors,
   not just the tag. No model is selected by this automated recommendation.
2. Human select prospective disk-backed workspace artifact storage and authorize
   read-only capacity/type checks there, plus aggregate `/proc/meminfo` and the
   current session's ancestor cgroup memory limits for feasibility. No unrelated
   process enumeration, credentials or existing daemon inspection is needed.
3. Only after feasibility, obtain separate bounded acquisition authorization.
   Download/hash verification, safe archive inspection/extraction, model-store
   construction and actual runtime execution are separate operations; none is
   implicitly permitted now. Unknown expansion must have a pre-agreed hard cap
   and safe failure behavior, not an unbounded unpack into RAM-backed scratch.
4. Obtain separate fixture/network/ownership and then live-inference authorization
   with actual supported hardware/byte identities and observation seams. Reject
   unexpected runners/endpoints/egress; retain unknown evidence and failed cleanup.

Static validation checks syntax, captured collector digest, exact raw manifest
and config hashes, publisher size sums and JSON consistency. No production tests,
Provider calls, service creation, model loading, archive/weight downloads,
provisioning, cleanup deletion or ADR changes occurred. #99 stays open and claimed.
