"""Explicit YAML selections shared by CLI preservation tests."""

import json
from pathlib import Path


def storage_args(directory: Path, **paths: Path) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "storage.yaml"
    config.write_text(
        json.dumps({key: str(path.absolute()) for key, path in paths.items()})
    )
    return ["--storage-config", str(config)]
