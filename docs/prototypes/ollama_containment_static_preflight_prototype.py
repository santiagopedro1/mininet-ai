"""THROWAWAY GH-99: bounded local metadata-as-data preflight. NEVER MERGE.

No subprocess, ldd, dynamic loading, permission changes, model runtime or network.
"""

import datetime
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import stat
import struct
import time


REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs/prototypes"
OUTPUT = DOCS / "ollama-containment-static-preflight.observations.json"
MEMORY_LIMIT = 512 * 1024**2
WALL_LIMIT = 120
CPU_LIMIT = 120
ELF_READ_LIMIT = 128 * 1024**2
GGUF_READ_LIMIT = 16 * 1024**2
READ_BYTES = 0


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def memory():
    fields = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"VmPeak", "VmSize", "VmHWM", "VmRSS"}:
            fields[key] = value.strip()
    return {"proc_self_status": fields, "rlimit_as": list(resource.getrlimit(resource.RLIMIT_AS)),
            "historical_ru_maxrss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def memory_ok(witness):
    try:
        return witness["rlimit_as"] == [MEMORY_LIMIT, MEMORY_LIMIT] and all(
            value.split()[1] == "kB" and 0 < int(value.split()[0]) * 1024 <= MEMORY_LIMIT
            for value in (witness["proc_self_status"][key] for key in ("VmPeak", "VmSize", "VmHWM", "VmRSS")))
    except (KeyError, ValueError, IndexError):
        return False


def elf_metadata(path):
    global READ_BYTES
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError("ELF metadata source is not a regular file")

        def read(offset, count):
            global READ_BYTES
            if offset < 0 or count < 0 or count > 1024**2 or offset + count > st.st_size:
                raise RuntimeError("Invalid or overlarge ELF metadata range")
            if READ_BYTES + count > ELF_READ_LIMIT:
                raise RuntimeError("ELF metadata byte budget reached")
            data = os.pread(fd, count, offset)
            READ_BYTES += len(data)
            if len(data) != count:
                raise RuntimeError("Truncated ELF metadata")
            return data

        if st.st_size < 4 or read(0, 4) != b"\x7fELF":
            return None
        header = read(0, 64)
        if header[4:7] != b"\x02\x01\x01":
            raise RuntimeError("Unsupported ELF class, byte order or version")
        fields = struct.unpack("<16sHHIQQQIHHHHHH", header)
        machine, phoff, phsize, phnum = fields[2], fields[5], fields[9], fields[10]
        if phsize != 56 or not 0 < phnum <= 1024:
            raise RuntimeError("Unsupported ELF program-header geometry")
        segments = []
        interpreter = None
        for index in range(phnum):
            ptype, flags, offset, address, physical, filesz, memsz, align = struct.unpack(
                "<IIQQQQQQ", read(phoff + index * phsize, 56))
            segments.append({"type": ptype, "offset": offset, "address": address, "filesz": filesz})
            if ptype == 3:
                if filesz > 4096:
                    raise RuntimeError("Overlong ELF interpreter metadata")
                interpreter = read(offset, filesz).rstrip(b"\0").decode("utf-8")

        def address_offset(address, count=1):
            for segment in segments:
                if (segment["type"] == 1 and segment["address"] <= address
                        and address + count <= segment["address"] + segment["filesz"]):
                    return segment["offset"] + address - segment["address"]
            raise RuntimeError("ELF metadata address outside a file-backed load segment")

        dynamic = []
        for segment in segments:
            if segment["type"] != 2:
                continue
            if segment["filesz"] // 16 > 16384:
                raise RuntimeError("Overlarge ELF dynamic metadata")
            for index in range(segment["filesz"] // 16):
                tag, value = struct.unpack("<qQ", read(segment["offset"] + index * 16, 16))
                if tag == 0:
                    break
                dynamic.append((tag, value))
        tags = dict(dynamic)

        def string(index):
            if 5 not in tags or 10 not in tags or index >= tags[10]:
                raise RuntimeError("Invalid ELF dynamic string reference")
            base = address_offset(tags[5], tags[10])
            result = bytearray()
            while len(result) < min(4096, tags[10] - index):
                data = read(base + index + len(result), min(128, 4096 - len(result), tags[10] - index - len(result)))
                if b"\0" in data:
                    result.extend(data.split(b"\0", 1)[0])
                    return result.decode("utf-8")
                result.extend(data)
            raise RuntimeError("Unterminated or overlong ELF dynamic string")

        required = {}
        cursor = tags.get(0x6FFFFFFE)
        count = tags.get(0x6FFFFFFF, 0)
        if count > 4096:
            raise RuntimeError("Overlarge ELF version requirement table")
        for _ in range(count):
            version, number, filename, auxiliary, following = struct.unpack("<HHIII", read(address_offset(cursor, 16), 16))
            if version != 1 or number > 4096:
                raise RuntimeError("Unsupported ELF version requirement")
            name = string(filename)
            aux = cursor + auxiliary
            required[name] = []
            for _ in range(number):
                hash_value, flags, other, version_name, next_aux = struct.unpack("<IHHII", read(address_offset(aux, 16), 16))
                required[name].append(string(version_name))
                if not next_aux:
                    break
                aux += next_aux
            if not following:
                break
            cursor += following
        definitions = []
        cursor = tags.get(0x6FFFFFFC)
        count = tags.get(0x6FFFFFFD, 0)
        if count > 4096:
            raise RuntimeError("Overlarge ELF version definition table")
        for _ in range(count):
            version, flags, index, number, hash_value, auxiliary, following = struct.unpack(
                "<HHHHIII", read(address_offset(cursor, 20), 20))
            if version != 1:
                raise RuntimeError("Unsupported ELF version definition")
            name, unused = struct.unpack("<II", read(address_offset(cursor + auxiliary, 8), 8))
            definitions.append(string(name))
            if not following:
                break
            cursor += following
        return {
            "machine": machine, "elf_class": 64, "byte_order": "little", "interpreter": interpreter,
            "needed": [string(value) for tag, value in dynamic if tag == 1],
            "soname": string(tags[14]) if 14 in tags else None,
            "rpath": string(tags[15]) if 15 in tags else None,
            "runpath": string(tags[29]) if 29 in tags else None,
            "required_versions": required, "defined_versions": definitions,
            "file_stat": {"size": st.st_size, "device": st.st_dev, "inode": st.st_ino, "mtime_ns": st.st_mtime_ns},
        }
    finally:
        os.close(fd)


def system_candidate(name):
    # Resolve links lexically and stop before inspecting anything outside /usr/lib.
    path = Path(name) if name.startswith("/") else Path("/usr/lib") / name
    if not name.startswith("/") and "/" in name:
        return {"unresolved": "dependency contains an unapproved relative path"}
    if str(path).startswith(("/lib/", "/lib64/")):
        alias = Path("/" + path.parts[1])
        if not alias.is_symlink():
            return {"unresolved": "unexpected library-root alias"}
        target = os.path.normpath(str(alias.parent / os.readlink(alias)))
        if target != "/usr/lib":
            return {"unresolved": "library-root alias exits approved root"}
        path = Path(target).joinpath(*path.parts[2:])
    root = Path("/usr/lib")
    for _ in range(32):
        normalized = Path(os.path.normpath(path))
        if not normalized.is_relative_to(root):
            return {"unresolved": "reference exits approved library root"}
        current = root
        changed = False
        parts = normalized.relative_to(root).parts
        for index, part in enumerate(parts):
            current /= part
            try:
                st = current.lstat()
            except FileNotFoundError:
                return {"unresolved": "no candidate at explicitly searched system path"}
            if stat.S_ISLNK(st.st_mode):
                target = Path(os.readlink(current))
                path = (target if target.is_absolute() else current.parent / target).joinpath(*parts[index + 1:])
                changed = True
                break
        if not changed:
            return {"canonical_path": str(normalized)}
    return {"unresolved": "system metadata link-depth limit reached"}


def gguf_metadata(path):
    consumed = 0
    digest = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb", buffering=0) as stream:
        def read(count):
            nonlocal consumed
            if count < 0 or count > 1024**2 or consumed + count > GGUF_READ_LIMIT:
                raise RuntimeError("GGUF metadata byte budget reached")
            data = stream.read(count)
            if len(data) != count:
                raise RuntimeError("Truncated GGUF metadata")
            consumed += count
            digest.update(data)
            return data

        def number(fmt):
            return struct.unpack("<" + fmt, read(struct.calcsize("<" + fmt)))[0]

        def text(keep):
            length = number("Q")
            if length > 4 * 1024**2:
                raise RuntimeError("Overlong GGUF metadata string")
            if keep and length <= 4096:
                return read(length).decode("utf-8", "replace")
            left = length
            while left:
                count = min(left, 65536)
                read(count)
                left -= count
            return {"string_bytes": length, "not_retained": True}

        if read(4) != b"GGUF":
            raise RuntimeError("Not the expected GGUF model")
        version = number("I")
        tensors, pairs = number("Q"), number("Q")
        if version not in (2, 3) or pairs > 4096 or tensors > 100000:
            raise RuntimeError("Unsupported GGUF header geometry")
        formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "B", 10: "Q", 11: "q", 12: "d"}
        values = {}
        for _ in range(pairs):
            key = text(True)
            if not isinstance(key, str) or key in values:
                raise RuntimeError("Overlong or duplicate GGUF metadata key")
            kind = number("I")
            if kind == 8:
                value = text(True)
            elif kind == 9:
                element, count = number("I"), number("Q")
                if count > 2_000_000 or element not in {*formats, 8}:
                    raise RuntimeError("Unsupported GGUF metadata array")
                if element == 8:
                    for _ in range(count):
                        text(False)
                else:
                    left = count * struct.calcsize("<" + formats[element])
                    while left:
                        size = min(left, 65536)
                        read(size)
                        left -= size
                value = {"array_element_type": element, "count": count, "contents_not_retained": True}
            elif kind in formats:
                value = number(formats[kind])
                if isinstance(value, float) and not math.isfinite(value):
                    raise RuntimeError("Nonfinite GGUF metadata")
            else:
                raise RuntimeError("Unsupported GGUF metadata type")
            values[key] = value
        return {"version": version, "tensor_count": tensors, "metadata_pairs": pairs,
                "metadata_bytes_read": consumed, "metadata_sha256": digest.hexdigest(),
                "values": values, "tensor_descriptors_or_data_parsed": False}


def cpu_metadata():
    records = []
    current = {}
    count = 0
    with Path("/proc/cpuinfo").open() as stream:
        for line in stream:
            count += len(line.encode())
            if count > 1024**2:
                raise RuntimeError("CPU metadata byte budget reached")
            if not line.strip():
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(":")
            if key.strip() in {"vendor_id", "model name", "cpu family", "model", "stepping", "flags"}:
                current[key.strip()] = value.strip()
    if current:
        records.append(current)
    flags = [set(item.get("flags", "").split()) for item in records]
    return {"logical_cpu_records": len(records),
            "models": sorted({item.get("model name", "unknown") for item in records}),
            "vendors": sorted({item.get("vendor_id", "unknown") for item in records}),
            "common_flags": sorted(set.intersection(*flags)) if flags else [],
            "serial_or_unrelated_fields_retained": False}


def main():
    if OUTPUT.exists():
        raise RuntimeError("Static preflight evidence exists; refuse overwrite or automatic rerun")
    started = time.monotonic()
    report = {"throwaway": True, "issue": 99, "scope": "local metadata-as-data inspection only",
              "started_utc": now(), "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "limits": {"wall_seconds": WALL_LIMIT, "cpu_seconds": CPU_LIMIT,
                         "address_space_bytes": MEMORY_LIMIT, "elf_metadata_bytes": ELF_READ_LIMIT,
                         "gguf_metadata_bytes": GGUF_READ_LIMIT},
              "package_elf": {}, "package_non_elf": [], "dependency_candidates": {}, "system_elf": {},
              "acquired_code_executed_or_loaded": False, "subprocess_started": False,
              "permissions_changed": False, "service_started": False, "inference_performed": False,
              "model_runtime_loaded": False, "network_used": False, "deletion_performed": False,
              "deployment_eligible": False, "runtime_authorized": False}
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT, CPU_LIMIT + 1))
    report["startup_memory"] = memory()

    def expired(signum, frame):
        raise TimeoutError("Static preflight wall or CPU budget reached")

    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGXCPU, expired)
    signal.setitimer(signal.ITIMER_REAL, WALL_LIMIT)
    try:
        staging_bytes = (DOCS / "ollama-containment-extraction.observations.json").read_bytes()
        acquired_bytes = (DOCS / "ollama-containment-acquisition.observations.json").read_bytes()
        report["staging_receipt_sha256"] = hashlib.sha256(staging_bytes).hexdigest()
        report["acquisition_receipt_sha256"] = hashlib.sha256(acquired_bytes).hexdigest()
        staged, acquired = json.loads(staging_bytes), json.loads(acquired_bytes)
        if not staged["success"] or not all(staged["checks"].values()) or not acquired["success"]:
            raise RuntimeError("Staging/acquisition prerequisite failed")
        root = Path(staged["directory"])
        for item in staged["files"]:
            info = elf_metadata(root / item["name"])
            if info is None:
                report["package_non_elf"].append(item["name"])
            else:
                info["staged_sha256_from_verified_receipt"] = item["sha256"]
                report["package_elf"][item["name"]] = info
        inventory = json.loads((DOCS / "ollama-containment-archive-inventory.witnessed.observations.json").read_text())
        names = {}
        for item in inventory["entries"]:
            if item["type"] in ("0", "2"):
                names.setdefault(PurePosixPath(item["name"]).name, []).append(item["name"])
        requests = set()
        for info in report["package_elf"].values():
            requests.update(info["needed"])
            if info["interpreter"]:
                requests.add(info["interpreter"])
        queue = sorted(requests)
        while queue:
            name = queue.pop(0)
            if name in report["dependency_candidates"]:
                continue
            if len(report["dependency_candidates"]) >= 256:
                raise RuntimeError("Static dependency candidate count reached")
            candidate = system_candidate(name)
            candidate["package_name_candidates"] = names.get(name, [])
            report["dependency_candidates"][name] = candidate
            canonical = candidate.get("canonical_path")
            if canonical and canonical not in report["system_elf"]:
                info = elf_metadata(Path(canonical))
                if info is None:
                    raise RuntimeError("Referenced system candidate is not ELF")
                report["system_elf"][canonical] = info
                queue.extend(info["needed"])
        weight = next(item for item in acquired["plan"] if item["kind"] == "application/vnd.ollama.image.model")
        report["gguf"] = gguf_metadata(Path(acquired["directory"]) / weight["filename"])
        small = {}
        for item in acquired["plan"]:
            if item["kind"] in ("server-archive", "application/vnd.ollama.image.model"):
                continue
            if item["size"] > 65536:
                raise RuntimeError("Unexpected small model-metadata size")
            with (Path(acquired["directory"]) / item["filename"]).open("rb") as stream:
                data = stream.read(65537)
            if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise RuntimeError("Acquired small model-metadata size/hash drift")
            small[item["kind"]] = {"sha256": item["sha256"], "bytes": len(data), "text": data.decode("utf-8")}
        report["small_model_metadata"] = small
        report["cpu"] = cpu_metadata()
        report["host_machine"] = os.uname().machine
        config = json.loads(small["application/vnd.docker.container.image.v1+json"]["text"])
        report["static_identifiers"] = {
            "expected_model_architecture": report["gguf"]["values"].get("general.architecture") == "qwen2",
            "package_machine_matches_selected_x86_64": bool(report["package_elf"]) and all(
                item["machine"] == 62 for item in report["package_elf"].values()),
            "host_matches_selected_x86_64": report["host_machine"] == "x86_64" and "lm" in report["cpu"]["common_flags"],
            "no_remote_config_route_fields": not any(
                config.get(key) for key in ("RemoteHost", "RemoteModel", "remote_host", "remote_model")),
        }
        report["not_established"] = [
            "actual loader resolution or loaded-byte identity", "dynamic dlopen closure or device probing",
            "specific compiled CPU variant/ISA compatibility", "symbol-level ABI or model inference compatibility",
            "absence of runtime offload or untracked tools", "network/worker/Provider ownership or containment",
        ]
        report["inspection_completed"] = True
    except Exception as error:
        report["inspection_completed"] = False
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        report["elapsed_seconds"] = time.monotonic() - started
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["cpu_seconds"] = usage.ru_utime + usage.ru_stime
        report["elf_metadata_bytes_read"] = READ_BYTES
        report["finished_utc"] = now()
        try:
            report["completed_memory"] = memory()
        except Exception as error:
            report["completed_memory"] = {}
            report["memory_witness_error"] = type(error).__name__
        report["checks"] = {
            "inspection_completed": report["inspection_completed"],
            "wall_within_limit": report["elapsed_seconds"] <= WALL_LIMIT,
            "cpu_within_limit": report["cpu_seconds"] <= CPU_LIMIT,
            "metadata_reads_within_limit": READ_BYTES <= ELF_READ_LIMIT,
            "startup_memory_witness": memory_ok(report["startup_memory"]),
            "completed_memory_witness": memory_ok(report["completed_memory"]),
            "static_identifiers_match_selected_candidate": bool(report.get("static_identifiers")) and all(
                report.get("static_identifiers", {}).values()),
        }
        report["success"] = all(report["checks"].values())
        with OUTPUT.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        print(json.dumps({"evidence": str(OUTPUT), "success": report["success"],
                          "failure": report.get("failure"), "checks": report["checks"],
                          "package_elf_count": len(report["package_elf"]),
                          "system_candidate_count": len(report["system_elf"]),
                          "elapsed_seconds": report["elapsed_seconds"],
                          "cpu": report.get("cpu"),
                          "gguf": {key: report.get("gguf", {}).get(key) for key in ("version", "tensor_count", "metadata_pairs", "metadata_bytes_read")},
                          "memory": report["completed_memory"]}, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
