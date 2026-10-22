"""THROWAWAY GH-99: one authorized bounded archive inventory, NEVER MERGE.

Trusted host Python only. Never extracts or executes archive members or follows
their links. Unsupported extended/sparse metadata is a negative result, not a
reason to silently replace this parser with an unbounded extraction tool.
"""

import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import stat
import tarfile
import time

from compression.zstd import DecompressionParameter, ZstdFile


MAX_EXPANDED = 12 * 1024**3
MAX_MEMBERS = 10_000
MAX_SECONDS = 600
MAX_CPU_SECONDS = 120
MAX_ADDRESS_SPACE = 512 * 1024**2
CHUNK_BYTES = 64 * 1024
REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "docs/prototypes/ollama-containment-archive-inventory.observations.json"


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


class CompressedInput:
    def __init__(self, stream):
        self.stream = stream
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(min(size, CHUNK_BYTES) if size >= 0 else CHUNK_BYTES)
        self.count += len(data)
        self.digest.update(data)
        return data

    def readable(self):
        return True

    def seekable(self):
        return False


def main():
    if EVIDENCE.exists():
        raise RuntimeError("Inventory evidence already exists; do not overwrite or rerun")
    acquired_bytes = (REPO / "docs/prototypes/ollama-containment-acquisition.observations.json").read_bytes()
    acquired = json.loads(acquired_bytes)
    archive = next(item for item in acquired["plan"] if item["kind"] == "server-archive")
    source = Path(acquired["directory"]) / archive["filename"]
    expected_hash = "0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a"
    if archive["sha256"] != expected_hash or archive["size"] != 1505219659:
        raise RuntimeError("Unexpected acquired archive descriptor")
    if not acquired["success"] or not acquired["all_expected_objects_verified"]:
        raise RuntimeError("Acquisition prerequisite not verified")
    start = time.monotonic()
    report = {
        "throwaway": True, "issue": 99, "started_utc": now(),
        "scope": "single bounded local archive inventory only",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "acquisition_receipt_sha256": hashlib.sha256(acquired_bytes).hexdigest(),
        "archive_path": str(source), "expected_archive_sha256": expected_hash,
        "limits": {"expanded_bytes": MAX_EXPANDED, "members": MAX_MEMBERS,
                   "wall_seconds": MAX_SECONDS, "cpu_seconds": MAX_CPU_SECONDS,
                   "address_space_bytes": MAX_ADDRESS_SPACE, "zstd_window_log_max": 27},
        "expanded_bytes_read": 0, "headers_seen": 0, "entries": [],
        "declared_regular_file_bytes": 0, "duplicate_names": [],
        "cpu_role_candidates": [], "unvalidated_links": [],
        "archive_extracted": False, "archive_members_executed": False,
        "services_started": False, "model_loaded": False, "inference_performed": False,
        "complete_tar_inventory": False, "compressed_eof_verified": False,
        "parser_support": "ordinary ustar/GNU headers; extended/PAX/sparse metadata rejected",
    }
    resource.setrlimit(resource.RLIMIT_AS, (MAX_ADDRESS_SPACE, MAX_ADDRESS_SPACE))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS + 1))

    def expired(signum, frame):
        raise TimeoutError("Authorized wall or CPU inventory budget reached")

    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGXCPU, expired)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    counted = None
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as compressed:
            st = os.fstat(compressed.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size != archive["size"]:
                raise RuntimeError("Archive is not the expected acquired regular file")
            counted = CompressedInput(compressed)
            with ZstdFile(counted, "rb", options={DecompressionParameter.window_log_max: 27}) as decoded:
                def read_some(size):
                    left = MAX_EXPANDED - report["expanded_bytes_read"]
                    if left <= 0:
                        raise RuntimeError("Authorized expanded-stream budget reached")
                    data = decoded.read(min(size, CHUNK_BYTES, left))
                    report["expanded_bytes_read"] += len(data)
                    return data

                def exact(size):
                    # Only fixed 512-byte headers are accumulated by this function.
                    result = bytearray()
                    while len(result) < size:
                        data = read_some(size - len(result))
                        if not data:
                            raise RuntimeError("Truncated tar header or padding")
                        result.extend(data)
                    return bytes(result)

                def discard(size, require_zero=False):
                    while size:
                        data = read_some(min(size, CHUNK_BYTES))
                        if not data:
                            raise RuntimeError("Truncated tar member")
                        if require_zero and any(data):
                            raise RuntimeError("Unexpected nonzero tar padding")
                        size -= len(data)

                seen = set()
                while True:
                    header = exact(512)
                    if header == bytes(512):
                        if exact(512) != bytes(512):
                            raise RuntimeError("Tar lacks the second end marker")
                        while trailing := read_some(CHUNK_BYTES):
                            if any(trailing):
                                raise RuntimeError("Unexpected data after tar end markers")
                        report["complete_tar_inventory"] = True
                        break
                    report["headers_seen"] += 1
                    if report["headers_seen"] > MAX_MEMBERS:
                        raise RuntimeError("Authorized member-count budget reached")
                    member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
                    kind = member.type.decode("ascii", "backslashreplace")
                    record = {
                        "name": member.name, "type": kind, "size": member.size,
                        "mode": member.mode, "link_target": member.linkname,
                    }
                    report["entries"].append(record)
                    path = PurePosixPath(member.name)
                    if (not member.name or path.is_absolute() or ".." in path.parts
                            or len(member.name) > 4096 or len(member.linkname) > 4096):
                        raise RuntimeError("Unsafe or overlong archive member metadata")
                    if header[257:263] not in (b"ustar\0", b"ustar "):
                        raise RuntimeError("Unsupported tar header format")
                    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE,
                                            tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE):
                        raise RuntimeError("Unsupported extended/sparse/special tar member")
                    if member.size < 0 or member.size > MAX_EXPANDED:
                        raise RuntimeError("Invalid or over-budget member size")
                    normalized = str(path)
                    if normalized in seen:
                        report["duplicate_names"].append(normalized)
                    seen.add(normalized)
                    if member.type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                        report["unvalidated_links"].append(record.copy())
                    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) and member.size:
                        raise RuntimeError("Unexpected payload on a directory or link")
                    if member.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
                        report["declared_regular_file_bytes"] += member.size
                        basename = path.name
                        if (basename in ("ollama", "llama-server") or "ggml-cpu" in basename
                                or basename.startswith(("libggml-base", "libllama", "libggml.so", "libomp"))):
                            report["cpu_role_candidates"].append(record.copy())
                    discard(member.size)
                    discard((-member.size) % 512, require_zero=True)
            report["compressed_bytes_read"] = counted.count
            report["observed_archive_sha256"] = counted.digest.hexdigest()
            if counted.count != archive["size"] or counted.digest.hexdigest() != expected_hash:
                raise RuntimeError("Full compressed stream size/hash verification failed")
            report["compressed_eof_verified"] = True
        report["success"] = True
    except Exception as error:
        report["success"] = False
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        if counted is not None:
            report["compressed_bytes_read"] = counted.count
            report["partial_compressed_sha256"] = counted.digest.hexdigest()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        report["finished_utc"] = now()
        report["elapsed_seconds"] = time.monotonic() - start
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["cpu_seconds"] = usage.ru_utime + usage.ru_stime
        report["max_rss_kib"] = usage.ru_maxrss
        with EVIDENCE.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps({
            "evidence": str(EVIDENCE), "success": report.get("success"),
            "failure": report.get("failure"), "headers_seen": report["headers_seen"],
            "expanded_bytes": report["expanded_bytes_read"],
            "regular_file_bytes": report["declared_regular_file_bytes"],
            "elapsed_seconds": report["elapsed_seconds"],
            "cpu_seconds": report["cpu_seconds"], "max_rss_kib": report["max_rss_kib"],
        }, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
