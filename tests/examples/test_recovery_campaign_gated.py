"""Confidence policy mechanisms at the configuration → exported-evidence seam."""

import asyncio
import json
import math
import subprocess
import sys

import pytest

from tests.examples.test_recovery_campaign import config, run
from tests.examples.test_recovery_campaign_s2 import DirectRuntime


def test_low_confidence_skips_correct_local_repair_before_handoff(tmp_path):
    evidence = run(
        tmp_path,
        DirectRuntime(),
        treatment="combined_gated",
        confidence_gate_enabled=True,
        confidence_threshold=0.5,
    )
    gate = next(e for e in evidence["events"] if e["kind"] == "confidence_gate")
    assert gate["decision"] == "escalate"
    assert gate["confidence_status"] == "valid"
    assert gate["confidence"] == 0.1
    assert gate["selected_choice"] == "bypass"
    assert gate["local_execution_skipped"] is True
    assert gate["cause"] == "confidence_below_threshold"
    assert 0 < gate["remaining_seconds"] < 3
    assert all(a["stage"] == "s2" for a in evidence["actions"])
    handoff = evidence["handoffs"][0]
    assert handoff["cause"] == "confidence_below_threshold"
    assert handoff["s1_actions"] == []
    assert gate in handoff["s1_events"]
    assert handoff["remaining_seconds"] <= gate["remaining_seconds"]
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["service_recovery"] is True
    assert evidence["tokens"]["input"] == 32
    assert evidence["tokens"]["output"] == 30
    assert all(c["latency_seconds"] >= 0 for c in evidence["model_calls"])


@pytest.mark.parametrize(
    "treatment,enabled",
    [("combined_ungated", False), ("combined_gated", False), ("combined_gated", True)],
)
@pytest.mark.parametrize(
    "confidence",
    [
        None,
        -0.1,
        1.1,
        True,
        "0.5",
        [],
        {},
        float("nan"),
        float("inf"),
        -float("inf"),
        "missing",
    ],
)
def test_invalid_confidence_is_retained_and_only_gates_when_enabled(
    tmp_path, treatment, enabled, confidence
):
    class Invalid(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            if confidence == "missing":
                result.pop("confidence")
            else:
                result["confidence"] = confidence
                result["response"]["confidence"] = confidence
            return result

    evidence = run(
        tmp_path,
        Invalid(),
        treatment=treatment,
        confidence_gate_enabled=enabled,
        confidence_threshold=0.5,
    )
    gate = next(e for e in evidence["events"] if e["kind"] == "confidence_gate")
    assert gate["confidence_status"] == "missing_or_invalid"
    assert gate["decision"] == ("escalate" if enabled else "proceed")
    assert len(evidence["handoffs"]) == int(enabled)
    assert [a["stage"] for a in evidence["actions"]] == (["s2"] if enabled else ["s1"])
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["validity"]["valid"] is True
    assert evidence["model_calls"][0]["confidence_status"] == "missing_or_invalid"
    retained = evidence["model_calls"][0]
    if confidence == "missing":
        assert "confidence" not in retained
    elif isinstance(confidence, float) and not math.isfinite(confidence):
        assert retained["confidence"] == {"nonfinite_float": repr(confidence)}
        assert retained["response"]["confidence"] == retained["confidence"]
    else:
        assert retained["confidence"] == confidence


@pytest.mark.parametrize(
    "confidence,threshold,escalates",
    [
        (0.5, 0.5, False),
        (0, 0, False),
        (1, 0, False),
        (0, 1, True),
        (1, 1, False),
        (0.49, 0.5, True),
    ],
)
def test_strict_threshold_and_endpoints(tmp_path, confidence, threshold, escalates):
    class Certain(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            result["confidence"] = confidence
            return result

    evidence = run(
        tmp_path,
        Certain(),
        treatment="combined_gated",
        confidence_gate_enabled=True,
        confidence_threshold=threshold,
    )
    assert len(evidence["handoffs"]) == int(escalates)
    assert [a["stage"] for a in evidence["actions"]] == (
        ["s2"] if escalates else ["s1"]
    )
    assert evidence["outcome"]["policy_status"] == "verified"


@pytest.mark.parametrize(
    "treatment,enabled",
    [("combined_ungated", False), ("combined_gated", False), ("combined_gated", True)],
)
@pytest.mark.parametrize(
    "choice,success,cause",
    [
        ("invented", True, "unusable_decision"),
        ("unable", True, "explicit_inability"),
        ("bypass", False, "action_failed"),
        ("bypass", True, "verification_failed"),
        ("no_action", True, "verification_failed"),
    ],
)
def test_shared_failures_and_confident_unsuccessful_repairs(
    tmp_path, treatment, enabled, choice, success, cause
):
    class Failed(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            result["confidence"] = None if choice in {"unable", "invented"} else 1
            return result

        async def service_signal(self, since_ns):
            await self.clock.sleep_until(since_ns + 1_100_000_000)
            return {"recovered": any(isinstance(call, dict) for call in self.calls)}

    evidence = run(
        tmp_path,
        Failed(choice=choice, action_success=success),
        treatment=treatment,
        confidence_gate_enabled=enabled,
        confidence_threshold=0.5,
        deadline_seconds=6,
    )
    assert evidence["handoffs"][0]["cause"] == cause
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["s1_status"] == cause


@pytest.mark.parametrize(
    "settings",
    [
        {"confidence_gate_enabled": True},
        {
            "confidence_gate_enabled": True,
            "confidence_threshold": 0.5,
            "treatment": "combined_ungated",
        },
        {"confidence_threshold": -0.1},
        {"confidence_threshold": 1.1},
        {"confidence_threshold": float("nan")},
        {"confidence_threshold": float("inf")},
        {"confidence_threshold": True},
        {"confidence_threshold": "0.5"},
        {"confidence_gate_enabled": "false"},
    ],
)
def test_ambiguous_or_invalid_gate_configuration_is_rejected(settings):
    with pytest.raises(ValueError):
        config(**{"treatment": "combined_gated", **settings})


def test_fourth_slot_defaults_to_gate_disabled_not_a_hidden_policy(tmp_path):
    evidence = run(tmp_path, DirectRuntime(), treatment="combined_gated")
    assert evidence["config"]["confidence_gate_enabled"] is False
    assert evidence["handoffs"] == []
    assert [a["stage"] for a in evidence["actions"]] == ["s1"]


def test_fourth_slot_configuration_check_is_network_free():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign",
            "--config",
            "examples/noms27/recovery_campaign/development-gated.json",
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    declared = json.loads(result.stdout)
    assert declared["config"]["treatment"] == "combined_gated"
    assert declared["config"]["confidence_gate_enabled"] is True
    assert declared["catalogue"]["bypass"]
    assert declared["permissions"]["max_attempts"] == 3
    assert declared["confidence_gate"]["comparison"] == "strictly_below"


@pytest.mark.parametrize(
    "confidence,confidence_status",
    [
        (0.1, "valid"),
        (None, "missing_or_invalid"),
        (-1, "missing_or_invalid"),
        (10**400, "missing_or_invalid"),
        (True, "missing_or_invalid"),
        (False, "missing_or_invalid"),
        ("bad", "missing_or_invalid"),
        ("0.5", "missing_or_invalid"),
        ([], "missing_or_invalid"),
        ({}, "missing_or_invalid"),
        (1.1, "missing_or_invalid"),
        (float("nan"), "missing_or_invalid"),
        (float("inf"), "missing_or_invalid"),
        (-float("inf"), "missing_or_invalid"),
        ("missing", "missing_or_invalid"),
    ],
)
def test_native_s1_prompt_schema_and_output_are_identical_across_combined_slots(
    tmp_path, confidence, confidence_status
):
    import httpx

    from examples.noms27.recovery_campaign.live import LiveRuntime, RecordingTransport
    from mininet_ai.agents.bounded import NativeSystemOneClient
    from mininet_ai.transports.http import HttpxJsonTransport

    class Native(DirectRuntime):
        async def setup(self, declared, root):
            answer = {"type": "choice", "choice": "bypass"}
            if confidence != "missing":
                answer["confidence"] = confidence
            response = {
                "answers": {"next_action": answer},
                "usage": {"input_tokens": 12, "output_tokens": 0},
            }
            transport = HttpxJsonTransport(
                httpx.MockTransport(
                    lambda request: httpx.Response(200, content=json.dumps(response))
                )
            )
            self.native = LiveRuntime()
            self.native.config = declared
            self.native.recording = RecordingTransport(root, transport)
            self.native.client = NativeSystemOneClient(
                declared.endpoint, self.native.recording
            )
            return await super().setup(declared, root)

        async def decide(self, diagnostics, remaining_seconds):
            return await self.native.decide(diagnostics, remaining_seconds)

    results = [
        run(
            tmp_path / name,
            Native(),
            treatment=name,
            confidence_gate_enabled=enabled,
            confidence_threshold=0.5,
        )
        for name, enabled in [("combined_ungated", False), ("combined_gated", True)]
    ]
    ungated, gated = results
    assert ungated["model_calls"][0]["request"] == gated["model_calls"][0]["request"]
    assert ungated["model_calls"][0]["response"] == gated["model_calls"][0]["response"]
    assert ungated["provenance"]["instructions"] == gated["provenance"]["instructions"]
    assert ungated["provenance"]["criteria"] == gated["provenance"]["criteria"]
    assert (
        ungated["provenance"]["catalogue_sha256"]
        == gated["provenance"]["catalogue_sha256"]
    )
    if isinstance(confidence, float) and not math.isfinite(confidence):
        # NaN/Infinity are not JSON. The shared HTTP parser rejects the whole
        # response before a usable decision exists; do not widen that contract.
        assert all(
            e["outcome"]["policy_status"] == "escalation_uncontained" for e in results
        )
        assert all(e["outcome"]["s1_status"] == "treatment_error" for e in results)
        assert all(e["actions"] == e["handoffs"] == [] for e in results)
        return
    for evidence in results:
        adapter_result = next(
            e["result"] for e in evidence["events"] if e["kind"] == "inference_complete"
        )
        assert adapter_result["confidence_status"] == confidence_status
        assert evidence["model_calls"][0]["confidence_status"] == confidence_status
        assert (
            next(
                e["confidence_status"]
                for e in evidence["events"]
                if e["kind"] == "confidence_gate"
            )
            == confidence_status
        )
    gate = next(e for e in gated["events"] if e["kind"] == "confidence_gate")
    assert gate["confidence_present"] is (confidence != "missing")
    assert ungated["handoffs"] == []
    assert [a["stage"] for a in gated["actions"]] == ["s2"]
    assert gated["provenance"]["confidence_gate"]["enabled"] is True
    assert all(e["outcome"]["policy_status"] == "verified" for e in results)


@pytest.mark.parametrize(
    "choice,enabled", [("unable", False), ("unable", True), ("bypass", True)]
)
def test_gated_slot_preserves_unknown_work_containment_boundary(
    tmp_path, enabled, choice
):
    class Unknown(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            result = await super().decide(diagnostics, remaining_seconds)
            result["remote_terminal"] = "unconfirmed"
            return result

    evidence = run(
        tmp_path,
        Unknown(choice=choice),
        treatment="combined_gated",
        confidence_gate_enabled=enabled,
        confidence_threshold=0.5,
    )
    assert evidence["outcome"]["policy_status"] == "escalation_uncontained"
    assert evidence["handoffs"] == []
    assert evidence["containment"]["provider_contained"] is False


def test_gate_handoff_does_not_hide_unsuccessful_s2_outcome(tmp_path):
    evidence = run(
        tmp_path,
        DirectRuntime(good_from=None),
        treatment="combined_gated",
        confidence_gate_enabled=True,
        confidence_threshold=0.5,
        deadline_seconds=6,
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "attempts_exhausted"
    assert evidence["outcome"]["recovery_failure"] is True
    assert evidence["measurement"]["observation_seconds"] == 11
    assert len(evidence["model_calls"]) == 4
    assert len(evidence["actions"]) == 3


def test_gated_verification_timeout_hands_off_without_resetting_deadline(tmp_path):
    evidence = run(
        tmp_path,
        DirectRuntime(),
        treatment="combined_gated",
        confidence_gate_enabled=True,
        confidence_threshold=0,
        s1_cap_seconds=0.5,
    )
    handoff = evidence["handoffs"][0]
    assert handoff["cause"] == "s1_stage_timeout"
    deadline = next(
        e["deadline_ns"] for e in evidence["events"] if e["kind"] == "injection_start"
    )
    assert handoff["deadline_ns"] == deadline
    assert 0 < handoff["remaining_seconds"] < 3
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["recovery_timeout"] is False


@pytest.mark.parametrize("phase", ["inference", "action"])
def test_gated_timeout_does_not_overlap_unknown_terminal_work(tmp_path, phase):
    class Unknown(DirectRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            if phase == "inference":
                await asyncio.Future()
            return await super().decide(diagnostics, remaining_seconds)

        async def execute(self, choice):
            await asyncio.Future()

    evidence = run(
        tmp_path,
        Unknown(),
        treatment="combined_gated",
        confidence_gate_enabled=True,
        confidence_threshold=0,
        s1_cap_seconds=0.5,
    )
    assert evidence["outcome"]["policy_status"] == "escalation_uncontained"
    assert evidence["handoffs"] == []
    assert evidence["containment"]["next_trial_allowed"] is False
    assert evidence["measurement"]["observation_seconds"] == 8
