"""THROWAWAY GH-99 read-only process-memory diagnostic. NEVER MERGE.

No archive access, large allocation, acquired-code loading, services or inference.
"""

import datetime
import hashlib
import json
from pathlib import Path
import resource
import time


LIMIT = 512 * 1024**2
START = time.monotonic()
OUTPUT = Path(__file__).with_name("ollama-containment-memory-diagnostic.observations.json")
if OUTPUT.exists():
    raise SystemExit("Diagnostic evidence already exists; refuse overwrite")


def snapshot():
    status = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"VmRSS", "VmHWM", "VmSize", "VmPeak", "RssAnon", "RssFile", "RssShmem"}:
            status[key] = value.strip()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "proc_self_status": status,
        "ru_maxrss_kib": usage.ru_maxrss,
        "cpu_seconds": usage.ru_utime + usage.ru_stime,
        "rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
        "rlimit_cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
    }


before = snapshot()
resource.setrlimit(resource.RLIMIT_AS, (LIMIT, LIMIT))
after = snapshot()
report = {
    "throwaway": True, "issue": 99,
    "observed_utc": datetime.datetime.now(datetime.UTC).isoformat(),
    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "scope": "fresh trusted interpreter process counters and own status only",
    "before_applying_address_space_limit": before,
    "after_applying_address_space_limit": after,
    "elapsed_seconds": time.monotonic() - START,
    "large_allocation_attempted": False,
    "archive_opened_or_rescanned": False,
    "acquired_code_loaded": False,
    "service_started": False,
    "inference_performed": False,
    "original_inventory_peak_reconstructed": False,
}
with OUTPUT.open("x") as output:
    json.dump(report, output, indent=2)
    output.write("\n")
print(json.dumps(report, indent=2))
