# Archive inventory — THROWAWAY GH-99, NEVER MERGE

2026-10-22. Human authorized one local inventory-only pass, with 12 GiB expanded
stream / 10,000 entries / 10 minutes wall / 120 seconds CPU / 512 MiB memory.
No extraction, acquired-code execution, services, model loading, inference or
deletion was authorized or performed. No new download or second archive pass ran.

## Pinned evidence

- [Executed inventory tool](ollama_containment_archive_inventory_prototype.py),
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
