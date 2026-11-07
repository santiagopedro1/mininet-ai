"""Six-class live capability at the configuration-to-evidence boundary.

Mechanism tests only: fake substrates model the harness seam, the original
diamond path stays untouched, and campaign-scale approval remains explicit.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import pytest

from examples.noms27.recovery_campaign import live as live_module
from examples.noms27.recovery_campaign.blocks import build_manifest
from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign
from examples.noms27.recovery_campaign.substrate import substrate_plan
from mininet_ai.errors import AgentRuntimeError
from tests.examples.test_recovery_campaign import ControlledRuntime, run


class FakeIntf:
    def __init__(self):
        self.configured_bw = None
        self.up = True

    def config(self, *, bw=None):
        self.configured_bw = bw

    def isUp(self) -> bool:
        return self.up


class FakeLink:
    def __init__(self):
        self.intf1 = FakeIntf()
        self.intf2 = FakeIntf()


class FakeScenarioHarness:
    """Records the LiveRuntime interactions without any substrate."""

    def __init__(self, scenario, root):
        self.scenario = scenario
        self.plan = substrate_plan(scenario)
        self.root = root
        self.fault_calls = 0
        self.check_calls = 0
        self.down = False
        self.links = {
            link["name"]: FakeLink() for link in self.plan["topology"]["links"]
        }
        self.net: Any = None

    def setup(self):
        self.net = "owned-substrate"
        return {
            "switches": list(self.plan["topology"]["switches"]),
            "controller": None,
            "initial_rules": len(self.plan["flows"]["initial"]),
            "preinstalled_backup_rules": len(self.plan["flows"]["backup"]),
            "table_miss": "drop",
            "scenario_placement": self.scenario.placement_id,
        }

    def readiness(self):
        return {
            "ports": {"switches": {}},
            "failModes": {
                switch: '"secure"' for switch in self.plan["topology"]["switches"]
            },
            "controllers": {switch: "" for switch in self.plan["topology"]["switches"]},
            "protocols": {},
            "linkStates": {},
            "tableMiss": "drop",
            "normalFallback": False,
        }

    def fault(self):
        self.fault_calls += 1
        self.down = True

    def check_fault(self):
        self.check_calls += 1
        self.down = True
        check = {
            "kind": self.scenario.fault_kind,
            "both_endpoints_down": True,
            "endpoints_survive": True,
        }
        if self.scenario.fault_kind == "transit_switch_isolation":
            check.update(switch_process_stopped=False, state_corruption=False)
        return check

    def inspect_flows(self):
        return {"flows": "dumped"}

    def cleanup(self):
        return {"verified": True}


def scenario_config(
    scenario,
    *,
    treatment: Literal["s1_only", "s2_only", "combined_ungated"] = "s1_only",
    purpose: Literal["mechanism_validation", "live_feasibility"] = (
        "mechanism_validation"
    ),
):
    return CampaignConfig(
        trial_id=f"six-{scenario.placement_id}-{treatment}",
        treatment=treatment,
        scenario="matched_scenario",
        scenario_instance=scenario,
        deadline_seconds=6,
        s1_cap_seconds=3,
        bottleneck_mbps=9.6,
        seed=1000,
        purpose=purpose,
    )


def all_placements() -> list:
    return list(build_manifest().scenarios)


def test_six_class_live_purpose_needs_a_frozen_scenario_instance():
    for scenario in all_placements():
        assert (
            scenario_config(scenario, purpose="live_feasibility").scenario_instance
            is not None
        )
        with pytest.raises(ValueError, match="frozen scenario instance"):
            CampaignConfig(
                trial_id="missing-instance",
                treatment="s1_only",
                scenario="matched_scenario",
                deadline_seconds=6,
                s1_cap_seconds=3,
                bottleneck_mbps=9.6,
                seed=1000,
                purpose="live_feasibility",
                scenario_instance=None,
            )


def test_matched_live_runtime_requires_matching_evidence_kind(tmp_path):
    scenario = all_placements()[0]
    with pytest.raises(ValueError, match="live_feasibility"):
        asyncio.run(
            run_campaign(
                scenario_config(scenario, purpose="live_feasibility"),
                tmp_path / "evidence",
                runtime=ControlledRuntime(),
            )
        )


def test_underlying_switch_liveness_failure_is_not_a_treatment_outcome(tmp_path):
    class IsolatedCrash(ControlledRuntime):
        async def collect(self, observation_end_ns):
            result = await super().collect(observation_end_ns)
            result["fault_checks"] = result["fault_checks"] + [
                {
                    "tick_ns": observation_end_ns,
                    "both_endpoints_down": True,
                    "switch_process_stopped": True,
                }
            ]
            return result

    evidence = run(tmp_path, IsolatedCrash())
    assert (
        "underlying_switch_failed_during_isolation" in evidence["validity"]["reasons"]
    )

    class IsolatedHealthyProcess(ControlledRuntime):
        async def collect(self, observation_end_ns):
            result = await super().collect(observation_end_ns)
            result["fault_checks"] = [
                {
                    "tick_ns": observation_end_ns,
                    "both_endpoints_down": True,
                    "switch_process_stopped": False,
                    "state_corruption": False,
                }
            ]
            return result

    evidence = run(tmp_path / "healthy", IsolatedHealthyProcess())
    assert (
        "underlying_switch_failed_during_isolation"
        not in evidence["validity"]["reasons"]
    )


def test_live_setup_scenario_uses_the_frozen_plan(tmp_path, monkeypatch):
    scenario = all_placements()[0]
    live = live_module.LiveRuntime()
    created = []
    monkeypatch.setattr(live_module.os, "geteuid", lambda: 0)

    async def fake_thread(runtime, function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(live_module.LiveRuntime, "_thread", fake_thread)

    def fake_run(arguments, capture_output=True, text=True, check=False, timeout=None):
        class Result:
            returncode = 0
            stdout = "bridge lines\n"
            stderr = ""

        return Result()

    monkeypatch.setattr(live_module.subprocess, "run", fake_run)

    async def fake_command(*parts):
        return ""

    monkeypatch.setattr(live, "_command", fake_command)

    def harness_factory(scenario_argument, root):
        harness = FakeScenarioHarness(scenario_argument, root)
        created.append((harness, root))
        return harness

    monkeypatch.setattr(live_module, "ScenarioLiveHarness", harness_factory)

    unconfigured = scenario_config(scenario, purpose="live_feasibility").model_copy(
        update={"model_name": "unconfigured"}, deep=True
    )
    with pytest.raises(ValueError, match="configure the local model"):
        asyncio.run(live.setup(unconfigured, tmp_path / "evidence"))
    assert created == []

    configured = scenario_config(scenario, purpose="live_feasibility").model_copy(
        update={"model_name": "text-fixture", "endpoint": "http://fixture.invalid"},
        deep=True,
    )

    class NoContact:
        def post_json(self, *args, **kwargs):
            raise OSError("a six-class preflight must not contact providers")

        def get_json(self, *args, **kwargs):
            raise OSError("no provider contact during warm-up")

    transport: Any = NoContact()
    live.transport = transport
    evidence_root = tmp_path / "no-contact"
    evidence_root.mkdir()
    with pytest.raises(AgentRuntimeError, match="startup service check failed"):
        asyncio.run(live.setup(configured, evidence_root))
    # Establishing the scenario substrate happened exactly once, on the frozen
    # instance; the missing provider contact aborts before a completed
    # network-manifest freeze is recorded.
    assert len(created) == 1
    assert created[0][0].scenario == scenario
    live_substrate_root = tmp_path / "no-contact" / "substrate"
    assert (live_substrate_root / "harness.jsonl").exists() is False
    assert (evidence_root / "network-manifest.json").exists() is False


def test_fault_persistence_path_records_liveness_evidence(tmp_path):
    scenario = all_placements()[1]
    live = live_module.LiveRuntime()
    harness = FakeScenarioHarness(scenario, tmp_path)
    live.harness = harness
    live.plan = harness.plan
    live.config = scenario_config(scenario)
    live.injection_ns = live.clock.now_ns()

    injection_check = asyncio.run(live._check_fault(phase="injection"))
    assert injection_check["phase"] == "injection"
    assert injection_check["both_endpoints_down"] is True
    if scenario.fault_kind == "transit_switch_isolation":
        assert injection_check["switch_process_stopped"] is False
    assert live.fault_checks == [injection_check]

    periodic_check = asyncio.run(live._check_fault(phase="periodic"))
    assert harness.check_calls == 2
    assert periodic_check["kind"] == scenario.fault_kind
    assert live.fault_checks[-1] is periodic_check


def test_inject_uses_the_scenario_fault_target(tmp_path):
    scenario = all_placements()[1]
    live = live_module.LiveRuntime()
    harness = FakeScenarioHarness(scenario, tmp_path)
    live.harness = harness
    live.plan = harness.plan
    live.config = scenario_config(scenario)
    check = asyncio.run(live.inject(live.clock.now_ns()))
    assert harness.fault_calls == 1
    assert check["both_endpoints_down"] is True
    assert live.monitor is not None
    live.monitor.cancel()
    asyncio.run(asyncio.gather(live.monitor, return_exceptions=True))


def test_live_cleanup_verifies_scenario_owned_absence(tmp_path, monkeypatch):
    scenario = all_placements()[1]
    live = live_module.LiveRuntime()
    live.config = scenario_config(scenario)
    live.plan = substrate_plan(scenario)
    live.harness = FakeScenarioHarness(scenario, tmp_path)
    substrate_net = FakeStoppedNet()
    live.harness.net = substrate_net
    live.injection_ns = live.clock.now_ns()
    live.safety_end = live.injection_ns + live.config.cleanup_budget_seconds * 10**9

    broadcasts: list[tuple[str, ...]] = []

    def fake_run(arguments, capture_output=True, text=True, check=False, timeout=None):
        class Result:
            returncode = 0
            stdout = "[]\n" if arguments[0] == "ip" else "\n"
            stderr = ""

        broadcasts.append(tuple(arguments))
        return Result()

    async def fake_thread(runtime, function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(live_module.LiveRuntime, "_thread", fake_thread)
    monkeypatch.setattr(live_module.subprocess, "run", fake_run)
    result = asyncio.run(live.cleanup())
    assert any(arguments[0] == "ip" for arguments in broadcasts)
    assert substrate_net.stopped == 1
    assert result["verified"] is True
    assert result["remaining_owned_interfaces"] == []
    assert result["remaining_owned_bridges"] == []


class FakeStoppedNet:
    def __init__(self):
        self.stopped = 0

    def stop(self):
        self.stopped += 1
