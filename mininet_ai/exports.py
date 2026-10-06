"""Explicit, non-overwriting result bundles from stable private evidence."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mininet_ai.artifacts import run_directory
from mininet_ai.errors import MininetAIError
from mininet_ai.runtime.ledger import LedgerRecord, RunManifest
from mininet_ai.saved_runs import (
    SourceLocks,
    absolute,
    identity,
    open_source,
    read_metadata,
)
from mininet_ai.storage import open_private_directory


class ExportError(MininetAIError):
    """An unsafe, busy, invalid or partially published result export."""


def _json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    path.chmod(0o644)


def _database(path: Path, role: str, run_id: str) -> dict[str, Any]:
    # Read-only connections never initialize missing stores or change schemas.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError(f"unsupported {role} database schema at {path}")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError(f"corrupt {role} database at {path}")
        if role == "ledger":
            row = connection.execute(
                "SELECT manifest_json FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            manifest = (
                RunManifest.model_validate_json(row[0]).model_dump(
                    mode="json", by_alias=True
                )
                if row
                else None
            )
            records = [
                LedgerRecord.model_validate_json(row[0]).model_dump(
                    mode="json", by_alias=True
                )
                for row in connection.execute(
                    "SELECT record_json FROM records WHERE run_id=? ORDER BY sequence",
                    (run_id,),
                )
            ]
            if manifest is not None and manifest["runId"] != run_id:
                raise ValueError("ledger manifest identity mismatch")
            if any(
                record["runId"] != run_id or record["sequence"] != index
                for index, record in enumerate(records, 1)
            ):
                raise ValueError("ledger record identity/sequence mismatch")
            return {
                "schema_version": 1,
                "run_id": run_id,
                "manifest": manifest,
                "records": records,
            }
        prefix = f"run:{run_id}:deployment:"
        rows = connection.execute(
            "SELECT namespace, scope, key, value_json, version, deleted, updated_by, updated_at FROM shared_state "
            "WHERE namespace=? OR substr(namespace,1,?)=? ORDER BY namespace,key",
            (f"run:{run_id}", len(prefix), prefix),
        )
        return {
            "schema_version": 1,
            "run_id": run_id,
            "entries": [
                {
                    "namespace": row[0],
                    "scope": row[1],
                    "key": row[2],
                    "value": json.loads(row[3]) if row[3] is not None else None,
                    "version": row[4],
                    "deleted": bool(row[5]),
                    "updated_by": row[6],
                    "updated_at": row[7],
                }
                for row in rows
            ],
        }


def _artifact_sources(source: Path) -> list[Path]:
    paths = []
    for path in sorted(source.iterdir()):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            continue
        fd = open_source(path)
        try:
            paths.append(path)
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                paths.extend(_artifact_sources(path))
        finally:
            os.close(fd)
    return paths


def _lock_paths(sources: dict[str, Path]) -> list[Path]:
    return [
        *sources.values(),
        *(_artifact_sources(sources["artifacts"]) if "artifacts" in sources else []),
    ]


def _copy_artifacts(
    source: Path, target: Path, excluded: list[str], locks: SourceLocks
) -> None:
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    for path in sorted(source.iterdir()):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            excluded.append(str(path))
            continue
        try:
            fd = locks.descriptor(path)
        except KeyError as error:
            raise ValueError(f"artifact tree changed during export: {path}") from error
        locks.verify()
        if stat.S_ISDIR(metadata.st_mode):
            _copy_artifacts(path, target / path.name, excluded, locks)
        elif stat.S_ISREG(metadata.st_mode):
            with os.fdopen(os.dup(fd), "rb") as stream:
                content = stream.read()
            if content.startswith(b"SQLite format 3\x00"):
                excluded.append(str(path))
                continue
            output = target / path.name
            output.write_bytes(content)
            output.chmod(0o644)


def _check_destination(destination: Path, trees: list[Path]) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"export destination already exists: {destination}")
    for source in trees:
        if destination.is_relative_to(absolute(source)):
            raise ValueError(f"export destination is inside source tree {source}")
        source_identity = (source.stat().st_dev, source.stat().st_ino)
        for parent in destination.parents:
            if (
                parent.exists()
                and (parent.stat().st_dev, parent.stat().st_ino) == source_identity
            ):
                raise ValueError("export destination aliases a source tree")


def _publish_tree(source: int, destination: int) -> None:
    """Copy across filesystems without following or overwriting destination entries."""
    for name in sorted(os.listdir(source)):
        metadata = os.stat(name, dir_fd=source, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            os.mkdir(name, 0o755, dir_fd=destination)
            source_child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source
            )
            destination_child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination
            )
            try:
                _publish_tree(source_child, destination_child)
                os.fchmod(destination_child, 0o755)
            finally:
                os.close(source_child)
                os.close(destination_child)
        elif stat.S_ISREG(metadata.st_mode):
            read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source)
            try:
                write_fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=destination,
                )
                with (
                    os.fdopen(write_fd, "wb") as output,
                    os.fdopen(os.dup(read_fd), "rb") as input_stream,
                ):
                    shutil.copyfileobj(input_stream, output)
                    output.flush()
                    os.fchmod(output.fileno(), 0o644)
                    os.fsync(output.fileno())
            finally:
                os.close(read_fd)
        else:
            raise ValueError(f"unsafe staging entry: {name}")


@dataclass(frozen=True)
class _Publication:
    destination: Path
    directory_identity: tuple[int, int]
    hashes: dict[str, str]
    complete: bool


def _certify(publication: _Publication) -> bool:
    """Certify a frozen bundle only after all source contexts verified/released."""
    parent = open_private_directory(
        publication.destination.parent, create=False, private_levels=0
    )
    final_fd: int | None = None
    created_marker = False
    try:
        final_fd = os.open(
            publication.destination.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent,
        )
        if identity(final_fd) != publication.directory_identity:
            raise ValueError("published export directory was replaced")
        for name, checksum in publication.hashes.items():
            directory = os.dup(final_fd)
            try:
                parts = Path(name).parts
                for part in parts[:-1]:
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                    os.close(directory)
                    directory = child
                fd = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory,
                )
                with os.fdopen(fd, "rb") as stream:
                    if (
                        not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
                        or hashlib.sha256(stream.read()).hexdigest() != checksum
                    ):
                        raise ValueError(f"published checksum/type mismatch: {name}")
            finally:
                os.close(directory)
        marker = os.open(
            "EXPORT_COMPLETE",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=final_fd,
        )
        created_marker = True
        with os.fdopen(marker, "w") as stream:
            stream.write("export-format-v1\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        try:
            os.fsync(final_fd)
        except OSError as error:
            # VirtualBox lacks directory fsync; no crash durability is promised.
            if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
        return publication.complete
    except OSError, ValueError:
        if created_marker and final_fd is not None:
            os.unlink("EXPORT_COMPLETE", dir_fd=final_fd)
        raise
    finally:
        if final_fd is not None:
            os.close(final_fd)
        os.close(parent)


def _bundle(
    run_id: str,
    sources: dict[str, Path],
    destination: Path,
    locks: SourceLocks,
    *,
    provenance: str,
    outcome: str,
    missing: list[str],
    trees: list[Path],
) -> _Publication:
    destination = absolute(destination)
    _check_destination(destination, trees)
    parent = open_private_directory(destination.parent, create=False, private_levels=0)
    partial = locks.namespace / f"export-{uuid.uuid4().hex}.partial"
    published = False
    final_fd: int | None = None
    try:
        partial.mkdir(mode=0o700)
        stage_fd = open_private_directory(partial, create=False)
        try:
            # Live/private staging stays local. Shared destinations need not
            # support owner-only permissions or SQLite locking.
            stage = Path(f"/proc/self/fd/{stage_fd}")
            excluded: list[str] = []
            for role, path in sources.items():
                locks.verify()
                metadata = os.fstat(locks.descriptor(path))
                if role == "artifacts":
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise ValueError(f"artifacts source is not a directory: {path}")
                    _copy_artifacts(path, stage / "artifacts", excluded, locks)
                elif not stat.S_ISREG(metadata.st_mode):
                    raise ValueError(f"{role} source is not a regular file: {path}")
                elif role in {"ledger", "shared_state"}:
                    value = _database(path, role, run_id)
                    if role == "ledger" and value["manifest"] is None:
                        missing.append("ledger_run")
                    _json(
                        stage
                        / ("ledger.json" if role == "ledger" else "shared-state.json"),
                        value,
                    )
                elif role == "log":
                    with os.fdopen(os.dup(locks.descriptor(path)), "rb") as stream:
                        content = stream.read()
                    if content.startswith(b"SQLite format 3\x00"):
                        raise ValueError(
                            f"refusing raw SQLite database as log source: {path}"
                        )
                    if provenance == "managed":
                        prefix = re.compile(
                            rb"^\S+ \S+ (?:INFO|ERROR) run_id="
                            + re.escape(json.dumps(run_id).encode())
                            + rb" "
                        )
                        content = b"".join(
                            line
                            for line in content.splitlines(keepends=True)
                            if prefix.match(line)
                        )
                    (stage / "run.log").write_bytes(content)
                    (stage / "run.log").chmod(0o644)
            locks.verify()
            inventory = {
                str(path.relative_to(stage)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(stage.rglob("*"))
                if path.is_file()
            }
            _json(
                stage / "manifest.json",
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "outcome": outcome,
                    "provenance": provenance,
                    "evidence_complete": not missing,
                    "missing_sources": sorted(set(missing)),
                    "excluded_sources": excluded,
                    "sources": {role: str(path) for role, path in sources.items()},
                    "sha256": inventory,
                },
            )
            # Exclusive mkdir reserves the final name, unlike rename which can
            # overwrite a concurrently-created empty directory. An interrupted
            # publication has no completion marker and is never reused.
            os.mkdir(destination.name, 0o755, dir_fd=parent)
            final_fd = os.open(
                destination.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            published = True
            _publish_tree(stage_fd, final_fd)
            os.fchmod(final_fd, 0o755)
            final = Path(f"/proc/self/fd/{final_fd}")
            for name, checksum in inventory.items():
                if hashlib.sha256((final / name).read_bytes()).hexdigest() != checksum:
                    raise ValueError(f"published checksum mismatch: {name}")
            locks.verify()
            opened_parent = open_private_directory(
                destination.parent, create=False, private_levels=0
            )
            try:
                if identity(opened_parent) != identity(parent):
                    raise ValueError("export destination parent was replaced")
                entry = os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
                if (entry.st_dev, entry.st_ino) != identity(final_fd):
                    raise ValueError("export destination was replaced")
            finally:
                os.close(opened_parent)
            publication = _Publication(
                destination,
                identity(final_fd),
                {
                    **inventory,
                    "manifest.json": hashlib.sha256(
                        (stage / "manifest.json").read_bytes()
                    ).hexdigest(),
                },
                not missing,
            )
        finally:
            os.close(stage_fd)
        shutil.rmtree(partial)
        return publication
    except (OSError, ValueError, sqlite3.Error) as error:
        if final_fd is not None:
            try:
                os.unlink("EXPORT_COMPLETE", dir_fd=final_fd)
            except FileNotFoundError:
                pass
        retained = f"{partial}" + (f" and {destination}" if published else "")
        raise ExportError(
            f"export failed: {error}; incomplete output may be retained at {retained}"
        ) from error
    finally:
        if final_fd is not None:
            os.close(final_fd)
        os.close(parent)


def export_managed(root: Path, run_id: str, destination: Path) -> bool:
    directory = run_directory(root, run_id)
    publication = None
    try:
        with SourceLocks([directory], writer=False):
            metadata = read_metadata(directory, run_id)
            if not metadata.finalized or not metadata.managed_writers_verified:
                raise ValueError(
                    "run is not safely finalized or has untracked writers; verify cleanup and prepare private offline snapshots"
                )
            sources: dict[str, Path] = {}
            missing: list[str] = []
            for role in ("ledger", "shared_state", "log", "artifacts"):
                path = Path(metadata.sources[role])
                try:
                    fd = open_source(path)
                except FileNotFoundError:
                    # A missing ancestor is unsafe/unknown, not absent evidence.
                    parent = open_private_directory(
                        path.parent, create=False, private_levels=0
                    )
                    os.close(parent)
                    missing.append(role)
                    continue
                try:
                    if metadata.source_identities.get(role) != identity(fd):
                        raise ValueError(f"recorded source identity changed: {path}")
                finally:
                    os.close(fd)
                sources[role] = path
            with SourceLocks(_lock_paths(sources), writer=False) as locks:
                for role, path in sources.items():
                    if metadata.source_identities.get(role) != identity(
                        locks.descriptor(path)
                    ):
                        raise ValueError(
                            f"source changed while acquiring locks: {path}"
                        )
                publication = _bundle(
                    run_id,
                    sources,
                    destination,
                    locks,
                    provenance="managed",
                    outcome=metadata.outcome,
                    missing=missing,
                    trees=[
                        directory,
                        *[
                            path
                            for role, path in sources.items()
                            if role == "artifacts"
                        ],
                    ],
                )
        return _certify(publication)
    except (OSError, ValueError, sqlite3.Error) as error:
        retained = (
            f"; unmarked incomplete bundle retained at {publication.destination}"
            if publication is not None
            else ""
        )
        raise ExportError(f"cannot export run {run_id}: {error}{retained}") from error


def export_offline(run_id: str, sources: dict[str, Path], destination: Path) -> bool:
    """Read explicitly operator-prepared snapshots; never infer live quiescence."""
    if not sources:
        raise ExportError("offline export requires at least one explicit source")
    sources = {role: absolute(path) for role, path in sources.items()}
    missing = sorted({"ledger", "shared_state", "log", "artifacts"} - sources.keys())
    publication = None
    try:
        with SourceLocks(_lock_paths(sources), writer=False) as locks:
            publication = _bundle(
                run_id,
                sources,
                destination,
                locks,
                provenance="operator-supplied-offline",
                outcome="unknown",
                missing=missing,
                trees=[path for role, path in sources.items() if role == "artifacts"],
            )
        return _certify(publication)
    except (OSError, ValueError, sqlite3.Error) as error:
        retained = (
            f"; unmarked incomplete bundle retained at {publication.destination}"
            if publication is not None
            else ""
        )
        raise ExportError(
            f"cannot export offline snapshots for {run_id}: {error}{retained}"
        ) from error
