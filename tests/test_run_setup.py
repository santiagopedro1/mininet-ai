from unittest.mock import patch

from mininet_ai.run_setup import reserve_run
from mininet_ai.substrates import MininetOVSRuntime
from mininet_ai.substrates.builtin_runtimes import register_builtin_runtimes
from mininet_ai.substrates.runtime_registry import RuntimeRegistry
from tests.agents.test_runtime import configured_plan


def test_fake_reserved_identity_is_deployed():
    run_id, runtime = reserve_run("fake")
    assert run_id is not None
    run = runtime.deploy(configured_plan({"message": "handled"}))
    assert run.id == run_id
    runtime.teardown(run_id)


def test_mininet_constructor_receives_reserved_identity():
    registry = RuntimeRegistry()
    with (
        patch(
            "mininet_ai.substrates.builtin_runtimes.MininetOVSRuntime",
            wraps=MininetOVSRuntime,
        ) as constructor,
        patch("mininet_ai.run_setup.runtime_registry", registry),
    ):
        register_builtin_runtimes(registry)
        run_id, runtime = reserve_run("mininet-ovs")
        assert constructor.call_args.kwargs["run_id_factory"]() == run_id
        assert isinstance(runtime, MininetOVSRuntime)


def test_reserved_mininet_identity_is_deployed(tmp_path):
    from mininet_ai.substrates.mininet_ovs.state import RunStateStore
    from tests.substrates.test_mininet_ovs_runtime import (
        RecordingActions,
        RecordingObservations,
        bindings,
        mininet_plan,
    )

    def construct(**kwargs):
        return MininetOVSRuntime(
            **kwargs,
            bindings_factory=bindings,
            state_store=RunStateStore(tmp_path / "state", tmp_path / "runtime.lock"),
            observation_factory=RecordingObservations,
            action_factory=RecordingActions,
        )

    registry = RuntimeRegistry()
    with (
        patch(
            "mininet_ai.substrates.builtin_runtimes.MininetOVSRuntime",
            side_effect=construct,
        ),
        patch("mininet_ai.run_setup.runtime_registry", registry),
    ):
        register_builtin_runtimes(registry)
        run_id, runtime = reserve_run("mininet-ovs")
    assert runtime.deploy(mininet_plan()).id == run_id
    assert run_id is not None
    runtime.teardown(run_id)
