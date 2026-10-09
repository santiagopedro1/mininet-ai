from __future__ import annotations

import copy
import importlib.util
import os
import shutil

import pytest

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.agents.agno import AgnoAgentFactory
from mininet_ai.experiments.link_failure import build_scenario, run_trial
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.substrates import MininetOVSRuntime, ResourceOperationalState


@pytest.mark.skipif(
    os.environ.get("MININET_AI_LIVE_TESTS") != "1"
    or os.geteuid() != 0
    or importlib.util.find_spec("mininet") is None
    or any(
        shutil.which(command) is None
        for command in ("ovs-vsctl", "ovs-ofctl", "ip", "ping")
    ),
    reason="requires MININET_AI_LIVE_TESTS=1, root, Mininet, and Open vSwitch",
)
def test_live_trial_recovers_connectivity_over_alternate_path():
    scenario = build_scenario(seed=7)
    snapshot = copy.deepcopy(scenario.plan.snapshot)
    snapshot["blueprints"][0]["implementation"]["entrypoint"] = (
        "tests.experiments.factories:create_recovery_agent"
    )
    plan = scenario.plan.model_copy(update={"snapshot": snapshot})
    substrate = MininetOVSRuntime()
    factory = AgnoAgentFactory()
    run = None
    try:
        run = substrate.deploy(plan)
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        agents = OneShotAgentRuntime(plan, substrate, registries, agent_factory=factory)
        result = run_trial(
            scenario,
            substrate,
            agents,
            run_id=run.id,
            trial_id="live-trial",
            timeout_seconds=60,
        )
        assert result.status == "recovered", result.model_dump_json()
        assert result.recovery_seconds is not None
        assert any(agent.action_effect_seconds is not None for agent in result.agents)
        failed = next(
            resource
            for resource in substrate.inspect(run.id).resources
            if resource.name == scenario.failed_link
        )
        assert failed.state == ResourceOperationalState.DOWN
    finally:
        try:
            factory.close()
        finally:
            if run is not None:
                substrate.teardown(run.id)
