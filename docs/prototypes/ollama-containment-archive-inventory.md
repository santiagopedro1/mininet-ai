# Archive inventory — THROWAWAY GH-99, NEVER MERGE

2026-10-22. Human authorized one local inventory-only pass, with 12 GiB expanded
stream / 10,000 entries / 10 minutes wall / 120 seconds CPU / 512 MiB memory.
No extraction, acquired-code execution, services, model loading, inference or
deletion was authorized or performed. No new download or second archive pass ran.

## Pinned evidence

- [First executed inventory tool, pinned before witness changes](https://github.com/santiagopedro1/mininet-ai/blob/ff138be/docs/prototypes/ollama_containment_archive_inventory_prototype.py),
  SHA-256 `f1369d9cfa45c3dda426f2c3916b2f0b73831e6035ada009f9ad690a101ecf3a`.
- [Full captured inventory and resource observations](ollama-containment-archive-inventory.observations.json).
- [Prior acquired-object receipt](ollama-containment-acquisition.observations.json).

Command executed once with trusted host Python, not an archive member:

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_archive_inventory_prototype.py
```

The preparatory trusted-runtime API probe initially used nonexistent
`compression.zstd.DParameter` and returned AttributeError. Public API inspection
identified `DecompressionParameter.window_log_max` before opening the archive;
this was not an archive pass, acquired-code execution or relaxed limit. The
actual pass used a 128 MiB maximum Zstandard window and 64 KiB streaming buffers.
No unsupported-header fallback or parser rerun occurred.

## Actual inventory result

The strict parser reached two tar end markers, accepted only zero trailing tar
padding, reached decompressor EOF and consumed/hash-verified all **1,505,219,659**
compressed bytes. The actual hash still matches the pinned acquired archive:
`0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a`.

- **80 headers:** 49 regular files, 3 directories, 28 symbolic links.
- **2,473,205,760 bytes** expanded stream, including headers/padding.
- **2,473,144,274 bytes** declared regular-file payload.
- No duplicate normalized member names observed. Member paths were inventoried,
  not opened on the host. Symlink targets were recorded, never followed or created.
- Approximately **2.925 seconds wall**, **2.927 seconds CPU** (CPU counter includes
  interpreter work). Those measured values, entry count and stream size are below
  their respective ceilings. Archive-byte integrity and complete header inventory
  are established for this one parser-supported artifact, not arbitrary archives.

The inventory identifies `bin/ollama`, `lib/ollama/llama-server`, ggml CPU variants,
llama/ggml support libraries and SONAME link chains. It also contains CUDA 12,
CUDA 13 and Vulkan directories; the published archive is **not CPU-only**.
The 36 regular files outside those three GPU directories total **64,326,914 bytes**,
but this is only a path-based candidate grouping, not a proven dependency closure,
approved stripped package or established CPU execution profile. It includes
licenses and auxiliary files. The tool's narrower role-name heuristic omits some
support files (for example libgomp/libmtmd); do not use it alone as an extractor
allowlist. Device/offload absence must still be observed across actual startup,
discovery, runner launch and inference. No exact loaded library/ISA or compatibility
claim is possible from headers alone.

## Memory-evidence anomaly: partial verdict, not a clean resource pass

The raw report says `success=true` for parser completion, but records
**`max_rss_kib=677656` (~661.77 MiB)**, above the requested 512 MiB ceiling.
The script successfully called `setrlimit(RLIMIT_AS, 512 MiB)` after imports;
RLIMIT_AS is an address-space limit, **not** an independent resident-memory
measurement or a bound on all earlier lifecycle activity. No startup/current
VmRSS/VmHWM observation or separate cgroup memory peak was collected. The script
does not include `ru_maxrss` in its success predicate.

An inherited/pre-exec high-water statistic or activity before the in-process
limit is a diagnostic hypothesis, not an observed explanation. Do not rewrite
the raw report, invent a lower peak, claim the whole 512 MiB memory scope passed,
or infer a particular resource violation mechanism from the mixed evidence.

**Verdict:** complete archive-header/integrity evidence, but **partial overall**:
the requested memory ceiling is not verified and remains a blocker for a clean
bounded-resource verdict. No extraction or runtime work proceeds from this result.

## Next gate

Propose a separately approved, read-only trusted-interpreter diagnostic of startup
`getrusage` counters, `/proc/self/status` (VmRSS/VmHWM/VmSize/VmPeak) and effective
in-process resource limits. No large allocations, archive rescan, extraction,
downloads, acquired-library imports, service creation, model loading or inference.
This could test a counter-inheritance hypothesis, not retroactively manufacture
missing telemetry for the original pass. Depending on observations, a repeat
inventory with better witnesses would need its own explicit scope.

Future extraction must separately approve exact paths/link handling, fresh output
storage, byte/member/memory/time caps and retention on failure. Archive mode bits
must not be applied to acquired code before execution authorization; do not run
`ldd`, a loader or an archive-supplied helper as an inspection shortcut. Installed
byte/dependency identities and whole runtime containment remain unvalidated.

Static validation checks the executed source and acquisition receipt hashes,
entry counts/type totals/file-size sums, exact archive hash recorded by the pass,
and explicit no-execution flags. It separately flags, rather than passes, the RSS
anomaly. No production tests ran. #99 remains open and claimed; no Provider or
architecture acceptance, and this branch must never be merged.

## Separately authorized startup-counter diagnostic

The human explicitly authorized the proposed read-only diagnostic. One fresh
trusted interpreter ran the [diagnostic script](ollama_containment_memory_diagnostic_prototype.py);
its [full observations](ollama-containment-memory-diagnostic.observations.json)
are retained. No archive was opened, no large allocation attempted, no acquired
code imported and no service/inference work performed. The only limit change was
a restrictive RLIMIT_AS applied to the diagnostic process itself; no other
process or persistent setting was modified.

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_memory_diagnostic_prototype.py
```

Before any inventory work (none was performed), `ru_maxrss` was already exactly
**677656 KiB**, matching the original inventory report. The diagnostic's own
`/proc/self/status` reported:

| Metric | Before local limit | After local limit |
| --- | ---: | ---: |
| VmHWM / VmRSS | 15,772 KiB | 15,844 KiB |
| VmPeak / VmSize | 27,980 KiB | 27,980 KiB |
| ru_maxrss | 677,656 KiB | 677,656 KiB |
| RLIMIT_AS soft/hard | unlimited | 536,870,912 bytes |

The [Linux man-pages getrusage(2) NOTES](https://man7.org/linux/man-pages/man2/getrusage.2.html#NOTES),
retrieved 2026-10-22, explicitly document that resource-usage metrics survive
`execve(2)`. This primary documentation plus the directly observed startup mismatch
supports a pre-exec/lifetime high-water explanation. No parent/unrelated process
was inspected, so the source process/event behind that value is not established.
The diagnostic does **not** show 662 MiB of inventory-phase resident memory use.

**Diagnostic verdict:** startup counter contamination is reproduced without archive
work or a large allocation. The original pass's current-image peak was not
collected and is **not reconstructed or retroactively accepted**. Keep its complete
header/integrity facts and partial memory verdict unchanged.

### Next proposed authorization, not yet granted

One repeat inventory pass with the **same** 12 GiB expanded-stream, 10,000-member,
10-minute wall, 120-second CPU and 512 MiB address-space limits. Add startup/end
`/proc/self/status` VmHWM/VmPeak snapshots and observed effective RLIMIT_AS to the
pass receipt, and explicitly evaluate those current-image memory witnesses rather
than treating inherited ru_maxrss as the only peak metric. Preserve ru_maxrss as
separate history, not delete or subtract it. Any missing/over-budget witness is
a negative/partial result, not permission to increase limits or retry again.

No extraction, model or executable launch, services, large intentional allocation,
new download or deletion. The earlier one-pass authorization is consumed; the
diagnostic authorization does not authorize this repeat or any later extraction.

## Separately authorized witnessed repeat: bounded inventory accepted by checks

The human explicitly approved **one** inventory repeat with direct memory
witnesses under the same ceilings. [Executed witnessed tool](ollama_containment_archive_inventory_prototype.py)
SHA-256 `1c875734b8301a01c55f0303c03bb295cc119ab12fad73ae82d2f09a844082f2`;
[full repeat observations](ollama-containment-archive-inventory.witnessed.observations.json).
The first tool/report remain pinned and unchanged; this is new evidence, not a
rewrite of the original missing witness.

All **10 explicit inventory/resource checks passed** in the one repeat. Full
ordered entries, link targets, file-size sums, expanded count and compressed
archive identity match the first inventory exactly: 80 headers, 2,473,205,760
expanded bytes and pinned compressed SHA-256. Wall time was **2.823 seconds**;
reported process CPU **2.841 seconds**, both below their ceilings.

| Direct current-image witness | Startup | After limit | After inventory |
| --- | ---: | ---: | ---: |
| VmHWM | 18,088 KiB | 18,092 KiB | 27,576 KiB |
| VmPeak | 30,332 KiB | 30,332 KiB | 39,704 KiB |
| Effective RLIMIT_AS soft/hard | unlimited | 512 MiB / 512 MiB | 512 MiB / 512 MiB |

Completed VmRSS was 19,192 KiB; VmSize 30,932 KiB. Direct peak RSS was therefore
~26.93 MiB and peak address space ~38.77 MiB, below 512 MiB. Required snapshots
were present and parsed with units; missing/malformed/over-budget evidence would
have failed the predicate. Effective CPU soft/hard limits were 120/121 seconds,
with the 120-second soft-limit signal stopping inventory work; the observed
reported CPU check separately enforces <=120 seconds.

Historical `ru_maxrss` was **683000 KiB** both at startup and after inventory;
it remains in the raw report, separately labeled rather than subtracted, erased
or treated as current-image RSS. The repeat witnesses support this process-local
inventory workload's memory verdict. They do not establish a cgroup-wide bound,
account for all host page-cache/launcher memory, or certify a Provider footprint.
The original pass remains partial on its own historical evidence; this new pass
supplies the current-image peak witness for an independently repeated inventory.

No extraction, executable/library loading, new download, service, model load,
inference, large intentional allocation or deletion occurred. Static provenance,
exact first/repeat inventory equality, direct memory checks and explicit retained
no-execution flags are validated separately without another archive pass.

### Next proposed authorization: full-layout extraction only

Recommend staging the **full packaged file set and relative link layout**, rather
than silently selecting a stripped CPU dependency closure. Actual CPU-only runtime
eligibility still needs device/network/loaded-library observation; keeping CUDA/
Vulkan files does not authorize loading or using those backends. No public
compatibility or deployment profile is accepted merely by extraction.

Proposed scope, **not yet authorized**:

- One extraction pass into a fresh private subdirectory under selected
  `.prototype-gh99-artifacts/`; never install into host `/usr` or touch old outputs.
- Exact expected 80-member inventory and archive hash, with size/type/name/link
  equality checks, no path traversal/absolute destinations/special nodes, no
  overwriting existing files. Any mismatch stops and retains explicit failure.
- At most **3 GiB additional regular-file payload**, **12 GiB decoded stream**,
  **10,000 headers**, **10 minutes wall**, **120 seconds CPU**, **512 MiB process
  address space**, with direct memory witnesses as in the repeat. Expected payload
  is 2,473,144,274 bytes, but no higher cap is implied if it changes.
- Regular files remain mode **0600**, directories **0700**. Do not apply archive
  ownership, executable/setuid bits, timestamps or extended metadata. Hash each
  staged file and retain exact byte provenance.
- Create only inventoried **relative symbolic links**, after validating the whole
  link graph terminates at inventoried in-root files (no cycles/escape/dangling
  targets), and only after regular-file writing/full archive verification. Never
  follow an archive link while writing or hashing a destination.
- No new network/download, acquired-code execution, dynamic loading/`ldd`, global
  installation, service creation, model-store loading, inference or deletion.
  Partial outputs remain explicitly ineligible; do not repair, retry or weaken
  the gate automatically. This inventory-repeat grant does not authorize extraction.
