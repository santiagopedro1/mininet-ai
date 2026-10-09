from __future__ import annotations

import pytest

from mininet_ai.experiments.link_failure import TrialMeasurements


def test_phase_timings_use_failure_onset_and_observed_network_recovery():
    trial = TrialMeasurements(
        trial_id="trial-1",
        seed=7,
        failed_link="s1-s2",
        agent_ids=("switch-router@s1", "switch-router@s2"),
        started_at=100,
    )
    trial.detected("switch-router@s1", at=102)
    trial.invoked(
        "switch-router@s1",
        invocation_id="invoke-1",
        status="succeeded",
        reasoning_seconds=3,
        action_execution_seconds=0.5,
        action_started_at=106,
    )

    result = trial.finish(recovered_at=108, completed_at=109)

    assert result.status == "recovered"
    assert result.recovery_seconds == 8
    assert result.agents[0].detection_seconds == 2
    assert result.agents[0].reasoning_seconds == 3
    assert result.agents[0].action_effect_seconds == 2
    assert result.agents[0].action_execution_seconds == 0.5
    assert result.agents[1].detection_seconds is None
    assert result.agents[1].reasoning_seconds is None
    assert result.agents[1].action_effect_seconds is None


def test_timeout_preserves_partial_measurements_and_rejects_backwards_time():
    trial = TrialMeasurements(
        trial_id="trial-2",
        seed=7,
        failed_link="s1-s2",
        agent_ids=("switch-router@s1",),
        started_at=100,
    )
    with pytest.raises(ValueError, match="precedes"):
        trial.detected("switch-router@s1", at=99)
    trial.detected("switch-router@s1", at=102)
    result = trial.finish(recovered_at=None, completed_at=110)
    assert result.status == "timeout"
    assert result.recovery_seconds is None
    assert result.elapsed_seconds == 10
    assert result.agents[0].detection_seconds == 2
    assert result.agents[0].reasoning_seconds is None
    assert result.agents[0].action_effect_seconds is None


def test_reasoning_failure_does_not_report_unattempted_actions_as_zero():
    trial = TrialMeasurements(
        trial_id="failed",
        seed=7,
        failed_link="s1-s2",
        agent_ids=("a",),
        started_at=0,
    )
    trial.detected("a", at=1)
    trial.invoked(
        "a",
        invocation_id="invoke-1",
        status="failed",
        reasoning_seconds=2,
        action_execution_seconds=0,
        action_started_at=None,
    )
    result = trial.finish(recovered_at=None, completed_at=4)
    assert result.agents[0].reasoning_seconds == 2
    assert result.agents[0].action_execution_seconds is None


def test_seeded_plan_has_ten_ollama_switch_agents_and_an_alternate_route():
    from mininet_ai.experiments.link_failure import build_scenario

    scenario = build_scenario(seed=7)
    repeated = build_scenario(seed=7)
    different = build_scenario(seed=8)

    assert scenario.plan.digest == repeated.plan.digest
    assert scenario.plan.digest != different.plan.digest
    assert len(scenario.plan.agents) == 10
    assert {agent.attachment.targets[0] for agent in scenario.plan.agents} == {
        f"s{i}" for i in range(1, 11)
    }
    blueprint = scenario.plan.snapshot["blueprints"][0]
    assert blueprint["model"]["name"] == "qwen3.5:latest"
    assert blueprint["model"]["parameters"]["host"] == "http://10.10.10.152:11434"
    assert len(scenario.route("h1", "h2")) == 2
    alternate = scenario.route("h1", "h2", failed=True)
    assert len(alternate) == 10
    assert set(alternate) == {f"s{i}" for i in range(1, 11)}
    for switch in alternate:
        rules = scenario.rules(switch, failed=True)
        assert len(rules) == 4
        assert all(rule["actions"].startswith("output:") for rule in rules)


@pytest.mark.parametrize("raise_action_error", [False, True])
def test_trial_measures_recovery_without_reenabling_failed_link(raise_action_error):
    import copy

    from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
    from mininet_ai.experiments.link_failure import build_scenario, run_trial
    from mininet_ai.plugins import ProviderRegistries
    from mininet_ai.substrates import (
        ActionStatus,
        FakeSubstrateRuntime,
        ObservationResult,
        ResourceOperationalState,
    )

    scenario = build_scenario(seed=7)
    snapshot = copy.deepcopy(scenario.plan.snapshot)
    snapshot["blueprints"][0]["implementation"]["entrypoint"] = (
        "tests.experiments.factories:create_recovery_agent"
    )
    plan = scenario.plan.model_copy(update={"snapshot": snapshot})

    class ReachableSubstrate(FakeSubstrateRuntime):
        name = "mininet-ovs"

        def __init__(self):
            super().__init__()
            self.installed = set()
            self.link_down = False
            self.enabled_after_failure = False

        def inspect(self, run_id):
            snapshot = super().inspect(run_id)
            return snapshot.model_copy(
                update={
                    "resources": tuple(
                        resource.model_copy(
                            update={"state": ResourceOperationalState.DOWN}
                        )
                        if self.link_down and resource.name == scenario.failed_link
                        else resource
                        for resource in snapshot.resources
                    )
                }
            )

        def execute(self, run_id, request):
            result = super().execute(run_id, request)
            if result.status == ActionStatus.SUCCEEDED:
                if request.name == "link.disable":
                    self.link_down = True
                    self.installed.clear()
                elif request.name == "link.enable" and self.link_down:
                    self.enabled_after_failure = True
                elif request.name == "openflow.flow.install" and self.link_down:
                    self.installed.add(
                        (request.target, str(request.parameters["match"]))
                    )
            return result

        def observe(self, run_id, query):
            if query.name == "host.reachability":
                return ObservationResult(
                    run_id=run_id,
                    query=query,
                    observed_at=self.inspect(run_id).observed_at,
                    values={
                        "h1": {
                            "probes": [
                                {
                                    "reachable": not self.link_down
                                    or len(self.installed) == 40
                                }
                            ]
                        }
                    },
                )
            return super().observe(run_id, query)

    substrate = ReachableSubstrate()
    run = substrate.deploy(plan)
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)

    class FailingActions(OneShotAgentRuntime):
        def execute(self, prepared, proposal):
            raise RuntimeError("action adapter unavailable")

    runtime_type = FailingActions if raise_action_error else OneShotAgentRuntime
    agents = runtime_type(plan, substrate, registries)
    try:
        result = run_trial(
            scenario,
            substrate,
            agents,
            run_id=run.id,
            trial_id="trial-1",
            timeout_seconds=1 if raise_action_error else 10,
            poll_interval_seconds=0.01,
        )
        if raise_action_error:
            assert result.status == "timeout"
            assert all(agent.status == "failed" for agent in result.agents)
            assert all(agent.invocation_id is not None for agent in result.agents)
            assert all(agent.reasoning_seconds is not None for agent in result.agents)
            assert all(
                agent.action_started_at_seconds is not None for agent in result.agents
            )
            assert all(agent.action_effect_seconds is None for agent in result.agents)
            return
        assert result.status == "recovered", result.issue
        assert result.recovery_seconds is not None
        assert len(result.agents) == 10
        assert all(agent.detection_seconds is not None for agent in result.agents)
        assert all(agent.reasoning_seconds is not None for agent in result.agents)
        assert all(agent.action_effect_seconds is not None for agent in result.agents)
        assert not substrate.enabled_after_failure
        failed = next(
            r
            for r in substrate.inspect(run.id).resources
            if r.name == scenario.failed_link
        )
        assert failed.state == ResourceOperationalState.DOWN
    finally:
        substrate.teardown(run.id)


def test_summary_retains_timeouts_without_treating_missing_phases_as_zero():
    from mininet_ai.experiments.link_failure import summarize_trials

    recovered = TrialMeasurements(
        trial_id="success",
        seed=7,
        failed_link="s1-s2",
        agent_ids=("a", "b"),
        started_at=0,
    )
    recovered.detected("a", at=2)
    timeout = TrialMeasurements(
        trial_id="timeout",
        seed=7,
        failed_link="s1-s2",
        agent_ids=("a", "b"),
        started_at=0,
    )
    timeout.detected("a", at=4)
    summary = summarize_trials(
        (
            recovered.finish(recovered_at=8, completed_at=9),
            timeout.finish(recovered_at=None, completed_at=10),
        )
    )
    assert summary["trials"] == 2
    assert summary["statuses"] == {"recovered": 1, "timeout": 1}
    assert summary["detection_seconds"] == {
        "count": 2,
        "missing": 2,
        "mean": 3,
        "median": 3,
        "p95": 4,
    }
    assert summary["reasoning_seconds"]["count"] == 0
    assert summary["reasoning_seconds"]["mean"] is None
    assert summary["recovery_seconds"]["count"] == 1
    assert summary["recovery_seconds"]["missing"] == 1
    assert summary["recovery_seconds"]["mean"] == 8


def test_generated_specification_can_be_compiled_without_live_dependencies():
    import json
    import subprocess
    import sys

    from mininet_ai.compiler import compile_experiment
    from mininet_ai.specification.models import Experiment

    emitted = subprocess.run(
        [
            sys.executable,
            "-m",
            "mininet_ai.experiments.link_failure",
            "--seed",
            "7",
            "--spec",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    plan = compile_experiment(Experiment.model_validate(json.loads(emitted.stdout)))
    assert len(plan.agents) == 10


def test_deployment_failures_are_exported_as_unsuccessful_trials(monkeypatch, tmp_path):
    import json
    import sys

    from mininet_ai.experiments import link_failure

    class UnavailableSubstrate:
        def deploy(self, plan):
            raise RuntimeError("substrate unavailable")

    output = tmp_path / "results.jsonl"
    monkeypatch.setattr(link_failure, "MininetOVSRuntime", UnavailableSubstrate)
    monkeypatch.setattr(link_failure.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        sys, "argv", ["experiment", "--trials", "2", "--output", str(output)]
    )
    link_failure.main()
    records = [json.loads(line) for line in output.read_text().splitlines()]
    trials = [record for record in records if record["type"] == "trial"]
    assert len(trials) == 2
    assert all(record["status"] == "error" for record in trials)
    assert all("substrate unavailable" in record["issue"] for record in trials)
    assert all(record["run_id"] is None for record in trials)
    assert records[-1]["type"] == "summary"
    assert records[-1]["statuses"] == {"error": 2}
