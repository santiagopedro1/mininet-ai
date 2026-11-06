"""Offline direct-S2 mechanisms at configuration -> campaign -> evidence only."""

import asyncio

import pytest

from tests.examples.test_recovery_campaign import ControlledRuntime, config, run


class DirectRuntime(ControlledRuntime):
    async def observe_network(self):
        return {
            "switches": {
                "s1": {"ports": {"1": True, "2": False, "3": True}, "flows": []},
                "s2": {"ports": {"1": False, "2": True}, "flows": []},
                "s3": {"ports": {"1": True, "2": True}, "flows": []},
                "s4": {"ports": {"1": True, "2": True, "3": True}, "flows": []},
            }
        }

    async def decide_s2(self, context, remaining_seconds):
        return {
            "repair": {
                "status": "repair",
                "actions": [
                    {"switch": "s3", "in_port": 1, "out_port": 2},
                    {"switch": "s4", "in_port": 3, "out_port": 1},
                    {"switch": "s1", "in_port": 1, "out_port": 3},
                ],
            },
            "request": context,
            "response": {"done": True},
            "tokens": {"input": 20, "output": 30, "reasoning": None},
        }

    async def execute_s2(self, repair):
        self.calls.append(repair)
        return {"success": True, "actions": repair["actions"]}


def test_direct_s2_starts_with_shared_snapshot_without_s1_waiting(tmp_path):
    evidence = run(tmp_path, DirectRuntime(), treatment="s2_only", s1_cap_seconds=0.01)
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "verified"
    assert evidence["outcome"]["recovery_timeout"] is False
    assert evidence["outcome"]["service_recovery"] is True
    assert len(evidence["actions"]) == 1
    call = evidence["model_calls"][0]
    assert call["stage"] == "s2"
    assert call["request"]["initial_snapshot"]["switch"] == "s1"
    assert call["request"]["alarm"] == "directional_service_disruption"
    assert set(call["request"]["observation"]["switches"]) == {"s1", "s2", "s3", "s4"}
    assert call["request"]["history"] == []
    assert evidence["permissions"]["forwarding"] == "surviving_switches_only"
    assert evidence["messages"]["handoff_count"] == 0
    assert evidence["tokens"]["input"] == 20
    assert evidence["measurement"]["observation_seconds"] == 8


def test_unsuccessful_s2_reobserves_and_stops_after_three_attempts(tmp_path):
    evidence = run(
        tmp_path,
        DirectRuntime(good_from=None),
        treatment="s2_only",
        deadline_seconds=6,
        s1_cap_seconds=0.01,
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "attempts_exhausted"
    assert evidence["outcome"]["recovery_timeout"] is False
    assert evidence["outcome"]["recovery_failure"] is True
    assert len(evidence["model_calls"]) == len(evidence["actions"]) == 3
    assert evidence["permissions"]["max_attempts"] == 3
    assert all(
        call["request"]["permissions"]["max_attempts"] == 3
        for call in evidence["model_calls"]
    )
    assert [len(call["request"]["history"]) for call in evidence["model_calls"]] == [
        0,
        1,
        2,
    ]
    assert evidence["tokens"]["input"] == 60
    assert evidence["tokens"]["output"] == 90
    assert evidence["measurement"]["observation_seconds"] == 11


@pytest.mark.parametrize("phase", ["telemetry", "inference", "action", "verification"])
def test_s2_all_attempts_share_deadline_and_retain_late_results(tmp_path, phase):
    class Late(DirectRuntime):
        async def delay(self):
            try:
                await self.clock.sleep_until(self.injection + 4_000_000_000)
            except asyncio.CancelledError:
                await self.clock.sleep_until(self.injection + 4_000_000_000)

        async def observe_network(self):
            if phase == "telemetry":
                await self.delay()
            return await super().observe_network()

        async def decide_s2(self, context, remaining_seconds):
            if phase == "inference":
                await self.delay()
            return await super().decide_s2(context, remaining_seconds)

        async def execute_s2(self, repair):
            if phase == "action":
                await self.delay()
            return await super().execute_s2(repair)

        async def service_signal(self, since_ns):
            if phase == "verification":
                await self.delay()
            return await super().service_signal(since_ns)

    evidence = run(
        tmp_path, Late(good_from=None), treatment="s2_only", s1_cap_seconds=0.01
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "timeout"
    assert evidence["outcome"]["recovery_timeout"] is True
    assert evidence["containment"]["next_trial_allowed"] is False
    assert evidence["measurement"]["observation_seconds"] == 8
    assert len(evidence["model_calls"]) <= 1
    assert any(
        event["kind"] == "late_completion" and event["phase"] == phase
        for event in evidence["events"]
    )


def test_s2_deadline_is_not_reset_on_second_attempt(tmp_path):
    evidence = run(
        tmp_path,
        DirectRuntime(good_from=None),
        treatment="s2_only",
        deadline_seconds=2,
        s1_cap_seconds=0.01,
    )
    assert evidence["outcome"]["policy_status"] == "timeout"
    assert len(evidence["actions"]) == 2
    assert evidence["measurement"]["observation_seconds"] == 7


@pytest.mark.parametrize("treatment", ["s1_only", "s2_only"])
@pytest.mark.parametrize("phase", ["inference", "action"])
def test_cancelled_incident_work_retains_unknown_terminal_evidence(
    tmp_path, treatment, phase
):
    class Cancelled(DirectRuntime):
        async def wait_for_cancellation(self):
            await asyncio.Future()

        async def decide(self, diagnostics, remaining_seconds):
            if phase == "inference":
                await self.wait_for_cancellation()
            return await super().decide(diagnostics, remaining_seconds)

        async def decide_s2(self, context, remaining_seconds):
            if phase == "inference":
                await self.wait_for_cancellation()
            return await super().decide_s2(context, remaining_seconds)

        async def execute(self, choice):
            await self.wait_for_cancellation()

        async def execute_s2(self, repair):
            await self.wait_for_cancellation()

    evidence = run(tmp_path, Cancelled(good_from=None), treatment=treatment)
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "timeout"
    assert evidence["containment"]["next_trial_allowed"] is False
    assert len(evidence["model_calls"]) == 1
    call = evidence["model_calls"][0]
    if phase == "inference":
        assert call["status"] == "locally_cancelled_remote_unknown"
        assert call["completed_ns"] >= call["started_ns"]
        assert call["latency_seconds"] >= 0
        assert call["response"] is None
        assert evidence["actions"] == []
    else:
        assert call["status"] == "completed"
        assert evidence["actions"][0]["result"] == {
            "success": None,
            "status": "cancelled_execution_unknown",
        }
    assert any(
        event["kind"] == "local_task_cancelled" and event["phase"] == phase
        for event in evidence["events"]
    )


def test_s2_missing_provider_terminal_response_stops_without_overlapping_retry(
    tmp_path,
):
    class LostResponse(DirectRuntime):
        async def decide_s2(self, context, remaining_seconds):
            raise ConnectionError("offline simulated transport failure")

    evidence = run(tmp_path, LostResponse(good_from=None), treatment="s2_only")
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["policy_status"] == "treatment_error"
    assert len(evidence["model_calls"]) == 1
    assert evidence["model_calls"][0]["status"] == "failed"
    assert evidence["containment"]["provider_contained"] is False
    assert evidence["containment"]["next_trial_allowed"] is False
    assert evidence["measurement"]["observation_seconds"] == 8


@pytest.mark.parametrize("status", ["unable", "no_action"])
def test_s2_early_inability_is_valid_and_observation_continues(tmp_path, status):
    class Unable(DirectRuntime):
        async def decide_s2(self, context, remaining_seconds):
            result = await super().decide_s2(context, remaining_seconds)
            result["repair"] = {"status": status, "actions": []}
            return result

    evidence = run(tmp_path, Unable(good_from=None), treatment="s2_only")
    assert evidence["validity"]["valid"] is True
    assert len(evidence["model_calls"]) == 1
    assert evidence["actions"] == []
    assert evidence["outcome"]["policy_status"] == (
        "explicit_inability" if status == "unable" else "no_action"
    )
    assert evidence["outcome"]["recovery_failure"] is True
    assert evidence["measurement"]["censor_seconds"] == 8


def test_direct_s2_cli_check_is_network_free_and_declares_only_s2_authority(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "examples.noms27.recovery_campaign",
            "--config",
            "examples/noms27/recovery_campaign/development-s2.json",
            "--check",
        ],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    declared = json.loads(completed.stdout)
    assert declared["config"]["treatment"] == "s2_only"
    assert declared["permissions"]["max_attempts"] == 3
    assert "repair_schema" in declared
    assert "catalogue" not in declared


def test_controlled_broader_scenario_cannot_be_selected_for_live_execution():
    with pytest.raises(ValueError, match="offline S2 mechanism fixture"):
        config(
            treatment="s2_only",
            scenario="controlled_broader_repair",
            purpose="live_feasibility",
        )


@pytest.mark.parametrize(
    "action",
    [
        {"switch": "s1", "in_port": 1, "out_port": 2},
        {"switch": "s9", "in_port": 1, "out_port": 2},
        {"switch": "s1", "in_port": 1, "out_port": 3, "command": "ip link set up"},
        {"switch": "s1", "in_port": 1, "out_port": 3, "offered_rate": 1},
        {"switch": "s1", "in_port": True, "out_port": 3},
    ],
)
def test_s2_rejects_fault_reversal_traffic_changes_and_unobserved_resources(
    tmp_path, action
):
    class Forbidden(DirectRuntime):
        async def decide_s2(self, context, remaining_seconds):
            result = await super().decide_s2(context, remaining_seconds)
            result["repair"]["actions"] = [action]
            return result

    evidence = run(tmp_path, Forbidden(good_from=None), treatment="s2_only")
    assert evidence["validity"]["valid"] is True
    assert evidence["actions"] == []
    assert len(evidence["model_calls"]) == 3
    assert evidence["outcome"]["policy_status"] == "attempts_exhausted"
    assert "s1-s2" not in str([call["request"] for call in evidence["model_calls"]])


@pytest.mark.parametrize("mode", ["success", "action_failure", "stale_port"])
@pytest.mark.parametrize("treatment", ["s2_only", "combined_ungated"])
def test_offline_broader_repair_uses_real_s2_adapter_and_independent_packets(
    tmp_path, mode, treatment
):
    """Controlled forwarding graph, not a live extended-diamond campaign class."""
    import json
    import math
    import re

    import httpx

    from examples.diamond_recovery import PORTS
    from examples.noms27.recovery_campaign.live import LiveRuntime, RecordingTransport
    from mininet_ai.transports.http import HttpxJsonTransport

    class GraphRuntime(LiveRuntime, DirectRuntime):
        evidence_kind = "mechanism_validation"

        def __init__(self):
            DirectRuntime.__init__(self, good_from=None)
            # Healthy primary forwarding; no active downstream bypass flows.
            self.installed = {("s1", 1): 2, ("s2", 1): 2, ("s4", 2): 1}
            self.requests = []

        async def setup(self, config, root):
            await ControlledRuntime.setup(self, config, root)
            self.root = root

            def respond(request):
                payload = json.loads(request.content)
                self.requests.append(payload)
                # A local redirect alone cannot repair this fixture's missing
                # active downstream forwarding. Retry must observe and repair it.
                actions = (
                    [{"switch": "s1", "in_port": 1, "out_port": 3}]
                    if len(self.requests) == 1
                    else [
                        {"switch": "s3", "in_port": 1, "out_port": 2},
                        {"switch": "s4", "in_port": 3, "out_port": 1},
                    ]
                )
                return httpx.Response(
                    200,
                    json={
                        "done": True,
                        "message": {
                            "content": json.dumps(
                                {"status": "repair", "actions": actions}
                            )
                        },
                        "prompt_eval_count": 41,
                        "eval_count": 23,
                    },
                )

            self.transport = HttpxJsonTransport(httpx.MockTransport(respond))
            self.recording = RecordingTransport(root, self.transport)
            return {
                "initial_state": True,
                "fresh_context": True,
                "automatic_backup": False,
                "fixture": "offline-forwarding-graph",
                "initial_forwarding": [
                    {"switch": switch, "in_port": port, "out_port": output}
                    for (switch, port), output in self.installed.items()
                ],
                "preinstalled_active_downstream_bypass": False,
            }

        async def _command(self, *parts):
            switch = parts[-1] if parts[3] != "add-flow" else parts[-2]
            if parts[3] == "dump-ports-desc":
                snapshot = await DirectRuntime.observe_network(self)
                if mode == "stale_port" and self.requests:
                    snapshot["switches"]["s1"]["ports"]["3"] = False
                return "\n".join(
                    f" {port}({switch}-eth{port}): addr:00:00:00:00:00:00\n     config: 0\n     state: {'0' if up else 'LINK_DOWN'}"
                    for port, up in snapshot["switches"][switch]["ports"].items()
                )
            if parts[3] == "dump-flows":
                return str(
                    {
                        port: output
                        for (node, port), output in self.installed.items()
                        if node == switch
                    }
                )
            assert parts[3] == "add-flow"
            if mode == "action_failure" and switch == "s4":
                raise RuntimeError("controlled OVS failure")
            in_match = re.search(r"in_port=(\d+)", parts[-1])
            out_match = re.search(r"output:(\d+)", parts[-1])
            assert in_match and out_match
            in_port, out_port = int(in_match[1]), int(out_match[1])
            self.installed[switch, in_port] = out_port
            # Independently walk the forwarding graph; the first redirect drops
            # at s3, and installing only s3 would still drop at s4's wrong ingress.
            node, previous, visited = "s1", "h1", set()
            while node != "h2" and node not in visited:
                visited.add(node)
                incoming = PORTS[node][previous]
                outgoing = self.installed.get((node, incoming))
                snapshot = await DirectRuntime.observe_network(self)
                if mode == "stale_port" and self.requests:
                    snapshot["switches"]["s1"]["ports"]["3"] = False
                if not snapshot["switches"][node]["ports"].get(str(outgoing)):
                    break
                following = next(
                    peer for peer, port in PORTS[node].items() if port == outgoing
                )
                previous, node = node, following
            if node == "h2":
                self.good_from = math.ceil(
                    (self.clock.now_ns() - self.injection) / 100_000_000
                )
            return ""

        async def inject(self, start_ns):
            self.injection_ns = start_ns
            return await ControlledRuntime.inject(self, start_ns)

        async def start_traffic(self):
            return await ControlledRuntime.start_traffic(self)

        async def diagnose(self):
            return await ControlledRuntime.diagnose(self)

        async def decide(self, diagnostics, remaining_seconds):
            return await ControlledRuntime.decide(self, diagnostics, remaining_seconds)

        async def service_signal(self, since_ns):
            return await ControlledRuntime.service_signal(self, since_ns)

        async def collect(self, end):
            return await ControlledRuntime.collect(self, end)

        async def cleanup(self):
            return await ControlledRuntime.cleanup(self)

    runtime = GraphRuntime()
    evidence = run(
        tmp_path,
        runtime,
        treatment=treatment,
        scenario="controlled_broader_repair",
        deadline_seconds=6,
        s1_cap_seconds=2,
    )
    assert evidence["validity"]["valid"] is True
    assert evidence["outcome"]["service_recovery"] is (mode == "success")
    attempts = 2 if mode == "success" else 3
    s2_actions = [action for action in evidence["actions"] if action["stage"] == "s2"]
    s2_calls = [call for call in evidence["model_calls"] if call["stage"] == "s2"]
    assert len(s2_actions) == attempts
    assert s2_calls[1]["request"]["messages"]
    assert evidence["tokens"]["input"] == 41 * attempts + (
        12 if treatment == "combined_ungated" else 0
    )
    assert evidence["tokens"]["output"] == 23 * attempts
    assert evidence["measurement"]["disruption_seconds"] >= 1.1
    assert {action["switch"] for action in s2_actions[1]["repair"]["actions"]} == {
        "s3",
        "s4",
    }
    assert all(
        "fault" not in json.loads(call["request"]["messages"][1]["content"])
        for call in s2_calls
    )
    action_journal = [
        json.loads(line)
        for line in (tmp_path / "evidence/s2-actions.jsonl").read_text().splitlines()
    ]
    if mode == "success":
        assert [row["status"] for row in action_journal] == ["started", "completed"] * 3
        if treatment == "combined_ungated":
            assert evidence["handoffs"][0]["cause"] == "verification_failed"
            assert evidence["handoffs"][0]["s1_actions"][0]["result"]["success"] is True
            assert (
                json.loads(s2_calls[0]["request"]["messages"][1]["content"])["handoff"]
                == evidence["handoffs"][0]
            )
    elif mode == "action_failure":
        assert evidence["actions"][-1]["result"]["success"] is False
        assert [
            result["success"] for result in evidence["actions"][-1]["result"]["actions"]
        ] == [True, False]
        assert sum(row["status"] == "failed" for row in action_journal) == 2
    else:
        assert s2_actions[0]["result"]["success"] is False
        assert not any(
            row.get("action", {}).get("switch") == "s1" for row in action_journal
        )
    assert "s2-actions.jsonl" in evidence["artifacts"]
    assert all(
        "/api/chat" in row["url"]
        for row in [
            json.loads(line)
            for line in (tmp_path / "evidence/model-wire.jsonl")
            .read_text()
            .splitlines()
        ]
        if row["kind"] == "request"
    )


@pytest.mark.parametrize("warmup_ok", [True, False])
@pytest.mark.parametrize("reply", ["unable", "malformed", "incomplete"])
@pytest.mark.parametrize("treatment", ["s2_only", "combined_ungated"])
def test_s2_startup_and_separate_stage_adapters_account_incident_separately(
    tmp_path, monkeypatch, warmup_ok, reply, treatment
):
    import json
    from types import SimpleNamespace

    import httpx

    from examples.noms27.recovery_campaign import live
    from mininet_ai.transports.http import HttpxJsonTransport

    requests = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"status": "ok"})
        payload = json.loads(request.content)
        requests.append((str(request.url), payload))
        if not request.url.path.endswith("/api/chat"):
            return httpx.Response(
                200,
                json={
                    "answers": {"next_action": {"type": "choice", "choice": "unable"}},
                    "usage": {"input_tokens": 11, "output_tokens": 0},
                },
            )
        s2_count = sum(url.endswith("/api/chat") for url, _ in requests)
        return httpx.Response(
            200,
            json={
                "done": warmup_ok and (s2_count == 1 or reply != "incomplete"),
                "message": {
                    "content": "OK"
                    if s2_count == 1
                    else (
                        '{"status":"unable","actions":[]}'
                        if reply == "unable"
                        else "not JSON"
                    )
                },
                "prompt_eval_count": 1000 if s2_count == 1 else 7,
                "eval_count": 1000 if s2_count == 1 else 5,
            },
        )

    harness = SimpleNamespace(
        setup=lambda: None,
        net=object(),
        links={},
        readiness=lambda: {"normalFallback": False, "controllers": {}},
        inspect=lambda: {"initial": "primary"},
    )
    monkeypatch.setattr(live.os, "geteuid", lambda: 0)
    original_import = live.importlib.import_module
    monkeypatch.setattr(
        live.importlib,
        "import_module",
        lambda name, *args: (
            SimpleNamespace(DiamondLiveHarness=lambda root: harness)
            if name == "examples.diamond-recovery.live_harness"
            else original_import(name, *args)
        ),
    )
    original_run = live.subprocess.run
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda args, **kwargs: (
            None if args[0] == "ovs-vsctl" else original_run(args, **kwargs)
        ),
    )

    class StartupRuntime(live.LiveRuntime, DirectRuntime):
        evidence_kind = "mechanism_validation"

        def __init__(self):
            live.LiveRuntime.__init__(self)
            self.clock = ControlledRuntime().clock
            self.transport = HttpxJsonTransport(httpx.MockTransport(respond))

        async def _command(self, *parts):
            return ""

        async def inject(self, start_ns):
            self.injection = start_ns
            return {"both_endpoints_down": True}

        async def start_traffic(self):
            return self.clock.now_ns()

        async def collect(self, end):
            self.good_from = None
            return await ControlledRuntime.collect(self, end)

        async def cleanup(self):
            return {"verified": True}

    evidence = run(
        tmp_path,
        StartupRuntime(),
        treatment=treatment,
        model_name="offline-test",
        s2_endpoint="http://s2.invalid:11434",
        s2_model_name="offline-s2",
    )
    wire_requests = [
        row
        for row in [
            json.loads(line)
            for line in (tmp_path / "evidence/model-wire.jsonl")
            .read_text()
            .splitlines()
        ]
        if row["kind"] == "request"
    ]
    s1_wire = [row for row in wire_requests if not row["url"].endswith("/api/chat")]
    wire_requests = [row for row in wire_requests if row["url"].endswith("/api/chat")]
    assert all(
        row["url"] == "http://s2.invalid:11434/api/chat"
        and row["payload"]["model"] == "offline-s2"
        for row in wire_requests
    )
    assert len(s1_wire) == (2 if treatment == "combined_ungated" and warmup_ok else 0)
    assert evidence["messages"]["handoff_count"] == (
        1 if treatment == "combined_ungated" and warmup_ok else 0
    )
    if warmup_ok:
        assert evidence["validity"]["valid"] is True
        expected_settings = {
            "think": False,
            "keep_alive": -1,
            "options": {"temperature": 0, "num_predict": 1024},
        }
        assert evidence["provenance"]["s2_model_settings"] == expected_settings
        for row in wire_requests[1:]:
            assert {
                key: row["payload"][key] for key in expected_settings
            } == expected_settings
        assert wire_requests[0]["payload"]["options"]["num_predict"] == 2
        attempts = 3 if reply == "malformed" else 1
        assert len(wire_requests) == 1 + attempts
        assert evidence["outcome"]["policy_status"] == (
            "explicit_inability"
            if reply == "unable"
            else "treatment_error"
            if reply == "incomplete"
            else "attempts_exhausted"
        )
        assert evidence["tokens"]["input"] == 7 * attempts + (
            11 if treatment == "combined_ungated" else 0
        )
        assert evidence["tokens"]["output"] == 5 * attempts
        assert evidence["containment"]["provider_contained"] is (reply != "incomplete")
        startup = evidence["reset"]["model_startup"]
        assert (startup["s2"] if treatment == "combined_ungated" else startup)[
            "immutable_identity_verified"
        ] is False
        assert (
            json.loads(wire_requests[1]["payload"]["messages"][1]["content"])[
                "initial_snapshot"
            ]["switch"]
            == "s1"
        )
    else:
        assert evidence["validity"]["valid"] is False
        assert len(wire_requests) == 1
        assert evidence["model_calls"] == []
        assert evidence["containment"]["next_trial_allowed"] is False
