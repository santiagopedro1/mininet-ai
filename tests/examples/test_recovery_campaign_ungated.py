"""Offline combined-policy checks through configuration and exported evidence."""

import asyncio
import json
import subprocess
import sys

import pytest

from tests.examples.test_recovery_campaign import config, run
from tests.examples.test_recovery_campaign_s2 import DirectRuntime


def combined(tmp_path, runtime, **settings):
    return run(tmp_path, runtime, treatment="combined_ungated", **settings)


@pytest.mark.parametrize("confidence", [0.0, 0.1, None, -1, 2, True, "bad"])
def test_local_success_never_escalates_on_confidence_alone(tmp_path, confidence):
    class Local(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            result["confidence"] = confidence
            return result

    evidence = combined(tmp_path, Local())
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["s1_status"] == "verified"
    assert [call["stage"] for call in evidence["model_calls"]] == ["s1"]
    assert len(evidence["actions"]) == 1
    assert evidence["handoffs"] == []
    assert evidence["messages"]["handoff_count"] == 0


@pytest.mark.parametrize(
    "choice,action_success,cause",
    [
        ("unable", True, "explicit_inability"),
        ("invented", True, "unusable_decision"),
        ("bypass", False, "action_failed"),
        ("bypass", True, "verification_failed"),
        ("no_action", True, "verification_failed"),
    ],
)
def test_failure_causes_handoff_with_local_evidence_and_shared_deadline(
    tmp_path, choice, action_success, cause
):
    class Recover(DirectRuntime):
        async def service_signal(self, since_ns):
            await self.clock.sleep_until(since_ns + 1_100_000_000)
            return {"recovered": any(isinstance(call, dict) for call in self.calls)}

    evidence = combined(
        tmp_path,
        Recover(choice=choice, action_success=action_success),
        deadline_seconds=6,
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["s1_status"] == cause
    assert [call["stage"] for call in evidence["model_calls"]] == ["s1", "s2"]
    assert sum(action["stage"] == "s1" for action in evidence["actions"]) <= 1
    handoff = evidence["handoffs"][0]
    assert handoff["cause"] == cause
    assert handoff["s1_decisions"][0]["response"]["choice"] == choice
    assert handoff["s1_actions"] == [
        a for a in evidence["actions"] if a["stage"] == "s1"
    ]
    context = evidence["model_calls"][1]["request"]
    assert context["handoff"] == handoff
    assert context["initial_snapshot"] == handoff["initial_snapshot"]
    deadline = next(
        e["deadline_ns"] for e in evidence["events"] if e["kind"] == "injection_start"
    )
    assert handoff["deadline_ns"] == deadline
    assert 0 < handoff["remaining_seconds"] < 6
    assert all(
        e["deadline_ns"] == deadline
        for e in evidence["events"]
        if e["kind"] == "s2_attempt_start"
    )
    assert not any(
        e["kind"] in {"reset_checks", "injection_complete", "fault_confirmed"}
        for e in handoff["s1_events"]
    )
    assert evidence["messages"]["handoff_count"] == 1
    assert evidence["messages"]["handoff_bytes"] > 0
    assert evidence["tokens"]["input"] == 32
    assert evidence["tokens"]["output"] == 30
    assert evidence["provenance"]["catalogue"] is not None
    assert evidence["provenance"]["s2_repair_schema"]


def test_escalated_route_v2_uses_same_route_authority_as_direct_s2(tmp_path):
    from examples.diamond_recovery import PORTS
    from examples.noms27.recovery_campaign.live import parse_port_liveness

    assert parse_port_liveness(" 1(s1-eth1): addr:00\n config: 0\n state: LIVE\n")

    class RouteCombined(DirectRuntime):
        async def diagnose(self):
            return {
                "switch": "s1",
                "ports": (
                    " 1(s1-eth1): addr:00\n config: 0\n state: LIVE\n"
                    " 2(s1-eth2): addr:00\n config: PORT_DOWN\n state: LINK_DOWN\n"
                    " 3(s1-eth3): addr:00\n config: 0\n state: LIVE\n"
                ),
                "flows": " priority=100,ip,in_port=1,nw_src=10.0.0.1,nw_dst=10.0.0.2 actions=output:2",
            }

        async def observe_network(self):
            observed = await super().observe_network()
            for name, record in observed["switches"].items():
                record["peers"] = PORTS[name]
                record["flows"] = ""
            return observed

        async def decide_s2(self, context, remaining_seconds):
            return {
                "repair": {"status": "repair", "path": ["h1", "s1", "s3", "s4", "h2"]},
                "request": context,
                "response": {"done": True},
                "tokens": {"input": 20, "output": 15, "reasoning": None},
            }

    evidence = combined(
        tmp_path,
        RouteCombined(choice="unable"),
        scenario="diamond_s1_s2_link_broader",
        s2_decision_interface="route-v2",
        deadline_seconds=10,
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["s1_status"] == "explicit_inability"
    assert [call["stage"] for call in evidence["model_calls"]] == ["s1", "s2"]
    assert evidence["handoffs"][0]["initial_snapshot"]["switch"] == "s1"
    request = evidence["model_calls"][1]["request"]
    assert request["handoff"] == evidence["handoffs"][0]
    assert request["initial_snapshot"]["ports"]["2"] is False
    assert request["s2_decision_interface"] == "route-v2"
    assert evidence["actions"][0]["selected_route"] == ["h1", "s1", "s3", "s4", "h2"]
    assert {a["switch"] for a in evidence["actions"][0]["repair"]["actions"]} == {
        "s1",
        "s3",
        "s4",
    }
    assert "path" in evidence["provenance"]["s2_repair_schema"]["properties"]


def test_s1_execution_exception_escalates(tmp_path):
    class Failed(DirectRuntime):
        async def execute(self, choice):
            raise RuntimeError("controlled action failure")

    evidence = combined(tmp_path, Failed())
    assert evidence["handoffs"][0]["cause"] == "action_failed"
    assert evidence["actions"][0]["result"]["success"] is False
    assert evidence["outcome"]["policy_status"] == "verified"


def test_stage_verification_timeout_escalates_with_remaining_budget(tmp_path):
    evidence = combined(tmp_path, DirectRuntime(), s1_cap_seconds=0.5)
    assert evidence["handoffs"][0]["cause"] == "s1_stage_timeout"
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["recovery_timeout"] is False
    assert evidence["model_calls"][1]["stage"] == "s2"
    assert evidence["measurement"]["observation_seconds"] == 8


def test_missing_confidence_and_healthy_no_action_remain_local(tmp_path):
    class Healthy(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            del result["confidence"]
            return result

    evidence = combined(tmp_path, Healthy(choice="no_action"))
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["actions"] == evidence["handoffs"] == []


def test_lost_s1_provider_response_does_not_overlap_s2(tmp_path):
    class Lost(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            raise ConnectionError("controlled missing terminal response")

    evidence = combined(tmp_path, Lost())
    assert evidence["outcome"]["policy_status"] == "escalation_uncontained"
    assert evidence["containment"]["provider_contained"] is False
    assert evidence["handoffs"] == []
    assert evidence["measurement"]["observation_seconds"] == 8


def test_incomplete_s1_terminal_evidence_denies_handoff(tmp_path):
    class Incomplete(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            result["remote_terminal"] = "unconfirmed"
            return result

    evidence = combined(tmp_path, Incomplete(choice="unable"))
    assert evidence["outcome"]["policy_status"] == "escalation_uncontained"
    assert evidence["containment"]["provider_contained"] is False
    assert evidence["handoffs"] == []


def test_combined_stage_cap_cannot_extend_the_common_budget():
    with pytest.raises(ValueError, match="S1 cap cannot exceed"):
        config(treatment="combined_ungated", s1_cap_seconds=4)


def test_s2_expiry_after_handoff_never_renews_the_deadline(tmp_path):
    evidence = combined(
        tmp_path,
        DirectRuntime(choice="unable", good_from=None),
        deadline_seconds=2,
        s1_cap_seconds=1,
    )
    assert evidence["outcome"]["policy_status"] == "timeout"
    assert evidence["outcome"]["recovery_timeout"] is True
    assert len(evidence["handoffs"]) == 1
    assert len(evidence["actions"]) == 2
    assert evidence["measurement"]["observation_seconds"] == 7


@pytest.mark.parametrize("phase", ["inference", "action"])
def test_unknown_cancelled_work_denies_unsafe_handoff(tmp_path, phase):
    class Uncontained(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            if phase == "inference":
                await asyncio.Future()
            return await super().decide(diagnostics, remaining_seconds)

        async def execute(self, choice):
            await asyncio.Future()

    evidence = combined(tmp_path, Uncontained(), s1_cap_seconds=0.5)
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "escalation_uncontained"
    assert len(evidence["model_calls"]) == 1
    assert evidence["handoffs"] == []
    assert any(
        e["kind"] == "escalation_denied" and e["reason"] == "s1_work_uncontained"
        for e in evidence["events"]
    )
    assert evidence["containment"]["next_trial_allowed"] is False


def test_expired_deadline_never_starts_s2(tmp_path):
    evidence = combined(
        tmp_path, DirectRuntime(), deadline_seconds=0.5, s1_cap_seconds=0.5
    )
    assert evidence["outcome"]["policy_status"] == "timeout"
    assert evidence["handoffs"] == []
    assert len(evidence["model_calls"]) == 1
    assert any(
        e["kind"] == "escalation_denied" and e["reason"] == "deadline_exhausted"
        for e in evidence["events"]
    )


def test_escalated_s2_keeps_three_attempt_bound_and_original_observation(tmp_path):
    evidence = combined(
        tmp_path, DirectRuntime(choice="unable", good_from=None), deadline_seconds=6
    )
    assert evidence["outcome"]["policy_status"] == "attempts_exhausted"
    assert len(evidence["model_calls"]) == 4
    assert len(evidence["actions"]) == 3
    assert evidence["measurement"]["observation_seconds"] == 11
    assert evidence["outcome"]["recovery_failure"] is True
    assert evidence["tokens"]["input"] == 72


def test_combined_configuration_check_is_network_free():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign",
            "--config",
            "examples/noms27/recovery_campaign/development-ungated.json",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    declared = json.loads(result.stdout)
    assert declared["config"]["treatment"] == "combined_ungated"
    assert declared["catalogue"]
    assert declared["permissions"]["max_attempts"] == 3
