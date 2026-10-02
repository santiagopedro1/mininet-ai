import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mininet_ai.runtime.control import (
    IntentControlError,
    _private_directory,
    resolve_control_directory,
)


def test_root_ignores_xdg():
    with (
        patch("os.geteuid", return_value=0),
        patch.dict(os.environ, {"XDG_RUNTIME_DIR": "bad"}),
    ):
        assert resolve_control_directory() == Path("/run/mininet-ai/control")


def test_fallback_ignores_tmpdir():
    with (
        patch("os.geteuid", return_value=123),
        patch.dict(os.environ, {"TMPDIR": "/vagrant"}, clear=True),
    ):
        assert resolve_control_directory() == Path("/tmp/mininet-ai-123/control")


def test_invalid_xdg():
    with (
        patch("os.geteuid", return_value=123),
        patch.dict(os.environ, {"XDG_RUNTIME_DIR": "relative"}),
        pytest.raises(IntentControlError, match="absolute"),
    ):
        resolve_control_directory()


def test_valid_xdg(tmp_path):
    with (
        patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(tmp_path)}),
        patch("os.geteuid", return_value=tmp_path.stat().st_uid),
    ):
        if os.geteuid() != 0:
            assert resolve_control_directory() == tmp_path / "mininet-ai/control"


def test_relative_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_control_directory(Path("control")) == tmp_path / "control"


def test_wrong_owner_is_rejected_without_chown(tmp_path):
    with (
        patch("os.geteuid", return_value=tmp_path.stat().st_uid + 1),
        pytest.raises(IntentControlError, match="current user"),
    ):
        _private_directory(tmp_path, create=False)


def test_shared_storage_errors_keep_control_permission_code(tmp_path):
    directory = tmp_path / "public"
    directory.mkdir(mode=0o755)
    with pytest.raises(IntentControlError) as error:
        _private_directory(directory, create=False)
    assert error.value.code == "runtime.control.permissions"


def test_public_and_symlink_xdg_fail_without_fallback(tmp_path):
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    for path in (public, link, tmp_path / "missing"):
        with (
            patch("os.geteuid", return_value=tmp_path.stat().st_uid),
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(path)}),
        ):
            if os.geteuid() != 0:
                with pytest.raises(IntentControlError, match="fix or unset"):
                    resolve_control_directory()
