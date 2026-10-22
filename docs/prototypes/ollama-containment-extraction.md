# Non-executable package staging — THROWAWAY GH-99, NEVER MERGE

2026-10-22. The human explicitly authorized the full-layout extraction proposal
after the witnessed inventory repeat: one fresh private output, <=3 GiB new
regular-file payload, the same 12 GiB stream / 10,000-header / 10-minute wall /
120-second CPU / 512 MiB process-address-space ceilings, direct memory witnesses,
safe in-root relative links, no executable permissions or runtime work.

## Captured execution

- [Executed extraction tool](ollama_containment_extract_prototype.py), SHA-256
  `910bcf947fe57dd95af8dbb3e0931415b4aabe7ddc80623f986cdc176a5a3541`.
- [Full staging receipt: exact per-file hashes, link graph and resource witnesses](ollama-containment-extraction.observations.json).
- [Pinned witnessed inventory prerequisite](ollama-containment-archive-inventory.witnessed.observations.json),
  SHA-256 `5d99ecc897bb5473f152168cd815f00a8c176b678b36dbe2fbb02ef524a071f8`.

Executed exactly once:

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_extract_prototype.py
```

Fresh retained output:
`.prototype-gh99-artifacts/extraction-23a304ee610249aebda73a51eaecca55/`.
Artifact bytes remain untracked and are not pushed. Original archive, model
payloads and all prior observations remain retained; no data was deleted.

## Result: 15/15 staging checks pass

- Exactly the expected **80 headers**, matched on order, name, type, size, mode
  and link target; unsupported/additional/changed members would stop the pass.
- **49 regular files**, **2,473,144,274 bytes** newly written, below 3 GiB.
  Every file was hashed while staging and independently reopened/rehashed with
  size/type/mode checks. All per-file hashes are in the receipt.
- Six required directories plus the output root, mode **0700**; regular files
  mode **0600**, no executable bits or source ownership/setuid/timestamp metadata
  applied. These permissions are staging restrictions, not a sandbox or a proof
  against arbitrary same-UID mutation/dynamic mapping by unrelated code.
- All **28** inventoried relative symbolic links were validated as a finite,
  nonescaping, nondangling graph ending at staged inventoried regular files.
  Only after file writing and complete archive verification were the links
  created; their stored target strings were checked without following them.
  A failed/interrupted partial output would remain ineligible, never auto-repaired.
- Full compressed source consumed and verified again against
  `0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a`;
  **2,473,205,760 bytes** decoded including tar headers/padding, below 12 GiB.
- **7.336 seconds wall / 6.575 seconds reported CPU**, below ceilings.
- Completed direct VmHWM **28,672 KiB (28 MiB)**; VmPeak **40,240 KiB (~39.30 MiB)**.
  Startup, post-limit and completed direct memory predicates passed; effective
  soft/hard RLIMIT_AS remained 536,870,912 bytes in required witnesses. Effective
  CPU soft/hard 120/121 seconds and file-size soft/hard 3 GiB were recorded, with
  separate CPU-time and cumulative-file-payload checks. These are process-local
  bounds, not a cgroup-wide host/launcher/page-cache memory reservation.
- Historical `ru_maxrss` **683000 KiB** remains separately recorded, not silently
  substituted for the direct current-image memory witnesses.

Destination traversal used directory descriptors and `O_DIRECTORY|O_NOFOLLOW`;
regular files used exclusive creation and `O_NOFOLLOW`, no overwrites. All paths
were drawn from the hash-pinned inventory; no generic archive extraction API,
shell-evaluated archive strings, link-following writes, special-node creation,
acquired helper, loader or `ldd` was used. The output starts fresh/private in the
cooperative local namespace; this is not arbitrary-adversarial same-UID sandbox
certification or a power-loss/fsync durability proof.

## Covered meaning and limits

This establishes staged content hashes and package-layout evidence, **not** a
running deployment, actual ELF dependency closure, exact loaded helper/library
identity, compatible ISA, valid model execution, network/device isolation or
Provider quiescence. CUDA/Vulkan files are present because the full package was
staged; their presence authorizes neither loading nor execution. No GPU profile
was selected. The separate source/deployment assumptions and request-accounting
acceptance gaps remain unchanged.

**No** global installation, acquired-code execution/dynamic loading, model-store
construction/loading, service creation, daemon/credential inspection, network
download, inference, migration, cleanup deletion or ADR change occurred.

Static cross-checks independently validate source/inventory hashes, 15 receipt
predicates, exact counts/byte sums, retained direct memory/limit witnesses, staged
file/directory modes and actual symbolic-link metadata. No additional extraction
or archive scan was used for these checks. Staged whitespace is checked before
publication; no production test suite was run. #99 remains open and claimed.

## Next proposed scope: static local preflight, not yet authorized

Before execution, propose a **read-only** local preflight:

- Use trusted host tooling (for example `readelf`, never `ldd`) to inspect staged
  ELF architecture, interpreter, DT_NEEDED and search-path metadata. Inspect
  referenced system-library/loader metadata under `/usr/lib` (including its
  `/lib` or `/lib64` aliases), without loading an acquired executable/library.
- Inspect bounded model GGUF/header and already-acquired template/config/license
  metadata as data, not a model runtime, prompt execution or inference. Reject
  unexpected architecture, external references or unaccounted tool/delegate routes;
  static metadata alone cannot establish absence of runtime offload.
- Read `/proc/cpuinfo` for CPU vendor/model/feature flags only, no serial numbers,
  credentials, unrelated-process enumeration or existing-daemon discovery.
- Limit the phase to **2 minutes wall**, **120 seconds aggregate CPU**, **512 MiB
  per trusted inspection process**, bounded metadata/output buffers; no services,
  permissions changes, installations, new downloads, model-store provisioning,
  acquired-code execution/loading, inference or deletion.

This preflight would propose exact runtime assumptions; it would not authorize
runtime work or establish loaded-byte identity. Separate working private-loopback,
distinct worker/Provider ownership, actual service/model-loading/inference scopes
and failure-matrix authorization remain necessary. The extraction grant is consumed
and cannot authorize this phase or any executable permission change.
