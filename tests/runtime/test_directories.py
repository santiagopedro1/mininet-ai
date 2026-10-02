import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mininet_ai.runtime.control import IntentControlError, resolve_control_directory


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
