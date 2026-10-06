"""Run inside the disposable Vagrant VM; verify persistence again after reboot.

Usage: sudo scripts/vm-run.sh python scripts/check-vm-storage.py
Then: sudo scripts/vm-run.sh python scripts/check-vm-storage.py verify BUNDLE
Creates one small Mininet topology, retained local evidence and a uniquely named
shared result bundle. It never invokes a model or replaces existing output.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml

from mininet_ai.artifacts import default_artifact_root
from mininet_ai.saved_runs import read_metadata


def verify(bundle: Path) -> None:
    import hashlib

    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["provenance"] == "managed"
    assert manifest["evidence_complete"]
    assert (bundle / "EXPORT_COMPLETE").is_file()
    for name, checksum in manifest["sha256"].items():
        assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == checksum
    for path in manifest["sources"].values():
        assert Path(path).exists(), path
        assert Path(path).stat().st_uid == 0
        assert Path(path).stat().st_mode & 0o077 == 0
    print(f"Verified persistent local evidence and bundle: {bundle}")


def run() -> None:
    assert os.geteuid() == 0, "Run this check as root inside the disposable VM"
    assert Path.cwd() == Path("/vagrant"), "Run from the shared project directory"
    assert default_artifact_root() == Path("/var/lib/mininet-ai")
    spec = yaml.safe_load(Path("examples/getting-started/experiment.yaml").read_text())
    spec["substrate"]["driver"] = "mininet-ovs"
    for key in ("blueprints", "capabilityDefinitions", "agents"):
        spec.pop(key, None)
    executable = str(Path(sys.executable).parent / "mininet-ai")
    with tempfile.TemporaryDirectory(prefix="mininet-ai-storage-smoke-") as temporary:
        path = Path(temporary) / "experiment.yaml"
        path.write_text(yaml.safe_dump(spec))
        process = subprocess.Popen(
            [executable, "run", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        directory = None
        try:
            deadline = time.monotonic() + 45
            while process.poll() is None:
                for candidate in default_artifact_root().glob("mn-*/run.json"):
                    metadata = json.loads(candidate.read_text())
                    if (
                        metadata["owner"]["pid"] == process.pid
                        and metadata["lifecycle"] == "running"
                    ):
                        directory = candidate.parent
                        break
                if directory is not None:
                    process.send_signal(signal.SIGINT)
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Mininet storage smoke startup timed out")
                time.sleep(0.1)
            stdout, stderr = process.communicate(timeout=30)
            if process.returncode:
                raise RuntimeError(stdout + stderr)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
        assert directory is not None
        metadata = read_metadata(directory, directory.name)
        assert metadata.finalized
        bundle = Path("/vagrant") / f".storage-acceptance-{uuid.uuid4().hex}"
        subprocess.run(
            [
                executable,
                "export",
                directory.name,
                "--destination",
                str(bundle),
                "--acknowledge-sensitive-data",
            ],
            check=True,
        )
        verify(bundle)
        # The mount must reject unsafe explicit live storage without deployment.
        unsafe_root = Path("/vagrant") / f".storage-unsafe-{uuid.uuid4().hex}"
        result = subprocess.run(
            [executable, "run", str(path), "--artifact-root", str(unsafe_root)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode != 0, (
            "shared mount unexpectedly supported private live storage; inspect mount suitability separately"
        )
        assert "reserve private run artifacts" in result.stderr, result.stderr
        print(
            f"Unsafe explicit shared storage refused; retained diagnostic path: {unsafe_root}"
        )
        print(f"BUNDLE={bundle}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "verify":
        verify(Path(sys.argv[2]))
    elif len(sys.argv) == 1:
        run()
    else:
        raise SystemExit("usage: check-vm-storage.py [verify BUNDLE]")
