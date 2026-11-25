"""Ticket 05: configuration-to-evidence checks, not effectiveness evidence."""

import asyncio
import json
import subprocess
import sys

import pytest

from examples.noms27.recovery_campaign.blocks import build_manifest, run_blocks


def test_manifest_reserves_splits_structural_classes_and_full_allocation():
    manifest = build_manifest()
    assert len(manifest.scenarios) == 18
    assert len({s.class_id for s in manifest.scenarios}) == 6
    assert {s.family for s in manifest.scenarios} == {"diamond", "extended_diamond"}
    assert {s.structural_check()["regime"] for s in manifest.scenarios} == {
        "local_repair_sufficient",
        "broader_repair_required",
        "no_surviving_path",
    }
    for class_id in {s.class_id for s in manifest.scenarios}:
        placements = [s for s in manifest.scenarios if s.class_id == class_id]
        assert [s.split for s in placements].count("calibration") == 1
        assert [s.split for s in placements].count("heldout") == 2
    assert set(manifest.calibration_seeds).isdisjoint(manifest.evaluation_seeds)
    allocation = manifest.allocation()
    assert allocation["primary_trials"] == 1440
    assert allocation["higher_load_trials"] == 1080
    assert allocation["calibration_repetitions_per_placement"] == 30
    assert allocation["higher_load_treatments"] == [
        "s2_only",
        "combined_ungated",
        "combined_gated",
    ]


def test_controlled_all_class_run_resets_repeats_and_replaces_whole_invalid_block(
    tmp_path,
):
    result = asyncio.run(
        run_blocks(
            build_manifest(),
            tmp_path / "campaign",
            repetitions=2,
            invalid_first="essential_measurement",
        )
    )
    assert result["evidence_kind"] == "mechanism_validation"
    assert result["status"] == "completed"
    assert len(result["blocks"]) == 13  # 12 requested + one full replacement
    original = result["blocks"][0]
    assert original["valid"] is False
    replacement = next(
        b for b in result["blocks"] if b["replaces"] == original["block_id"]
    )
    assert replacement["block_id"] != original["block_id"]
    assert replacement["matching"] == original["matching"]
    assert replacement["valid"] is True
    valid = [b for b in result["blocks"] if b["valid"]]
    assert len(valid) == 12
    assert len({tuple(b["treatment_order"]) for b in valid}) > 1
    for block in result["blocks"]:
        assert set(block["treatment_order"]) == {
            "s1_only",
            "s2_only",
            "combined_ungated",
        }
        assert len(block["trials"]) == 3
        for trial in block["trials"]:
            evidence = json.loads(
                (tmp_path / "campaign" / trial["evidence"]).read_text()
            )
            assert evidence["reset"]["fresh_context"] is True
            assert evidence["reset"]["initial_state"] is True
            assert evidence["containment"]["next_trial_allowed"] is True
            assert evidence["model_calls"]
            assert evidence["config"]["seed"] == block["matching"]["traffic_seed"]
            assert (
                evidence["config"]["environment_seed"]
                == block["matching"]["environment_seed"]
            )
            assert (
                evidence["reset"]["initial_forwarding"]
                == block["trials"][0]["reset"]["initial_forwarding"]
            )
            assert (
                evidence["scenario"]["structural_check"]["live_feasibility"]
                == "pending"
            )
            if block["matching"]["class_id"].startswith("tail_"):
                assert evidence["validity"]["valid"] is True
                assert evidence["outcome"]["recovery_failure"] is True
                assert evidence["measurement"]["censored"] is True
            elif trial["treatment"] != "s1_only" or block["matching"][
                "class_id"
            ].startswith("diamond_"):
                assert evidence["outcome"]["service_recovery"] is True
            else:
                assert evidence["outcome"]["recovery_failure"] is True
            if trial["treatment"] == "s2_only":
                assert all(call["stage"] == "s2" for call in evidence["model_calls"])
            if (
                block["matching"]["class_id"].startswith("extended_")
                and trial["treatment"] != "s1_only"
            ):
                repair = next(
                    a["repair"] for a in evidence["actions"] if a["stage"] == "s2"
                )
                assert {a["switch"] for a in repair["actions"]} == {"s1", "s3", "s5"}
            if block["matching"]["class_id"].endswith("switch"):
                fault = next(
                    e for e in evidence["events"] if e["kind"] == "injection_complete"
                )["result"]
                assert fault["fault_kind"] == "transit_switch_isolation"
                assert len(fault["disabled_links"]) >= 2
                assert fault["switch_process_stopped"] is False
                assert fault["endpoints_survive"] is True
    assert json.loads((tmp_path / "campaign/blocks.json").read_text()) == result


@pytest.mark.parametrize(
    "failure", ["injection", "initial_state", "essential_measurement"]
)
def test_demonstrated_failures_retain_originals_and_stop_at_replacement_limit(
    tmp_path, failure
):
    result = asyncio.run(
        run_blocks(
            build_manifest(),
            tmp_path / "campaign",
            repetitions=1,
            invalid_first=failure,
            max_replacements=0,
        )
    )
    assert result["status"] == "blocked_replacement_limit"
    assert len(result["blocks"]) == 1
    assert len(result["blocks"][0]["trials"]) == 3
    assert result["blocks"][0]["valid"] is False


@pytest.mark.parametrize("failure", ["model", "action"])
def test_treatment_failures_do_not_replace_matched_blocks(tmp_path, failure):
    result = asyncio.run(
        run_blocks(
            build_manifest(),
            tmp_path / "campaign",
            repetitions=1,
            invalid_first=failure,
            max_replacements=0,
        )
    )
    assert result["status"] == "completed"
    assert len(result["blocks"]) == 6
    assert all(b["valid"] for b in result["blocks"])
    first = result["blocks"][0]["trials"][0]
    evidence = json.loads((tmp_path / "campaign" / first["evidence"]).read_text())
    assert evidence["outcome"]["recovery_failure"] is True


def test_unsafe_prior_work_stops_before_next_trial(tmp_path):
    from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime

    class Uncontained(ControlledScenarioRuntime):
        async def cleanup(self):
            return {"verified": False}

    result = asyncio.run(
        run_blocks(
            build_manifest(),
            tmp_path / "campaign",
            runtime_factory=Uncontained,
        )
    )
    assert result["status"] == "blocked_containment"
    assert len(result["blocks"]) == 1
    assert len(result["blocks"][0]["trials"]) == 1
    assert result["blocks"][0]["valid"] is None
    assert result["blocks"][0]["complete"] is False
    assert result["blocks"][0]["trials"][0]["validity"]["valid"] is True


def test_frozen_manifest_rejects_overlap_allocation_drift_and_false_regime():
    from examples.noms27.recovery_campaign.blocks import Manifest

    manifest = build_manifest()
    for changes in (
        {"evaluation_seeds": manifest.calibration_seeds},
        {"primary_treatments": ["s1_only", "s2_only", "combined_ungated"]},
        {"scenarios": manifest.scenarios[1:]},
    ):
        with pytest.raises(ValueError):
            Manifest.model_validate({**manifest.model_dump(), **changes})
    scenario = manifest.scenarios[0]
    with pytest.raises(ValueError, match="structural witness"):
        type(scenario).model_validate(
            {
                **scenario.model_dump(),
                "initial_forwarding": {
                    k: v for k, v in scenario.initial_forwarding.items() if k != "s3"
                },
            }
        )


def test_renaming_a_calibration_incident_cannot_make_it_heldout():
    from examples.noms27.recovery_campaign.blocks import Manifest

    data = build_manifest().model_dump()
    scenarios = list(data["scenarios"])
    scenarios[1] = {
        **scenarios[0],
        "placement_id": "renamed-heldout",
        "split": "heldout",
    }
    data["scenarios"] = scenarios
    with pytest.raises(ValueError, match="disjoint structural placements"):
        Manifest.model_validate(data)


def test_duplicate_heldout_placement_and_repeated_seed_cannot_fill_allocation():
    from examples.noms27.recovery_campaign.blocks import Manifest

    data = build_manifest().model_dump()
    scenarios = list(data["scenarios"])
    scenarios[2] = {**scenarios[1], "placement_id": "duplicate-heldout"}
    with pytest.raises(ValueError, match="distinct structural placements"):
        Manifest.model_validate({**data, "scenarios": scenarios})
    with pytest.raises(ValueError, match="distinct traffic seeds"):
        Manifest.model_validate(
            {**data, "calibration_seeds": (1000, *data["calibration_seeds"])}
        )


def test_partial_switch_isolation_reversal_invalidates_without_restoring_service(
    tmp_path,
):
    from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime
    from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign

    scenario = next(
        s for s in build_manifest().scenarios if s.class_id == "diamond_switch"
    )

    class Reversed(ControlledScenarioRuntime):
        async def execute(self, choice):
            result = await super().execute(choice)
            self.down.remove(frozenset(self.scenario.disabled_links[-1]))
            return result

    evidence = asyncio.run(
        run_campaign(
            CampaignConfig(
                trial_id="partial-reversal",
                treatment="s1_only",
                scenario="matched_scenario",
                scenario_instance=scenario,
                seed=1000,
                deadline_seconds=6,
                s1_cap_seconds=3,
                bottleneck_mbps=9.6,
            ),
            tmp_path / "incident",
            runtime=Reversed(),
        )
    )
    assert evidence["validity"]["valid"] is False
    assert "fault_persistence_unverified_or_reversed" in evidence["validity"]["reasons"]
    assert json.loads((tmp_path / "incident/evidence.json").read_text()) == evidence


def test_manifest_cli_roundtrip_is_offline_and_preserves_future_slots(tmp_path):
    frozen = tmp_path / "manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign.blocks",
            "--write-manifest",
            str(frozen),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["allocation"]["primary_trials"] == 1440
    from examples.noms27.recovery_campaign.blocks import Manifest

    loaded = Manifest.model_validate_json(frozen.read_text())
    assert len(loaded.plan()) == 360
    assert sum(len(b["treatments"]) for b in loaded.plan(load_fraction=0.7)) == 1080
    assert all("combined_gated" in b["treatments"] for b in loaded.plan())


def test_higher_load_controlled_run_uses_reserved_baseline_and_full_observation(
    tmp_path,
):
    result = asyncio.run(
        run_blocks(
            build_manifest(),
            tmp_path / "campaign",
            repetitions=1,
            load_fraction=0.7,
        )
    )
    assert result["status"] == "completed"
    assert len(result["blocks"]) == 6
    for block in result["blocks"]:
        assert block["matching"]["load_fraction"] == 0.7
        assert set(block["treatment_order"]) == {"s2_only", "combined_ungated"}
        assert block["valid"] is True
        for trial in block["trials"]:
            evidence = json.loads(
                (tmp_path / "campaign" / trial["evidence"]).read_text()
            )
            assert evidence["measurement"]["packet_rate"] == 700
            assert evidence["measurement"]["observation_seconds"] == 11


@pytest.mark.parametrize("switch", ["invented", "h1", "p0"])
def test_matched_s2_cannot_expand_authority_or_use_isolated_switch_ports(
    tmp_path, switch
):
    from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime
    from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign

    class BadRepair(ControlledScenarioRuntime):
        async def observe_network(self):
            observation = await super().observe_network()
            observation["switches"]["invented"] = {"ports": {"1": True, "2": True}}
            observation["switches"]["h1"] = {"ports": {"1": True, "2": True}}
            return observation

        async def decide_s2(self, context, remaining_seconds):
            result = await super().decide_s2(context, remaining_seconds)
            result["repair"] = {
                "status": "repair",
                "actions": [
                    {
                        "switch": switch,
                        "in_port": 1,
                        "out_port": 2,
                    }
                ],
            }
            result["response"] = {"done": True, "repair": result["repair"]}
            return result

    scenario = next(
        s for s in build_manifest().scenarios if s.class_id == "extended_switch"
    )
    evidence = asyncio.run(
        run_campaign(
            CampaignConfig(
                trial_id="denied-repair",
                treatment="s2_only",
                scenario="matched_scenario",
                scenario_instance=scenario,
                seed=1000,
                deadline_seconds=6,
                s1_cap_seconds=3,
                bottleneck_mbps=9.6,
            ),
            tmp_path / "incident",
            runtime=BadRepair(),
        )
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["actions"] == []
    assert len(evidence["model_calls"]) == 3
    assert evidence["outcome"]["recovery_failure"] is True


def test_six_class_live_capability_boundary_and_cli_reports_no_path(tmp_path):
    """Six-class live capability exists pre-feasibility; scale approval holds later.

    Ticket five's prior boundary (six-class configs reject live purpose because
    the live adapter only accepted the original diamond incident) is replaced
    by the implemented capability: a live-purpose config is constructable only
    with its frozen scenario instance. Campaign-scale execution still requires
    the later feasibility/budget/calibration/freeze gates, and the blocks
    rehearsal keeps rejecting non-mechanism runtimes.
    """

    from examples.noms27.recovery_campaign.runner import CampaignConfig

    scenario = next(s for s in build_manifest().scenarios if s.class_id == "tail_link")
    settings = {
        "trial_id": "cut",
        "treatment": "s1_only",
        "scenario": "matched_scenario",
        "scenario_instance": scenario,
        "seed": 1000,
        "deadline_seconds": 6,
        "s1_cap_seconds": 3,
        "bottleneck_mbps": 9.6,
    }
    live = CampaignConfig.model_validate({**settings, "purpose": "live_feasibility"})
    assert live.scenario_instance == scenario
    with pytest.raises(
        ValueError, match="matched scenario requires its frozen scenario instance"
    ):
        CampaignConfig.model_validate(
            {**settings, "purpose": "live_feasibility", "scenario_instance": None}
        )
    config_file = tmp_path / "config.json"
    config_file.write_text(CampaignConfig.model_validate(settings).model_dump_json())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign",
            "--config",
            str(config_file),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["surviving_path"] == []
    assert (
        json.loads(result.stdout)["structural_check"]["regime"] == "no_surviving_path"
    )


@pytest.mark.parametrize("ingress,recovered", [(2, False), (1, True)])
def test_s2_rule_only_repairs_service_when_traffic_matches_ingress(
    tmp_path, ingress, recovered
):
    from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime
    from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign
    from examples.noms27.recovery_campaign.scenarios import Scenario

    placement = next(
        s for s in build_manifest().scenarios if s.placement_id == "diamond_link-p1"
    )
    # A controlled mechanism fixture, not a held-out effectiveness run. Both
    # s1->p0 and s1->s3 survive; only the downstream primary link is faulted.
    scenario = Scenario.model_validate(
        {
            **placement.model_dump(),
            "fault_target": ("p0", "p1"),
            "placement_id": "controlled-ingress-check",
            "split": "calibration",
        }
    )

    class WrongIngress(ControlledScenarioRuntime):
        async def decide_s2(self, context, remaining_seconds):
            repair = {
                "status": "repair",
                "actions": [
                    {"switch": "s1", "in_port": ingress, "out_port": 3},
                ],
            }
            return {
                "repair": repair,
                "request": context,
                "response": {"done": True, "repair": repair},
                "tokens": {"input": 20, "output": 30, "reasoning": None},
            }

    evidence = asyncio.run(
        run_campaign(
            CampaignConfig(
                trial_id="wrong-ingress",
                treatment="s2_only",
                scenario="matched_scenario",
                scenario_instance=scenario,
                seed=1000,
                deadline_seconds=6,
                s1_cap_seconds=3,
                bottleneck_mbps=9.6,
            ),
            tmp_path / "incident",
            runtime=WrongIngress(),
        )
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["actions"] and all(
        a["result"]["success"] for a in evidence["actions"]
    )
    assert evidence["actions"][0]["repair"]["actions"] == [
        {"switch": "s1", "in_port": ingress, "out_port": 3},
    ]
    assert evidence["outcome"]["service_recovery"] is recovered
    assert evidence["outcome"]["policy_status"] == (
        "verified" if recovered else "attempts_exhausted"
    )
    assert evidence["measurement"]["censored"] is not recovered
    if not recovered:
        assert evidence["measurement"]["disruption_seconds"] == 11
        observed_rules = evidence["model_calls"][1]["request"]["observation"][
            "switches"
        ]["s1"]["flows"]
        assert {tuple(sorted(rule.items())) for rule in observed_rules} == {
            (("in_port", 1), ("out_port", 2)),
            (("in_port", 2), ("out_port", 3)),
        }
    assert json.loads((tmp_path / "incident/evidence.json").read_text()) == evidence


@pytest.mark.parametrize("wrong_ingress", [True, False])
def test_multi_switch_repair_requires_matching_ingress_at_downstream_switch(
    tmp_path, wrong_ingress
):
    from examples.noms27.recovery_campaign.controlled import ControlledScenarioRuntime
    from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign
    from examples.noms27.recovery_campaign.scenarios import Scenario

    placement = next(
        s for s in build_manifest().scenarios if s.placement_id == "extended_link-p0"
    )
    scenario = Scenario.model_validate(
        {
            **placement.model_dump(),
            "links": (*placement.links, ("s3", "spare")),
            "placement_id": "controlled-downstream-ingress",
        }
    )

    class DownstreamIngress(ControlledScenarioRuntime):
        async def decide_s2(self, context, remaining_seconds):
            result = await super().decide_s2(context, remaining_seconds)
            if wrong_ingress:
                next(a for a in result["repair"]["actions"] if a["switch"] == "s3")[
                    "in_port"
                ] = 3
            return result

    evidence = asyncio.run(
        run_campaign(
            CampaignConfig(
                trial_id="downstream-ingress",
                treatment="s2_only",
                scenario="matched_scenario",
                scenario_instance=scenario,
                seed=1000,
                deadline_seconds=6,
                s1_cap_seconds=3,
                bottleneck_mbps=9.6,
            ),
            tmp_path / "incident",
            runtime=DownstreamIngress(),
        )
    )
    assert evidence["validity"]["valid"] is True
    assert all(a["result"]["success"] for a in evidence["actions"])
    assert {a["switch"] for a in evidence["actions"][0]["repair"]["actions"]} == {
        "s1",
        "s3",
        "s5",
    }
    assert evidence["outcome"]["service_recovery"] is not wrong_ingress
    assert evidence["measurement"]["censored"] is wrong_ingress
    assert evidence["containment"]["next_trial_allowed"] is True
