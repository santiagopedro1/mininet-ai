"""Privilege and remote-isolation guards never need a live network or model."""

import argparse
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(
        str(Path(__file__).parents[2] / "examples/diamond-recovery")
    )
    return importlib.import_module("live_diagnosis")


def test_unprivileged_runner_refuses_before_network_or_inference(
    runner, monkeypatch, tmp_path
):
    monkeypatch.setattr(runner.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        runner,
        "HttpxJsonTransport",
        lambda: pytest.fail("service contacted without root"),
    )
    with pytest.raises(PermissionError, match="no inference was sent"):
        runner.run(argparse.Namespace(evidence=tmp_path / "must-not-exist"))
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.parametrize(
    "slots", [[], {}, [{"is_processing": True}], [{"is_processing": None}]]
)
def test_unverified_or_busy_remote_blocks_trial(runner, monkeypatch, tmp_path, slots):
    class Transport:
        def get_json(self, *args, **kwargs):
            return slots

    monkeypatch.setattr(runner, "HttpxJsonTransport", Transport)
    with pytest.raises(RuntimeError, match="no trial started"):
        runner.require_idle("http://localhost", tmp_path / "status.json")
    assert (tmp_path / "status.json").exists()


def test_idle_service_is_retained_with_observation_time(runner, monkeypatch, tmp_path):
    import json

    class Transport:
        def get_json(self, *args, **kwargs):
            return [{"id": 0, "is_processing": False}]

    monkeypatch.setattr(runner, "HttpxJsonTransport", Transport)
    runner.require_idle("http://localhost", tmp_path / "status.json")
    evidence = json.loads((tmp_path / "status.json").read_text())
    assert evidence["observedAt"]
    assert evidence["slots"][0]["is_processing"] is False
