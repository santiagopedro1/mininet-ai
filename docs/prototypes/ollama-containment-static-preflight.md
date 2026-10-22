# Static local preflight — THROWAWAY GH-99, NEVER MERGE

2026-10-22. Human authorized read-only staged ELF/model/CPU metadata inspection,
including referenced libraries under `/usr/lib` and `/lib`/`/lib64` aliases, capped
at 2 minutes wall, 120 seconds aggregate CPU and 512 MiB per inspection process.
No permissions changes, acquired-code execution/loading, model runtime, services,
downloads, model-store construction or deletion was authorized or performed.

## Evidence and actual execution

- [Executed single-process metadata parser](ollama_containment_static_preflight_prototype.py),
  SHA-256 `7ad15eb683edd0d373846a4828d66762ce2489c298e9330b40bad40492f569e7`.
- [Full metadata/resource observations](ollama-containment-static-preflight.observations.json).
- Inputs: prior [staging receipt](ollama-containment-extraction.observations.json)
  and [acquired-object receipt](ollama-containment-acquisition.observations.json),
  with their actual hashes recorded in this pass.

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_static_preflight_prototype.py
```

All parsing used trusted host Python, `struct` and bounded reads. No subprocess,
`readelf`, `ldd`, loader or acquired executable/library ran. ELF was parsed as
file metadata, not mapped into executable memory. GGUF was read only through its
key/value header metadata; tensor descriptors/data were not parsed or instantiated.
The CPU query retained only vendor/model/common feature flags/counts, no serials
or unrelated process/device/daemon inventory. System symlink resolution stopped
before any target outside approved `/usr/lib`.

**7/7 inspection/resource predicates pass**, including the selected-candidate
static identifiers. Wall **0.352 seconds**, reported CPU **0.366 seconds**;
304,135 ELF metadata bytes read under a 128 MiB cap and 5,931,697 GGUF metadata
bytes under a 16 MiB cap. Completed direct VmHWM **18,292 KiB (~17.86 MiB)**,
VmPeak **30,364 KiB (~29.65 MiB)**, observed RLIMIT_AS soft/hard 512 MiB.
Historical ru_maxrss remains separately recorded. These are process-local
inspection bounds, not a runtime Provider memory or host-cache guarantee.

## Observed static identities

- **38 packaged ELF files**, all ELF64 little-endian x86-64 (`e_machine=62`),
  and 11 non-ELF regular files (the total 49 staged regular files).
- **26 system-library/loader candidate ELF files** inspected as metadata.
  Actual mapped system bytes are not pinned by this inspection: stat identity,
  needed names, search paths and symbol-version metadata are observations, not
  final content hashes, actual loader resolution or a complete dlopen closure.
- Machine reports **12th Gen Intel Core i5-12450H**, GenuineIntel, 12 logical CPU
  records. Common flags include SSE4.2, AVX, AVX2, FMA and AVX-VNNI; no AVX-512
  flag is present in the recorded common set. Do not select a particular compiled
  CPU library just from its filename or equate flags with runtime compatibility.
- `bin/ollama` specifies `/lib64/ld-linux-x86-64.so.2` and requires libresolv,
  libdl, libpthread, libstdc++ and libc. The small `llama-server` ELF requires
  `libllama-server-impl.so`, llama/common/mtmd/ggml libraries and system runtime
  libraries, with `$ORIGIN` runpath. The 15,096-byte launcher is not the complete
  runner byte boundary; implementation/support libraries matter.
- Model header: **GGUF v3**, **290 tensors**, **34 metadata pairs**,
  `general.architecture=qwen2`, `general.file_type=15`, quantization version 2,
  24 blocks, embedding length 896, 14 attention heads / 2 KV heads, context metadata
  32768. Config identifies Q4_K_M / 494.03M. This is static identity consistency,
  not full GGUF validation, weight correctness, loader support or inference quality.
- Actual small manifest/config/system/template/license payloads were reread with
  exact size/hash verification. The acquired license text contains Apache License
  Version 2.0 markers, consistent with the model card. This is evidence, not legal
  advice or upstream-to-quantized-build attestation. Model source/license URLs are
  provenance metadata, not evidence that a Provider made remote requests.
- No nonempty RemoteHost/RemoteModel-style route field was found in the inspected
  config. The template supports supplied tools; no tool was called. Neither absence
  of those config fields nor template inspection proves absence of runtime cloud,
  proxy, external tool or auxiliary-client behavior.

## Material runtime constraint: embedded build/CWD search paths

All **14** packaged CPU variants have:

```text
RUNPATH=$ORIGIN:/build/llama-server-cpu/bin:
```

The trailing empty component allows a current-directory lookup; the absolute
build path is outside the owned packaged tree. These are actual embedded ELF
metadata, not an observed malicious load or proof that such a host path exists.
This phase did **not** inspect host `/build` or patch RPATH/binary bytes.

**Runtime admission stays closed.** Before a trial, an observed isolated filesystem
must make the unowned `/build` path unavailable and constrain CWD to a fresh,
controlled directory with no unowned libraries. Pin effective library/environment
search policy and observe every mapped helper/backend/library across startup,
discovery and inference. Do not assume `$ORIGIN` alone dominates every lookup,
trust an inherited LD_LIBRARY_PATH/working directory, patch the artifact silently,
or clear the gate merely because the archive hash is correct.

Static candidate analysis of the recorded metadata found **113 required-version
groups across 25 non-GPU-directory ELF files**, each with at least one inspected
packaged/system candidate advertising all requested version names. This is weaker
than actual symbol-level ABI compatibility, loader ordering, complete dependency
closure or runtime selection. Six CUDA runtime names had no candidate at the
explicitly searched `/usr/lib` path but had packaged counterparts; do not infer
host-driver absence, substitute a GPU profile, or silently broaden search paths.

## Verdict and retained gates

**Static metadata preflight completed within its limits**, with candidates and a
concrete loader-search constraint recorded. Raw `success=true` means these limited
inspection checks, **not deployment eligibility**. `deployment_eligible=false` and
`runtime_authorized=false` remain explicit. No actual CPU backend/ISA, loaded
library byte set, model compatibility, private-loopback usability, separate worker/
Provider ownership or native request/containment matrix is established.

Static validation checks parser syntax/hash, input provenance, seven predicates,
ELF counts/machines, GGUF metadata consistency, small-payload hash checks and
resource/direct-memory witnesses without rerunning metadata inspection. No staged
file permissions changed; no production tests ran. #99 remains open and claimed.

## Next proposed authorization: harmless ownership/network/filesystem fixtures

Before any Ollama launch, propose **two uniquely named disposable user services**
using only trusted harmless fixture code, each <=60 seconds, <=128 MiB and <=16
tasks; no automatic restart/retry/adoption. A bounded trusted controller observes
only their identities, listeners, namespaces and cgroups, and may stop only those
exact fixtures. Scratch/evidence remain under a fresh selected workspace artifact
subdirectory, not shared daemon/service state.

Observe distinct whole-service ownership for simulated Provider and reasoning
worker, direct communication over shared **private loopback**, no outward route,
and independent shutdown/cessation witnesses. Establish an isolated filesystem
view where `/build` is unavailable and CWD is freshly controlled. Any setup,
namespace, path/ownership, stop or evidence failure is a negative/partial verdict,
never a reason to fall back to host networking, shared daemons or wider signals.
Fixture sockets carry fixed toy data only, no Provider API, real inference, model
loading or public outbound request. Namespace setup may use authority only within
new owned user/network namespaces; no host-level elevation or firewall change.

**No acquired-code execution/loading or permission changes, model-store
construction/loading, downloads, inference or deletion.** The trusted controller
is proposed at <=128 MiB process address space and <=2 minutes wall, separate from
each unit's caps. Both services would be new/exclusively owned; no existing daemon
would be discovered, adopted or stopped. This is a proposal, **not authorization**;
static preflight approval does not permit these units or any Ollama execution.
