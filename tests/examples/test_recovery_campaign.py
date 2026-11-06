"""Known outcomes at the approved campaign configuration -> evidence boundary.

Controlled model/packet records validate mechanisms, never treatment effects.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

import pytest

from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign


class FastClock:
    def __init__(self):
        self.origin = time.monotonic_ns()

    def now_ns(self):
        return (time.monotonic_ns() - self.origin) * 50

    async def sleep_until(self, tick):
        await asyncio.sleep(max(0, tick - self.now_ns()) / 50e9)


class ControlledRuntime:
    evidence_kind = "mechanism_validation"

    def __init__(
        self, *, choice="bypass", good_from: int | None = 2, action_success=True
    ):
        self.clock = FastClock()
        self.choice = choice
        self.good_from = good_from
        self.action_success = action_success
        self.calls = []

    async def setup(self, config, root):
        self.config = config
        return {"initial_state": True, "fresh_context": True, "automatic_backup": False}

    async def start_traffic(self):
        return self.clock.now_ns()

    async def inject(self, start_ns):
        self.injection = start_ns
        self.calls.append("inject_down")
        return {"both_endpoints_down": True}

    async def diagnose(self):
        return {"switch": "s1", "ports": {"s1-eth2": "down"}, "flows": []}

    async def decide(self, diagnostics, remaining_seconds):
        return {
            "choice": self.choice,
            "confidence": 0.1,
            "request": {"diagnostics": diagnostics},
            "response": {"choice": self.choice},
            "tokens": {"input": 12, "output": 0, "reasoning": None},
        }

    async def execute(self, choice):
        self.calls.append(choice)
        return {"success": self.action_success}

    async def service_signal(self, since_ns):
        await self.clock.sleep_until(since_ns + 1_100_000_000)
        return {"recovered": self.good_from is not None}

    async def cancel(self, phase):
        self.calls.append("cancel:" + phase)
        return {"local_cancel_requested": True, "remote_termination": "unknown"}

    async def collect(self, observation_end_ns):
        # 1000 pps = 10% of a 96 Mbit/s payload bottleneck.
        packets = []
        for seq in range(-5000, int((observation_end_ns - self.injection) / 1e6)):
            sent = self.injection + seq * 1_000_000
            good = seq < 0 or (
                self.good_from is not None and seq >= self.good_from * 100
            )
            packets.append(
                {
                    "seq": seq + 5000,
                    "send_ns": sent,
                    "arrivals_ns": [sent + 1_000_000] if good else [],
                }
            )
        return {
            "packets": packets,
            "measurement_errors": [],
            "fault_checks": [
                {"tick_ns": observation_end_ns, "both_endpoints_down": True}
            ],
        }

    async def cleanup(self):
        return {"verified": True}


def config(**overrides):
    return CampaignConfig.model_validate(
        {
            "trial_id": "known-success",
            "treatment": "s1_only",
            "scenario": "diamond_s1_s2_link",
            "deadline_seconds": 3,
            "s1_cap_seconds": 3,
            "bottleneck_mbps": 96,
            "seed": 7,
            **overrides,
        }
    )


def run(tmp_path, runtime=None, **overrides):
    runtime = runtime or ControlledRuntime()
    result = asyncio.run(
        run_campaign(config(**overrides), tmp_path / "evidence", runtime=runtime)
    )
    assert json.loads((tmp_path / "evidence/evidence.json").read_text()) == result
    return result


def test_s1_configuration_exports_independent_recovery_and_one_local_attempt(tmp_path):
    runtime = ControlledRuntime()
    evidence = run(tmp_path, runtime)
    assert evidence["evidence_kind"] == "mechanism_validation"
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["service_recovery"] is True
    assert evidence["outcome"]["action_execution_success"] is True
    assert evidence["outcome"]["recovery_timeout"] is False
    assert evidence["measurement"]["recovery_start_seconds"] == 0.2
    assert evidence["measurement"]["verification_complete_seconds"] == 1.3
    assert evidence["measurement"]["disruption_seconds"] == 0.2
    assert evidence["measurement"]["observation_seconds"] == 8
    assert runtime.calls == ["inject_down", "bypass"]
    assert evidence["model_calls"][0]["tokens"] == {
        "input": 12,
        "output": 0,
        "reasoning": None,
    }
    assert evidence["provenance"]["budget_status"] == "development_unapproved"


@pytest.mark.parametrize(
    "choice,action_success,status,attempts",
    [
        ("bypass", False, "action_failed", 1),
        ("bypass", True, "verification_failed", 1),
        ("unable", True, "explicit_inability", 0),
        ("no_action", True, "no_action", 0),
        ("invented", True, "unusable_decision", 0),
    ],
)
def test_unsuccessful_treatments_remain_valid_and_observed(
    tmp_path, choice, action_success, status, attempts
):
    evidence = run(
        tmp_path,
        ControlledRuntime(choice=choice, good_from=None, action_success=action_success),
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == status
    assert evidence["outcome"]["service_recovery"] is False
    assert evidence["outcome"]["recovery_failure"] is True
    assert evidence["outcome"]["recovery_timeout"] is False
    assert len(evidence["actions"]) == attempts
    assert evidence["measurement"]["disruption_seconds"] == 8
    assert evidence["measurement"]["longest_delivery_gap_seconds"] == 8
    assert evidence["measurement"]["censored"] is True
    assert evidence["measurement"]["censor_seconds"] == 8


def test_send_bins_dedup_deadline_equality_every_bin_and_relapse(tmp_path):
    class Packets(ControlledRuntime):
        async def collect(self, end):
            result = await super().collect(end)
            by_seq = {packet["seq"]: packet for packet in result["packets"]}
            # Exactly 100ms is timely; the other arrival is a raw duplicate.
            by_seq[5010]["arrivals_ns"] = [by_seq[5010]["send_ns"] + 100_000_000] * 2
            # One failure in a 100-packet bin is allowed. Two are not, even
            # though the whole-second delivery ratio would exceed 99%.
            for seq in (5000, 5500, 5501, 7000, 7001):
                by_seq[seq]["arrivals_ns"] = [by_seq[seq]["send_ns"] + 100_000_001]
            return result

    evidence = run(tmp_path, Packets(good_from=0))
    measurement = evidence["measurement"]
    assert measurement["duplicate_arrivals"] == 1
    assert measurement["late_packets"] == 5
    assert measurement["failed_packets"] == 5
    assert measurement["disruption_seconds"] == 0.005
    assert measurement["loss_timeline"][0]["qualifies"] is True
    assert measurement["loss_timeline"][5]["qualifies"] is False
    assert measurement["recovery_start_seconds"] == 0.6
    assert measurement["verification_complete_seconds"] == 1.7
    assert measurement["relapse_starts_seconds"] == [2.0]


def test_deadline_cancels_and_retains_late_inference_without_executing(tmp_path):
    class Late(ControlledRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            try:
                await self.clock.sleep_until(self.injection + 4_000_000_000)
            except asyncio.CancelledError:
                # A provider can complete after local cancellation; do not admit its action.
                await self.clock.sleep_until(self.injection + 4_000_000_000)
            return await super().decide(diagnostics, remaining_seconds)

    runtime = Late(good_from=None)
    evidence = run(tmp_path, runtime)
    assert evidence["outcome"]["recovery_timeout"] is True
    assert evidence["actions"] == []
    assert runtime.calls == ["inject_down", "cancel:inference"]
    assert evidence["measurement"]["observation_seconds"] == 8
    assert any(event["kind"] == "late_completion" for event in evidence["events"])
    assert len(evidence["model_calls"]) == 1
    assert evidence["model_calls"][0]["late"] is True
    assert evidence["model_calls"][0]["tokens"]["input"] == 12


def test_cli_validates_frozen_configuration_without_starting_a_network(tmp_path):
    source = tmp_path / "campaign.json"
    source.write_text(json.dumps(config().model_dump(mode="json")))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign",
            "--config",
            str(source),
            "--check",
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    declared = json.loads(completed.stdout)
    assert declared["packet_rate"] == 1000
    assert declared["budget_status"] == "development_unapproved"
    assert declared["surviving_path"] == ["h1", "s1", "s3", "s4", "h2"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"treatment": "s2_only"},
        {"scenario": "cut_link"},
        {"payload_bytes": 1000},
        {"load_fraction": 0.7},
        {"delivery_deadline_ms": 50},
        {"automatic_backup": True},
        {"deadline_seconds": float("nan")},
        {"s1_cap_seconds": 4},
        {"budget_status": "approved"},
        {"purpose": "held_out_evaluation"},
    ],
)
def test_configuration_rejects_unsupported_or_unapproved_campaign_changes(overrides):
    with pytest.raises(ValueError):
        config(**overrides)


@pytest.mark.skipif(os.geteuid() == 0, reason="read-only non-root live preflight check")
def test_live_cli_retains_invalid_preflight_without_contacting_model(tmp_path):
    source = tmp_path / "live.json"
    source.write_text(
        json.dumps(config(purpose="live_feasibility").model_dump(mode="json"))
    )
    destination = tmp_path / "live-evidence"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign",
            "--config",
            str(source),
            "--evidence",
            str(destination),
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2, completed.stderr
    evidence = json.loads((destination / "evidence.json").read_text())
    assert evidence["evidence_kind"] == "live_feasibility"
    assert evidence["validity"]["valid"] is False
    assert any("requires root" in reason for reason in evidence["validity"]["reasons"])
    assert evidence["model_calls"] == []
    assert evidence["actions"] == []
    assert evidence["measurement"] is None


def test_late_restoration_is_not_on_time_success_or_early_censoring(tmp_path):
    evidence = run(tmp_path, ControlledRuntime(good_from=25))
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["action_execution_success"] is True
    assert evidence["outcome"]["service_recovery"] is False
    assert evidence["outcome"]["recovery_failure"] is True
    assert evidence["measurement"]["late_restoration"] is True
    assert evidence["measurement"]["censored"] is False
    assert evidence["measurement"]["verification_complete_seconds"] == 3.6


def test_malformed_response_is_a_valid_outcome_with_retained_request(tmp_path):
    class Malformed(ControlledRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            raise ValueError("invalid native output")

    evidence = run(tmp_path, Malformed(good_from=None))
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "treatment_error"
    assert evidence["model_calls"][0]["status"] == "failed"
    assert evidence["model_calls"][0]["request"]["diagnostics"]["switch"] == "s1"
    assert evidence["model_calls"][0]["tokens"]["input"] is None


def test_failed_action_has_false_execution_outcome_not_unavailable(tmp_path):
    class BrokenCommand(ControlledRuntime):
        async def execute(self, choice):
            raise RuntimeError("OVS rejected action")

    evidence = run(tmp_path, BrokenCommand(good_from=None))
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["action_execution_success"] is False
    assert evidence["outcome"]["policy_status"] == "action_failed"
    assert "OVS rejected" in evidence["actions"][0]["result"]["error"]


def test_real_udp_workers_and_native_protocol_emit_mechanism_evidence(tmp_path):
    """Real loopback sockets/processes, controlled network/model; NOT Mininet evidence."""
    from types import SimpleNamespace

    import httpx

    from examples.noms27.recovery_campaign.live import LiveRuntime, RecordingTransport
    from examples.noms27.recovery_campaign.runner import Clock
    from mininet_ai.agents.bounded import NativeSystemOneClient
    from mininet_ai.transports.http import HttpxJsonTransport

    class LoopbackHost:
        def popen(self, arguments, **kwargs):
            arguments[arguments.index("--address") + 1] = "127.0.0.1"
            return subprocess.Popen(arguments, **kwargs)

    class SocketRuntime(LiveRuntime):
        evidence_kind = "mechanism_validation"

        async def setup(self, declared, root):
            self.config, self.root = declared, root
            self.clock = Clock()
            self.down = False
            endpoint = SimpleNamespace(isUp=lambda: not self.down)
            self.harness = SimpleNamespace(
                net=SimpleNamespace(get=lambda name: LoopbackHost()),
                links={"s1-s2": SimpleNamespace(intf1=endpoint, intf2=endpoint)},
                fault=lambda link: setattr(self, "down", True),
                cleanup=lambda: {"status": "verified"},
            )
            transport = httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "answers": {
                            "next_action": {
                                "type": "choice",
                                "choice": "bypass",
                                "confidence": -1,
                            }
                        },
                        "usage": {"input_tokens": 17, "output_tokens": 0},
                    },
                )
            )
            self.transport = HttpxJsonTransport(transport)
            self.recording = RecordingTransport(root, self.transport)
            self.client = NativeSystemOneClient(declared.endpoint, self.recording)
            return {
                "initial_state": True,
                "fresh_context": True,
                "automatic_backup": False,
            }

        async def diagnose(self):
            return {"switch": "s1", "ports": {"2": "down"}, "flows": []}

        async def execute(self, choice):
            return {"success": True}

    evidence = run(tmp_path, SocketRuntime(), bottleneck_mbps=9.6)
    assert evidence["evidence_kind"] == "mechanism_validation"
    assert evidence["outcome"]["service_recovery"] is True
    assert evidence["measurement"]["sent_packets"] >= 790
    assert len(evidence["packet_evidence"]["packets"]) > 1250
    assert evidence["model_calls"][0]["tokens"] == {
        "input": 17,
        "output": 0,
        "reasoning": None,
    }
    assert (
        evidence["model_calls"][0]["response"]["answers"]["next_action"]["confidence"]
        == -1
    )
    assert evidence["model_calls"][0]["confidence_status"] == "missing_or_invalid"
    assert "fault" not in json.loads(evidence["model_calls"][0]["request"]["state"])
    assert (tmp_path / "evidence/udp-arrivals.jsonl").stat().st_size > 0


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("baseline", "broken_initial_service"),
        ("fault_reversed", "fault_persistence_unverified_or_reversed"),
        ("clock", "arrival_precedes_send_clock_error"),
        ("collector", "collector_process_failed"),
    ],
)
def test_experimental_failures_retained_independently_of_recovery(
    tmp_path, failure, expected
):
    class Invalid(ControlledRuntime):
        async def collect(self, end):
            result = await super().collect(end)
            if failure == "baseline":
                result["packets"][0]["arrivals_ns"] = []
                result["packets"][1]["arrivals_ns"] = []
            elif failure == "fault_reversed":
                result["fault_checks"][0]["both_endpoints_down"] = False
            elif failure == "clock":
                result["packets"][0]["arrivals_ns"] = [
                    result["packets"][0]["send_ns"] - 1
                ]
            else:
                result["measurement_errors"] = ["collector_process_failed"]
            return result

    evidence = run(tmp_path, Invalid())
    assert evidence["outcome"]["service_recovery"] is True
    assert evidence["validity"]["valid"] is False
    assert expected in evidence["validity"]["reasons"]
    assert evidence["packet_evidence"]["packets"]


def test_separate_token_totals_unavailable_metadata_and_artifact_manifest(tmp_path):
    evidence = run(tmp_path)
    assert evidence["tokens"] == {
        "input": 12,
        "output": 0,
        "reasoning": None,
        "calls": 1,
        "retries": 0,
        "reasoning_accounting": "separate_not_added_to_output",
    }
    assert evidence["messages"]["handoff_count"] == 0
    assert evidence["messages"]["wire_bytes"] is None
    assert (
        evidence["provenance"]["clock_domain"] == "mechanism-validation-runtime-clock"
    )
    assert "config.json" in evidence["artifacts"]
    assert "events.jsonl" in evidence["artifacts"]
    assert evidence["provenance"]["source_manifest"]["runner.py"]


def test_missing_essential_packet_fields_retains_invalid_trial_instead_of_crashing(
    tmp_path,
):
    class Missing(ControlledRuntime):
        async def collect(self, end):
            result = await super().collect(end)
            del result["packets"][0]["send_ns"]
            return result

    evidence = run(tmp_path, Missing())
    assert evidence["validity"]["valid"] is False
    assert (
        "missing_or_malformed_essential_packet_records"
        in evidence["validity"]["reasons"]
    )
    assert evidence["measurement"] is None
    assert "send_ns" not in evidence["packet_evidence"]["packets"][0]


def test_s1_cap_does_not_reset_deadline_and_preserves_late_model_cost(tmp_path):
    class StageLate(ControlledRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            try:
                await self.clock.sleep_until(self.injection + 2_000_000_000)
            except asyncio.CancelledError:
                await self.clock.sleep_until(self.injection + 2_000_000_000)
            return await super().decide(diagnostics, remaining_seconds)

    evidence = run(tmp_path, StageLate(good_from=None), s1_cap_seconds=1)
    assert evidence["outcome"]["recovery_timeout"] is True
    assert evidence["measurement"]["observation_seconds"] == 8
    assert evidence["tokens"]["input"] == 12
    assert evidence["actions"] == []
    assert evidence["containment"]["next_trial_allowed"] is False


def test_native_budget_exhaustion_is_timeout_not_malformed_response(tmp_path):
    class NativeTimeout(Exception):
        code = "agent.bounded-decision.timeout"

    class Exhausted(ControlledRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            raise NativeTimeout("native request exhausted remaining time")

    evidence = run(tmp_path, Exhausted(good_from=None))
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["recovery_timeout"] is True
    assert any(event["kind"] == "cancellation" for event in evidence["events"])


def test_unusable_native_output_still_counts_reported_tokens(tmp_path):
    class Unusable(Exception):
        native_response: ClassVar[dict] = {
            "answers": {"invalid": {}},
            "usage": {"input_tokens": 9, "output_tokens": 2},
        }

    class InvalidOutput(ControlledRuntime):
        async def decide(self, diagnostics, remaining_seconds):
            raise Unusable("not a valid native choice")

    evidence = run(tmp_path, InvalidOutput(good_from=None))
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "treatment_error"
    assert evidence["tokens"]["input"] == 9
    assert evidence["tokens"]["output"] == 2
    assert evidence["model_calls"][0]["response"] == Unusable.native_response
