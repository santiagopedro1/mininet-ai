# Acquisition planning — THROWAWAY GH-99, NEVER MERGE

Metadata-only continuation of [Validate dedicated Ollama Provider and runner containment](https://github.com/santiagopedro1/mininet-ai/issues/99).
2026-10-22. No architecture acceptance, real deployment, download authorization
or inference authorization follows from this note. The initial metadata snapshot
predates model selection; the human subsequently explicitly selected the proposed
exact model manifest and workspace storage path, as recorded below.
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

1. **Settled:** human selected the proposed exact model manifest, not just the tag.
   This accepts the artifact candidate, not its runtime behavior or compatibility.
2. **Settled:** human selected prospective workspace `.prototype-gh99-artifacts/`
   storage and authorized read-only capacity/type, aggregate `/proc/meminfo` and
   current-session ancestor cgroup memory-limit checks. Observations below retain
   their limits; no unrelated process, credentials or existing daemon was inspected.
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

## Authorized read-only feasibility snapshot after human selection

[Exact transcribed observations](ollama-containment-feasibility.observations.json)
record the selected model manifest/storage and the authorized read-only checks.
The artifact directory did **not** exist and was not created. `statvfs` was run
on the nearest existing ancestor (the workspace); `/proc/self/mountinfo` places
the prospective path under the `/home` Btrfs mount. Available space was
396,868,403,200 bytes (~397 GB decimal / 370 GiB), comfortably above the proposed
download-only payload. This is not a quota or allocation/write test. Btrfs
reported available inode count zero through statvfs; this alone does not prove
exhaustion or guarantee future allocations.

Aggregate `MemAvailable` was 10,115,572 KiB (~10.36 GB decimal / 9.65 GiB),
`MemTotal` 16,084,804 KiB; swap total/free were both 4,194,300 KiB. Inspected
non-root ancestors of this session reported `memory.max=max`, `memory.high=max`
and `memory.swap.max=max`; the root exposed none of those files. No finite
ancestor limit was observed. Ancestor usage overlaps; do not add the reported
`memory.current` values. This neither reserves memory nor validates the proposed
future services' effective limits.

`Committed_AS` was 14,257,188 KiB, above `CommitLimit` 12,236,700 KiB. The
overcommit policy was not inspected. Do not infer guaranteed allocations, an
immediate failure or permission to raise limits from these aggregate values.
Refresh resource observations before any separately authorized runtime phase.

**Feasibility verdict:** sufficient sampled disk capacity to propose bounded
acquisition, not runtime/containment acceptance. Archive expansion remains unknown;
it is unnecessary to unpack anything to validate acquired compressed-file hashes.

### Acquisition-only scope proposed, then separately granted

- Create only a uniquely named acquisition subdirectory under the selected
  workspace-local `.prototype-gh99-artifacts/` root; keep artifact bytes untracked.
- Fetch the exact pinned server archive and model manifest/config/four layers
  from public GitHub/Ollama registry and their HTTPS download CDNs, without
  credentials inspection, inherited proxies, Provider APIs or an installed daemon.
  Do not follow extra config references to download unapproved blobs.
- Maximum **2 GiB combined response-body/file payload**, **20 minutes total**;
  stream with bounded buffers, exact expected per-object size/hash checks and an
  absolute deadline. No automatic retries/resume or overwriting existing files.
  Exact publisher payload is 1,903,040,978 bytes plus the tiny manifest/receipts.
- Stop on changed metadata, unexpected content/size/hash, write failure or budget
  exhaustion; preserve partial files and failure evidence without deletion or
  successful-acquisition claims. Do not relax pins or automatically switch models.
- **No extraction, installation, executable launch, services, model loading,
  inference, actual-data changes or destructive cleanup.** Those require later
  distinct scopes, including archive expansion caps and the remaining networking/
  ownership/containment evidence. This proposal did not authorize itself; the human
  subsequently explicitly approved this exact acquisition-only scope.

## Authorized acquisition result: actual archive/model bytes verified

The human approved the capped acquisition proposal. The
[executed downloader](ollama_containment_acquire_prototype.py) fetched exactly
seven objects into a fresh private directory, with no retry, resume, overwrite,
unpack, acquired-code execution, Provider call, service or model loading:

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_acquire_prototype.py
```

[Full acquisition receipt](ollama-containment-acquisition.observations.json)
records exact filenames/descriptor hashes, response status/final public host,
received/written bytes, observed hashes, source/input hashes and timings. Signed
redirect queries and credentials are not retained. Source SHA-256:
`7ffc49aac7194f51adf429d989f7d78fdbcaae4f94fe2dfb9286efc90239ba7b`.

All seven objects matched their pinned sizes and SHA-256 values:
manifest, config, weight, system, template, license, server archive. Total
**1,903,041,835 response-body bytes**, including the 857-byte manifest, in
**180.477 seconds**; below both the 2 GiB and 1,200-second limits. The exact
downloaded archive now matches the publisher hash, not merely publisher metadata.
This does not verify a signature, reproducible build or installed/loaded bytes.

Local retained directory:
`.prototype-gh99-artifacts/acquisition-883a415d8f5f48db9f1aa5f4815ac83a/`.
It contains the append-only acquisition events, initial plan, final receipt and
payload files. These artifacts stay **untracked**, outside pushed commits. The
directory is mode 0700; payloads are non-executable regular files, mode 0600.
File presence alone is not acquisition evidence; consult the exact verified receipt.
No ignore rule or unrelated data was altered; no files were deleted.

An independent post-download pass reopened and rehashed all seven actual files,
checked exact sizes/types/permissions, source and input metadata hashes, local
receipt equality, object cardinality and both reported budgets. All passed.
Static source parsing passed. No production suite ran. Earlier metadata-only
and feasibility observations remain historical snapshots, not retroactively
rewritten permissions or measurements.

**Still unperformed:** archive inventory/extraction, installed-library/helper
inventory, GGUF/loader semantics, usable private loopback, distinct worker/Provider
ownership and all real native-request/containment tests. Download origin at a
public registry CDN is not evidence of runtime offload; no Provider ran here.

### Next proposed scope: bounded archive inventory only

The acquisition grant is now consumed. Before extraction, propose one local
inventory pass over the verified archive: stream-decompress and parse headers,
record paths/types/sizes/link targets and proposed CPU-only candidates. Enforce
at most **12 GiB decompressed stream**, **10,000 members**, **10 minutes wall time**,
**120 seconds CPU time**, and **512 MiB address space**. Stop on malformed,
unsupported or over-budget input and retain negative evidence, not relaxed bounds.

This would run only trusted host tooling/Python, never an acquired executable or
library. **No archive files would be extracted**, links followed, permissions
applied, installations performed, services started, model loaded, inference run
or existing files deleted. Record paths as untrusted metadata; do not execute
shell fragments or follow paths supplied by the archive. Exact extraction support
and storage expansion caps follow the actual inventory under a later explicit
scope. The human subsequently approved this inventory-only scope. The
[actual inventory result](ollama-containment-archive-inventory.md) reached all
80 headers and verified archive integrity without extraction, but recorded an
unresolved memory-telemetry anomaly. Its overall verdict remains partial; no
extraction or runtime work is authorized by it.
