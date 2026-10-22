"""THROWAWAY GH-99: explicitly authorized, bounded acquisition only. NEVER MERGE.

No archive extraction, acquired-code execution, model loading or Provider calls.
Each invocation needs new human authorization; never retries or resumes downloads.
"""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


MAX_BYTES = 2 * 1024**3
MAX_SECONDS = 20 * 60
CHUNK_BYTES = 64 * 1024
START = time.monotonic()
DEADLINE = START + MAX_SECONDS
ALLOWED_HOSTS = {
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
    "registry.ollama.ai", "dd20bb891979d25aebc8bec07b2b3bbc.r2.cloudflarestorage.com",
}
REPO = Path(__file__).resolve().parents[2]
METADATA = REPO / "docs/prototypes/ollama-containment-acquisition.metadata.json"
EVIDENCE = REPO / "docs/prototypes/ollama-containment-acquisition.observations.json"


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def check_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise RuntimeError("Unapproved HTTPS acquisition endpoint")
    return parsed.hostname


class Redirects(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, request, response, code, message, headers):
        # Do not drain arbitrary redirect response bodies into memory or budget.
        destination = urllib.parse.urljoin(request.full_url, headers.get("Location", ""))
        count = getattr(request, "gh99_redirect_count", 0) + 1
        try:
            check_url(destination)
            if count > 5:
                raise RuntimeError("Acquisition redirect limit exceeded")
            redirected = urllib.request.Request(destination, headers=dict(request.headers))
            redirected.gh99_redirect_count = count
        finally:
            response.close()
        return self.parent.open(redirected, timeout=request.timeout)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def exclusive_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main():
    if EVIDENCE.exists():
        raise RuntimeError("Acquisition evidence already exists; refuse overwrite or rerun")
    metadata_bytes = METADATA.read_bytes()
    meta = json.loads(metadata_bytes)
    manifest_bytes = meta["raw_manifest"].encode()
    manifest_digest = "a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67"
    archive_digest = "0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a"
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_digest:
        raise RuntimeError("Pinned manifest metadata changed")
    archive = meta["archive"]
    if archive["digest"] != "sha256:" + archive_digest or archive["size"] != 1505219659:
        raise RuntimeError("Pinned server descriptor changed")
    manifest = json.loads(manifest_bytes)
    objects = [{
        "kind": "manifest", "filename": "model-manifest.json.payload",
        "url": "https://registry.ollama.ai/v2/library/qwen2.5/manifests/0.5b-instruct-q4_K_M",
        "sha256": manifest_digest, "size": len(manifest_bytes),
    }]
    for descriptor in [manifest["config"], *manifest["layers"]]:
        digest = descriptor["digest"].removeprefix("sha256:")
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise RuntimeError("Invalid pinned model descriptor")
        objects.append({
            "kind": descriptor["mediaType"], "filename": "sha256-" + digest + ".payload",
            "url": "https://registry.ollama.ai/v2/library/qwen2.5/blobs/sha256:" + digest,
            "sha256": digest, "size": descriptor["size"],
        })
    objects.append({
        "kind": "server-archive", "filename": "ollama-linux-amd64.tar.zst.payload",
        "url": archive["browser_download_url"], "sha256": archive_digest,
        "size": archive["size"],
    })
    if sum(item["size"] for item in objects) >= MAX_BYTES:
        raise RuntimeError("Pinned payload exceeds authorization")
    for item in objects:
        check_url(item["url"])
    artifact_root = REPO / ".prototype-gh99-artifacts"
    try:
        artifact_root.mkdir(mode=0o700)
    except FileExistsError:
        attributes = artifact_root.lstat()
        if (not stat.S_ISDIR(attributes.st_mode) or attributes.st_uid != os.getuid()
                or attributes.st_mode & 0o077):
            raise RuntimeError("Existing artifact root is not an owned private directory") from None
    root = artifact_root / ("acquisition-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    report = {
        "throwaway": True, "issue": 99, "scope": "download and hash verification only",
        "started_utc": now(), "directory": str(root),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "input_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "limits": {"downloaded_body_bytes": MAX_BYTES, "elapsed_seconds": MAX_SECONDS,
                   "buffer_bytes": CHUNK_BYTES, "retries": 0, "resume": False},
        "plan": objects, "objects": [], "downloaded_body_bytes": 0,
        "archive_extracted": False, "acquired_code_executed": False,
        "service_started": False, "model_loaded": False, "inference_performed": False,
    }
    exclusive_json(root / "plan.json", report)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), Redirects())
    journal_fd = os.open(root / "events.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    active = None
    active_hash = None

    def event(value):
        # Only our generated private file, append-only acquisition observations.
        encoded = (json.dumps(value) + "\n").encode()
        view = memoryview(encoded)
        while view:
            count = os.write(journal_fd, view)
            view = view[count:]
        os.fsync(journal_fd)

    def expired(signum, frame):
        raise TimeoutError("Authorized total acquisition deadline reached")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, DEADLINE - time.monotonic()))
    try:
        for item in objects:
            remaining = DEADLINE - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Authorized total acquisition deadline reached")
            active = {"filename": item["filename"], "kind": item["kind"],
                      "expected_size": item["size"], "expected_sha256": item["sha256"],
                      "received_bytes": 0, "written_bytes": 0, "verified": False}
            active_hash = hashlib.sha256()
            report["objects"].append(active)
            event({"event": "intent", "utc": now(), "object": active.copy()})
            fd = os.open(root / item["filename"], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as destination:
                request = urllib.request.Request(item["url"], headers={
                    "User-Agent": "mininet-ai-GH99-authorized-acquisition-only",
                    "Accept-Encoding": "identity",
                })
                with opener.open(request, timeout=min(30, remaining)) as response:
                    active["http_status"] = response.status
                    active["final_host"] = check_url(response.url)
                    if response.status != 200:
                        raise RuntimeError("Unexpected HTTP response status")
                    if response.headers.get("Content-Encoding", "identity") != "identity":
                        raise RuntimeError("Unexpected content encoding")
                    length = response.headers.get("Content-Length")
                    active["http_content_length"] = length
                    if length is not None and int(length) != item["size"]:
                        raise RuntimeError("Publisher content length differs from pinned size")
                    while True:
                        left = MAX_BYTES - report["downloaded_body_bytes"]
                        if left <= 0:
                            raise RuntimeError("Authorized total payload budget reached")
                        # Read at most one excess byte to detect incorrect framing/size.
                        chunk = response.read(min(CHUNK_BYTES, left, item["size"] - active["received_bytes"] + 1))
                        if not chunk:
                            break
                        report["downloaded_body_bytes"] += len(chunk)
                        active["received_bytes"] += len(chunk)
                        if active["received_bytes"] > item["size"]:
                            raise RuntimeError("Response exceeds pinned object size")
                        destination.write(chunk)
                        active["written_bytes"] += len(chunk)
                        active_hash.update(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            active["observed_sha256"] = active_hash.hexdigest()
            if active["written_bytes"] != item["size"] or active_hash.hexdigest() != item["sha256"]:
                raise RuntimeError("Acquired object size or SHA-256 mismatch")
            active["verified"] = True
            event({"event": "verified", "utc": now(), "object": active.copy()})
            print(json.dumps({"verified": item["kind"], "bytes": active["written_bytes"],
                              "elapsed_seconds": time.monotonic() - START}), flush=True)
        if time.monotonic() > DEADLINE:
            raise TimeoutError("Authorized total acquisition deadline reached")
        report["success"] = True
    except Exception as error:
        report["success"] = False
        # Avoid serializing HTTP exception strings that can contain signed URLs.
        report["failure"] = {"type": type(error).__name__}
        if isinstance(error, urllib.error.HTTPError):
            report["failure"]["http_code"] = error.code
        elif isinstance(error, (RuntimeError, TimeoutError)):
            report["failure"]["message"] = str(error)
        if active_hash is not None:
            active["partial_buffer_sha256"] = active_hash.hexdigest()
        event({"event": "failed", "utc": now(), "failure": report["failure"], "object": active})
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        os.close(journal_fd)
        report["finished_utc"] = now()
        report["elapsed_seconds"] = time.monotonic() - START
        report["all_expected_objects_verified"] = (
            len(report["objects"]) == len(objects) and all(item["verified"] for item in report["objects"]))
        exclusive_json(root / "receipt.json", report)
        exclusive_json(EVIDENCE, report)
        print(json.dumps({"receipt": str(root / "receipt.json"), "evidence": str(EVIDENCE),
                          "success": report.get("success"), "failure": report.get("failure")}), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 1:
        raise SystemExit("No arguments accepted; each run requires separate human authorization")
    raise SystemExit(main())
