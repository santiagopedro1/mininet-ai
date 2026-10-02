"""Reserve private, non-overwriting saved output independently of control sockets."""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from mininet_ai.errors import MininetAIError
from mininet_ai.storage import open_private_directory


@dataclass(frozen=True)
class RunArtifacts:
    directory: Path
    log: Path
    ledger: Path
    shared_state: Path
    agno: Path


def reserve_artifacts(
    root: Path, run_id: str, *, persistent_memory: bool = False
) -> RunArtifacts:
    root = root.absolute()
    # Disjoint namespaces prevent safe IDs colliding with mapped arbitrary IDs.
    name = (
        run_id
        if re.fullmatch(r"(?:run|mn)-[a-zA-Z0-9-]{1,100}", run_id)
        else "id-" + hashlib.sha256(run_id.encode()).hexdigest()
    )
    directory = root / name
    try:
        # The root is a container, not a private storage boundary. In particular,
        # /run/mininet-ai can also contain the independent control directory.
        descriptor = open_private_directory(root, create=True, private_levels=0)
        try:
            os.mkdir(name, 0o700, dir_fd=descriptor)
        finally:
            os.close(descriptor)
        for child in ("logs", "dbs", "artifacts"):
            descriptor = open_private_directory(
                directory / child, create=True, private_levels=2
            )
            os.close(descriptor)
        if persistent_memory:
            descriptor = open_private_directory(root / "memory", create=True)
            os.close(descriptor)
    except (OSError, MininetAIError) as error:
        raise MininetAIError(
            f"could not reserve private run artifacts at {directory}: {error}; "
            "use a new run ID and an absolute --artifact-root on a local filesystem "
            "supporting private permissions and SQLite locking (not /vagrant)"
        ) from error
    return RunArtifacts(
        directory,
        directory / "logs/run.log",
        directory / "dbs/ledger.sqlite3",
        directory / "dbs/shared-state.sqlite3",
        root / "memory/agno.sqlite3"
        if persistent_memory
        else directory / "dbs/agno.sqlite3",
    )
