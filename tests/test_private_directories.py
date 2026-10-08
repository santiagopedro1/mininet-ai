import os
from unittest.mock import patch

import pytest

from mininet_ai.storage import PrivateStoragePathError, open_private_directory


def test_creates_private_directories_and_returns_owned_descriptor(tmp_path):
    path = tmp_path / "application" / "runs"
    descriptor = open_private_directory(path, create=True, private_levels=2)
    try:
        assert os.fstat(descriptor).st_ino == path.stat().st_ino
        assert path.stat().st_mode & 0o777 == 0o700
        assert path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        os.close(descriptor)


def test_lookup_never_creates_missing_directories(tmp_path):
    path = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        open_private_directory(path, create=False)
    assert not path.exists()


def test_rejects_public_private_parent(tmp_path):
    parent = tmp_path / "application"
    parent.mkdir(mode=0o755)
    path = parent / "runs"
    path.mkdir(mode=0o700)
    with pytest.raises(PrivateStoragePathError, match="owner-only"):
        open_private_directory(path, create=False, private_levels=2)


def test_rejects_symlink_ancestors(tmp_path):
    path = tmp_path / "link"
    path.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        open_private_directory(path / "runs", create=True)
    assert not (tmp_path / "runs").exists()


def test_rejects_wrong_owner(tmp_path):
    with (
        patch("os.geteuid", return_value=tmp_path.stat().st_uid + 1),
        pytest.raises(PrivateStoragePathError, match="current user"),
    ):
        open_private_directory(tmp_path, create=False)


def test_failed_validation_closes_all_opened_descriptors(tmp_path):
    path = tmp_path / "public"
    path.mkdir(mode=0o755)
    opened = []
    original_open = os.open

    def record_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    with (
        patch("mininet_ai.storage.os.open", side_effect=record_open),
        pytest.raises(PrivateStoragePathError),
    ):
        open_private_directory(path, create=False)
    assert len(opened) > 1
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)
