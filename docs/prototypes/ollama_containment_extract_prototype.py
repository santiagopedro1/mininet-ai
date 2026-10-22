"""THROWAWAY GH-99: separately authorized bounded non-executable staging only.

NEVER MERGE. No network, install, acquired-code loading, services or inference.
Archive paths are compared to the pinned inventory, never passed to an extractor.
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
import uuid

from compression.zstd import DecompressionParameter, ZstdFile


MAX_FILES = 3 * 1024**3
MAX_EXPANDED = 12 * 1024**3
MAX_MEMBERS = 10_000
MAX_SECONDS = 600
MAX_CPU = 120
MAX_MEMORY = 512 * 1024**2
CHUNK = 64 * 1024
REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs/prototypes"
OUTPUT = DOCS / "ollama-containment-extraction.observations.json"
INVENTORY_HASH = "5d99ecc897bb5473f152168cd815f00a8c176b678b36dbe2fbb02ef524a071f8"
ARCHIVE_HASH = "0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a"


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def memory():
    values = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"VmPeak", "VmSize", "VmHWM", "VmRSS"}:
            values[key] = value.strip()
    return {"proc_self_status": values,
            "rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
            "ru_maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def memory_ok(witness, require_limit=True):
    try:
        for key in ("VmPeak", "VmSize", "VmHWM", "VmRSS"):
            value, unit = witness["proc_self_status"][key].split()
            if unit != "kB" or not 0 < int(value) * 1024 <= MAX_MEMORY:
                return False
        return not require_limit or witness["rlimit_as"] == [MAX_MEMORY, MAX_MEMORY]
    except (KeyError, ValueError, TypeError):
        return False


class CompressedInput:
    def __init__(self, stream):
        self.stream = stream
        self.count = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(min(size, CHUNK) if size >= 0 else CHUNK)
        self.count += len(data)
        self.digest.update(data)
        return data

    def readable(self):
        return True

    def seekable(self):
        return False


def safe_parts(name):
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or ".." in p.parts or "\\" in name
            or "\0" in name or len(name) > 4096 or str(p) != name):
        raise RuntimeError("Noncanonical or unsafe inventoried path")
    return p.parts


def parent_fd(root_fd, parts):
    result = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=result)
            os.close(result)
            result = next_fd
        return result
    except BaseException:
        os.close(result)
        raise


def resolve_link(name, entries):
    current = name
    chain = []
    while entries[current]["type"] == "2":
        if current in chain:
            raise RuntimeError("Inventoried symbolic-link cycle")
        chain.append(current)
        target = entries[current]["link_target"]
        p = PurePosixPath(target)
        if not target or p.is_absolute() or "\\" in target or "\0" in target:
            raise RuntimeError("Unsafe inventoried symbolic-link target")
        parts = list(PurePosixPath(current).parent.parts)
        for part in p.parts:
            if part == "..":
                if not parts:
                    raise RuntimeError("Symbolic-link target escapes staging root")
                parts.pop()
            elif part != ".":
                parts.append(part)
        current = "/".join(parts)
        if current not in entries:
            raise RuntimeError("Dangling or non-inventoried symbolic-link target")
    if entries[current]["type"] != "0":
        raise RuntimeError("Symbolic-link graph does not terminate at a regular file")
    return {"name": name, "target": entries[name]["link_target"],
            "chain": chain, "final_regular_file": current}


def main():
    if OUTPUT.exists():
        raise RuntimeError("Extraction evidence already exists; refuse overwrite or rerun")
    start = time.monotonic()
    inventory_bytes = (DOCS / "ollama-containment-archive-inventory.witnessed.observations.json").read_bytes()
    if hashlib.sha256(inventory_bytes).hexdigest() != INVENTORY_HASH:
        raise RuntimeError("Pinned inventory changed")
    inventory = json.loads(inventory_bytes)
    if not inventory["success"] or not all(inventory["checks"].values()):
        raise RuntimeError("Witnessed inventory prerequisite not verified")
    entries = inventory["entries"]
    mapping = {entry["name"]: entry for entry in entries}
    if len(mapping) != len(entries) or len(entries) != 80:
        raise RuntimeError("Unexpected or duplicate inventory members")
    directories = set()
    for entry in entries:
        parts = safe_parts(entry["name"])
        if entry["type"] not in ("0", "2", "5"):
            raise RuntimeError("Unapproved extraction member type")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in mapping and mapping[parent]["type"] != "5":
                raise RuntimeError("Non-directory inventory member used as parent")
            directories.add(parent)
        if entry["type"] == "5":
            directories.add(entry["name"])
    links = [resolve_link(entry["name"], mapping) for entry in entries if entry["type"] == "2"]
    expected_bytes = sum(entry["size"] for entry in entries if entry["type"] == "0")
    if expected_bytes != 2473144274 or expected_bytes > MAX_FILES:
        raise RuntimeError("Unexpected or over-budget expected file payload")
    artifact_root = REPO / ".prototype-gh99-artifacts"
    st = artifact_root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("Artifact root is not the owned private directory")
    report = {
        "throwaway": True, "issue": 99, "scope": "one non-executable full-layout staging pass",
        "started_utc": now(), "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_inventory_sha256": INVENTORY_HASH, "expected_archive_sha256": ARCHIVE_HASH,
        "limits": {"file_payload_bytes": MAX_FILES, "expanded_bytes": MAX_EXPANDED,
                   "members": MAX_MEMBERS, "wall_seconds": MAX_SECONDS,
                   "cpu_seconds": MAX_CPU, "address_space_bytes": MAX_MEMORY},
        "startup_memory": memory(), "expanded_bytes_read": 0, "headers_seen": 0,
        "file_payload_bytes_written": 0, "files": [], "created_links": [],
        "validated_link_graph": links, "full_archive_verified": False,
        "created_directories": [], "independent_file_verification": False,
        "acquired_code_executed_or_loaded": False, "service_started": False,
        "model_loaded": False, "inference_performed": False,
        "new_download_performed": False, "deletion_performed": False,
    }
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU, MAX_CPU + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILES, MAX_FILES))
    report["post_limit_memory"] = memory()
    report["effective_cpu_limit"] = list(resource.getrlimit(resource.RLIMIT_CPU))
    report["effective_file_size_limit"] = list(resource.getrlimit(resource.RLIMIT_FSIZE))

    def expired(signum, frame):
        raise TimeoutError("Authorized extraction wall or CPU deadline reached")

    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGXCPU, expired)
    signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    root_fd = None
    counted = None
    try:
        destination = artifact_root / ("extraction-" + uuid.uuid4().hex)
        destination.mkdir(mode=0o700)
        report["directory"] = str(destination)
        root_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for name in sorted(directories, key=lambda item: (len(PurePosixPath(item).parts), item)):
            parts = safe_parts(name)
            fd = parent_fd(root_fd, parts)
            try:
                os.mkdir(parts[-1], mode=0o700, dir_fd=fd)
            finally:
                os.close(fd)
            report["created_directories"].append(name)
        source_fd = os.open(inventory["archive_path"], os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(source_fd, "rb") as compressed:
            st = os.fstat(compressed.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_size != 1505219659:
                raise RuntimeError("Unexpected archive source")
            counted = CompressedInput(compressed)
            with ZstdFile(counted, "rb", options={DecompressionParameter.window_log_max: 27}) as decoded:
                def read_some(size):
                    left = MAX_EXPANDED - report["expanded_bytes_read"]
                    if left <= 0:
                        raise RuntimeError("Authorized expanded-stream budget reached")
                    data = decoded.read(min(size, CHUNK, left))
                    report["expanded_bytes_read"] += len(data)
                    return data

                def exact(size):
                    result = bytearray()
                    while len(result) < size:
                        data = read_some(size - len(result))
                        if not data:
                            raise RuntimeError("Truncated tar header")
                        result.extend(data)
                    return bytes(result)

                for expected in entries:
                    header = exact(512)
                    member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
                    actual = {"name": member.name, "type": member.type.decode("ascii", "backslashreplace"),
                              "size": member.size, "mode": member.mode, "link_target": member.linkname}
                    report["headers_seen"] += 1
                    if actual != expected or report["headers_seen"] > MAX_MEMBERS:
                        raise RuntimeError("Archive member differs from pinned inventory")
                    if expected["type"] == "0":
                        parts = safe_parts(expected["name"])
                        fd = parent_fd(root_fd, parts)
                        try:
                            file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                              0o600, dir_fd=fd)
                        finally:
                            os.close(fd)
                        result = {"name": expected["name"], "size": expected["size"],
                                  "written_bytes": 0, "complete": False}
                        report["files"].append(result)
                        digest = hashlib.sha256()
                        remaining = expected["size"]
                        with os.fdopen(file_fd, "wb") as output:
                            while remaining:
                                data = read_some(min(remaining, CHUNK))
                                if not data:
                                    raise RuntimeError("Truncated regular file")
                                if report["file_payload_bytes_written"] + len(data) > MAX_FILES:
                                    raise RuntimeError("Authorized additional-file payload budget reached")
                                output.write(data)
                                digest.update(data)
                                remaining -= len(data)
                                result["written_bytes"] += len(data)
                                report["file_payload_bytes_written"] += len(data)
                            output.flush()
                            os.fsync(output.fileno())
                        result["sha256"] = digest.hexdigest()
                        result["complete"] = True
                    elif expected["size"]:
                        raise RuntimeError("Unexpected non-regular member payload")
                    padding = (-expected["size"]) % 512
                    if padding and any(exact(padding)):
                        raise RuntimeError("Nonzero tar member padding")
                if exact(1024) != bytes(1024):
                    raise RuntimeError("Missing tar end markers or extra member")
                while trailing := read_some(CHUNK):
                    if any(trailing):
                        raise RuntimeError("Nonzero trailing tar content")
            report["compressed_bytes_read"] = counted.count
            report["observed_archive_sha256"] = counted.digest.hexdigest()
            if counted.count != 1505219659 or counted.digest.hexdigest() != ARCHIVE_HASH:
                raise RuntimeError("Full compressed archive size/hash mismatch")
        report["full_archive_verified"] = True
        # Links are created only after every regular file and the full archive verify.
        for link in links:
            parts = safe_parts(link["name"])
            fd = parent_fd(root_fd, parts)
            try:
                os.symlink(link["target"], parts[-1], dir_fd=fd)
                if os.readlink(parts[-1], dir_fd=fd) != link["target"]:
                    raise RuntimeError("Created link differs from validated metadata")
            finally:
                os.close(fd)
            report["created_links"].append(link["name"])
        # Reopen only direct regular-file paths, O_NOFOLLOW at every component.
        for result in report["files"]:
            parts = safe_parts(result["name"])
            fd = parent_fd(root_fd, parts)
            try:
                verify_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            finally:
                os.close(fd)
            with os.fdopen(verify_fd, "rb") as verified:
                st = os.fstat(verified.fileno())
                if (not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600
                        or st.st_size != result["size"] or st.st_nlink != 1):
                    raise RuntimeError("Staged regular-file identity/size/mode mismatch")
                if hashlib.file_digest(verified, "sha256").hexdigest() != result["sha256"]:
                    raise RuntimeError("Independent staged-file hash mismatch")
            result["independently_verified"] = True
        for name in report["created_directories"]:
            parts = safe_parts(name)
            fd = parent_fd(root_fd, parts)
            try:
                st = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o700:
                    raise RuntimeError("Staged directory mode/type mismatch")
            finally:
                os.close(fd)
        report["independent_file_verification"] = True
        report["operation_success"] = True
    except Exception as error:
        report["operation_success"] = False
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        if counted is not None:
            report["compressed_bytes_read"] = counted.count
            report["partial_compressed_sha256"] = counted.digest.hexdigest()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if root_fd is not None:
            os.close(root_fd)
        report["finished_utc"] = now()
        report["elapsed_seconds"] = time.monotonic() - start
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["cpu_seconds"] = usage.ru_utime + usage.ru_stime
        report["historical_ru_maxrss_kib"] = usage.ru_maxrss
        try:
            report["completed_memory"] = memory()
        except Exception as error:
            report["completed_memory"] = {}
            report["memory_witness_error"] = type(error).__name__
        report["checks"] = {
            "operation_completed": report.get("operation_success", False),
            "full_archive_verified": report["full_archive_verified"],
            "exact_member_count": report["headers_seen"] == len(entries),
            "exact_file_count": len(report["files"]) == 49,
            "exact_link_count": len(report["created_links"]) == len(links) == 28,
            "exact_file_payload_within_budget": report["file_payload_bytes_written"] == expected_bytes <= MAX_FILES,
            "exact_expanded_stream_within_budget": report["expanded_bytes_read"] == inventory["expanded_bytes_read"] <= MAX_EXPANDED,
            "wall_time_within_budget": report["elapsed_seconds"] <= MAX_SECONDS,
            "cpu_time_within_budget": report["cpu_seconds"] <= MAX_CPU,
            "effective_cpu_limit": report["effective_cpu_limit"] == [MAX_CPU, MAX_CPU + 1],
            "effective_file_size_limit": report["effective_file_size_limit"] == [MAX_FILES, MAX_FILES],
            "startup_memory_witness": memory_ok(report["startup_memory"], require_limit=False),
            "post_limit_memory_witness": memory_ok(report["post_limit_memory"]),
            "completed_memory_witness": memory_ok(report["completed_memory"]),
            "independent_file_verification": report["independent_file_verification"],
        }
        report["success"] = all(report["checks"].values())
        if not report["success"] and "failure" not in report:
            report["failure"] = {"type": "BoundEvidenceFailure", "message": "Required extraction witness failed"}
        with OUTPUT.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps({"evidence": str(OUTPUT), "directory": report.get("directory"),
                          "success": report["success"], "failure": report.get("failure"),
                          "file_bytes": report["file_payload_bytes_written"],
                          "elapsed_seconds": report["elapsed_seconds"], "cpu_seconds": report["cpu_seconds"],
                          "checks": report["checks"], "memory": report["completed_memory"]}, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
