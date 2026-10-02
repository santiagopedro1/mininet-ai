from unittest.mock import patch

from mininet_ai.run_setup import reserve_run
from tests.agents.test_runtime import configured_plan


def test_fake_reserved_identity_is_deployed():
    run_id, runtime = reserve_run("fake")
    run = runtime.deploy(configured_plan({"message": "handled"}))
    assert run.id == run_id
    runtime.teardown(run_id)


def test_mininet_constructor_receives_reserved_identity():
    with patch("mininet_ai.run_setup.MininetOVSRuntime") as constructor:
        run_id, runtime = reserve_run("mininet-ovs")
        assert constructor.call_args.kwargs["run_id_factory"]() == run_id
        assert runtime is constructor.return_value
