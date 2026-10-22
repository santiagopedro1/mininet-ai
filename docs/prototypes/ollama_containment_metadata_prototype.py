"""THROWAWAY GH-99 public metadata only. No server, weights or archive downloads."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
import urllib.error


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
sources = []


def metadata(url, expected_sha256=None):
    request = urllib.request.Request(url, headers={
        "User-Agent": "mininet-ai-GH99-metadata-only",
        "Accept": "application/json, application/vnd.docker.distribution.manifest.v2+json",
    })
    try:
        response = opener.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        sources.append({"url": url, "status": error.code, "body_retained": False})
        raise
    with response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise RuntimeError("Metadata exceeded the 2 MB bound")
        digest = hashlib.sha256(body).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise RuntimeError("Pinned metadata digest mismatch")
        # A registry metadata redirect can have a signed query: do not retain it.
        sources.append({
            "url": url, "status": response.status,
            "final_host": urllib.parse.urlsplit(response.url).hostname,
            "body_sha256": digest, "body_bytes": len(body),
        })
        return json.loads(body), body.decode()


release, _ = metadata("https://api.github.com/repos/ollama/ollama/releases/tags/v0.40.8")
assets = [
    {key: item.get(key) for key in ("name", "id", "size", "digest", "browser_download_url")}
    for item in release["assets"] if item["name"] == "ollama-linux-amd64.tar.zst"
]
assert len(assets) == 1
assert assets[0]["digest"] == "sha256:0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a"
manifest_digest = "a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67"
try:
    manifest, raw_manifest = metadata(
        "https://registry.ollama.ai/v2/library/qwen2.5/manifests/sha256:" + manifest_digest,
        manifest_digest,
    )
except urllib.error.HTTPError as error:
    if error.code != 404:
        raise
    # Registry digest retrieval returned 404. A mutable tag is not trusted alone:
    # require its returned raw metadata bytes to match the already pinned digest.
    manifest, raw_manifest = metadata(
        "https://registry.ollama.ai/v2/library/qwen2.5/manifests/0.5b-instruct-q4_K_M",
        manifest_digest,
    )
config_digest = manifest["config"]["digest"].removeprefix("sha256:")
config, raw_config = metadata(
    "https://registry.ollama.ai/v2/library/qwen2.5/blobs/sha256:" + config_digest,
    config_digest,
)
assert len(raw_config.encode()) == manifest["config"]["size"]
upstream_revision = "7ae557604adf67be50417f59c2c2f167def9a775"
upstream, _ = metadata(
    "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/raw/"
    + upstream_revision + "/config.json"
)
vfs = os.statvfs("/tmp/opencode")
model_bytes = sum(layer["size"] for layer in manifest["layers"]) + manifest["config"]["size"]
output = {
    "throwaway": True,
    "observed_utc": datetime.datetime.now(datetime.UTC).isoformat(),
    "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "authorization": "public metadata and /tmp/opencode capacity only",
    "release": {key: release.get(key) for key in ("id", "tag_name", "published_at", "immutable")},
    "archive": assets[0],
    "archive_bytes_downloaded": False,
    "model_candidate": "qwen2.5:0.5b-instruct-q4_K_M",
    "human_model_selection": False,
    "manifest_sha256": manifest_digest,
    "manifest": manifest,
    "raw_manifest": raw_manifest,
    "model_config": config,
    "raw_model_config": raw_config,
    "upstream_config_revision": upstream_revision,
    "upstream_config": upstream,
    "upstream_to_ollama_conversion_provenance": "not established",
    "model_config_bytes_retrieved": manifest["config"]["size"],
    "model_layer_bytes_downloaded": False,
    "model_and_config_publisher_bytes": model_bytes,
    "archive_plus_model_publisher_bytes": assets[0]["size"] + model_bytes,
    "expanded_archive_bytes": "unknown without separately authorized archive inspection",
    "scratch_capacity": {
        "path": "/tmp/opencode", "total_bytes": vfs.f_blocks * vfs.f_frsize,
        "available_bytes": vfs.f_bavail * vfs.f_frsize,
        "free_capacity_is_not_memory_or_extraction_headroom": True,
    },
    "sources": sources,
    "runtime_compatibility": "unvalidated",
    "provider_started": False, "inference_performed": False,
}
destination = Path(__file__).with_name("ollama-containment-acquisition.metadata.json")
destination.write_text(json.dumps(output, indent=2) + "\n")
print(json.dumps({
    "output": str(destination), "model_candidate": output["model_candidate"],
    "model_bytes": model_bytes,
    "archive_plus_model_bytes": output["archive_plus_model_publisher_bytes"],
    "config": config, "scratch": output["scratch_capacity"],
}, indent=2))
