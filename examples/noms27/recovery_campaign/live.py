"""Mininet diamond adapter with separate native S1 and Ollama S2 inference paths.

Only setup/cleanup own topology state. Incident policy installs forwarding only,
never a shell supplied by a model or a resource-restoration command.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from mininet_ai.agents.bounded import NativeSystemOneClient
from mininet_ai.transports.http import HttpxJsonTransport

from .harness import ScenarioLiveHarness
from .runner import (
    BIN,
    CATALOGUE,
    CRITERIA,
    INSTRUCTIONS,
    NS,
    Clock,
    decode_tokens,
    measure,
    write_json,
)
from .substrate import cleanup_inventory, substrate_plan


class RecordingTransport:
    """Keep original wire JSON, including invalid confidence, before native mapping."""

    def __init__(self, root: Path, transport: Any):
        self.root = root
        self.transport = transport
        self.last_request = self.last_response = None

    def get_json(self, *args, **kwargs):
        return self.transport.get_json(*args, **kwargs)

    def post_json(self, url, payload, **kwargs):
        self.last_request = payload
        with (self.root / "model-wire.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "kind": "request",
                        "tick_ns": Clock().now_ns(),
                        "request_id": kwargs.get("request_id"),
                        "url": url,
                        "payload": payload,
                    }
                )
                + "\n"
            )
        try:
            self.last_response = self.transport.post_json(url, payload, **kwargs)
        except Exception as error:
            with (self.root / "model-wire.jsonl").open("a") as stream:
                stream.write(
                    json.dumps(
                        {
                            "kind": "transport_failure",
                            "tick_ns": Clock().now_ns(),
                            "request_id": kwargs.get("request_id"),
                            "error": str(error),
                            "response_body": getattr(error, "response_body", None),
                            "response_body_base64": getattr(
                                error, "response_body_base64", None
                            ),
                        }
                    )
                    + "\n"
                )
            raise
        with (self.root / "model-wire.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "kind": "response",
                        "tick_ns": Clock().now_ns(),
                        "request_id": kwargs.get("request_id"),
                        "payload": self.last_response,
                    }
                )
                + "\n"
            )
        # Confidence is diagnostic-only in these S1 paths. Do not widen the core native
        # contract: retain invalid values in wire evidence, omit solely for mapping.
        mapped = copy.deepcopy(self.last_response)
        if isinstance(mapped, dict):
            answer = (
                mapped.get("answers", {}).get("next_action", {})
                if isinstance(mapped.get("answers"), dict)
                else {}
            )
            for container in (mapped, answer):
                if not isinstance(container, dict) or "confidence" not in container:
                    continue
                value = container["confidence"]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    del container["confidence"]
        return mapped


class LiveRuntime:
    evidence_kind = "live_feasibility"

    def __init__(self):
        self.clock = Clock()
        self.harness = None
        self.processes: list[Any] = []
        self.process_logs: list[Any] = []
        self.action_process = None
        self.transport = HttpxJsonTransport()
        self.fault_checks = []
        self.monitor = None
        self.measurement_errors = []
        self.cleanup_started_ns: int | None = None
        self.cleanup_budget_seconds = 30.0
        self.plan: dict[str, Any] | None = None

    async def _thread(self, function, *args, **kwargs):
        # Do not lose the terminal result of a blocking provider/harness operation
        # when the coordinator requests local cancellation.
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            return await asyncio.shield(task)

    async def setup(self, config, root):
        self.config, self.root = config, root
        if config.scenario_instance is not None:
            return await self._setup_scenario(config)
        if config.scenario != "diamond_s1_s2_link":
            raise ValueError(
                "the live adapter does not implement controlled fixture scenarios"
            )
        if os.geteuid() != 0:
            raise PermissionError(
                "live Mininet campaign requires root; no model contacted"
            )
        if config.model_name == "unconfigured":
            raise ValueError("configure the local model before live feasibility")
        # Verify privileges and OVS before model warm-up or network construction.
        await self._thread(
            subprocess.run,
            ["ovs-vsctl", "--timeout=3", "show"],
            capture_output=True,
            text=True,
            check=True,
            timeout=4,
        )
        module = importlib.import_module("examples.diamond-recovery.live_harness")
        self.harness = module.DiamondLiveHarness(root / "diamond")
        await self._thread(self.harness.setup)
        assert self.harness.net is not None
        # Freeze actual bottleneck capacity on every owned link, both directions.
        for link in self.harness.links.values():
            for interface in (link.intf1, link.intf2):
                await self._thread(interface.config, bw=config.bottleneck_mbps)
        from examples.diamond_recovery import RECOVERY_RULES

        # Preinstall complete downstream active forwarding; s1 still uses primary.
        for rule in (RECOVERY_RULES[0], RECOVERY_RULES[2]):
            flow = (
                f"cookie={rule['cookie']:#x},priority=200,idle_timeout=0,hard_timeout=0,"
                f"ip,in_port={rule['match']['in_port']},nw_src=10.0.0.1,nw_dst=10.0.0.2,actions={rule['actions']}"
            )
            await self._command(
                "ovs-ofctl", "-O", "OpenFlow13", "add-flow", rule["switch"], flow
            )
        ready = await self._thread(self.harness.readiness)
        if ready["normalFallback"] or any(ready["controllers"].values()):
            raise RuntimeError("automatic_backup_or_controller_present")
        groups = await self._command(
            "ovs-ofctl", "-O", "OpenFlow13", "dump-groups", "s1"
        )
        if "group_id=" in groups:
            raise RuntimeError("automatic_group_forwarding_present")
        verification = await self._warm_up_models()
        # Warm-up is pre-injection and retained separately from incident inference.
        write_json(
            root / "network-manifest.json",
            {
                "readiness": ready,
                "bottleneck_mbps": config.bottleneck_mbps,
                "direction": "h1-to-h2",
                "return_traffic": "none required by unidirectional UDP",
                "preinstalled_downstream": [RECOVERY_RULES[0], RECOVERY_RULES[2]],
                "initial_flows": await self._thread(self.harness.inspect),
            },
        )
        return {
            "initial_state": True,
            "fresh_context": True,
            "automatic_backup": False,
            "model_startup": verification,
            "workload_feasibility": "not yet approved",
        }

    async def _setup_scenario(self, config) -> dict[str, Any]:
        """Six-class substrate from the frozen scenario; diamond path untouched."""

        scenario = config.scenario_instance
        self.plan = substrate_plan(scenario)
        if os.geteuid() != 0:
            raise PermissionError(
                "live Mininet campaign requires root; no model contacted"
            )
        if config.model_name == "unconfigured":
            raise ValueError("configure the local model before live feasibility")
        await self._thread(
            subprocess.run,
            ["ovs-vsctl", "--timeout=3", "show"],
            capture_output=True,
            text=True,
            check=True,
            timeout=4,
        )
        self.harness = ScenarioLiveHarness(scenario, self.root / "substrate")
        await self._thread(self.harness.setup)
        assert self.harness.net is not None
        # Freeze actual bottleneck capacity on every owned link, both directions.
        for link in self.harness.links.values():
            for interface in (link.intf1, link.intf2):
                await self._thread(interface.config, bw=config.bottleneck_mbps)
        ready = await self._thread(self.harness.readiness)
        if ready["normalFallback"] or any(ready["controllers"].values()):
            raise RuntimeError("automatic_backup_or_controller_present")
        for switch in self.plan["topology"]["switches"]:
            groups = await self._command(
                "ovs-ofctl", "-O", "OpenFlow13", "dump-groups", switch
            )
            if "group_id=" in groups:
                raise RuntimeError("automatic_group_forwarding_present")
        verification = await self._warm_up_models()
        flows = self.plan["flows"]
        write_json(
            self.root / "network-manifest.json",
            {
                "readiness": ready,
                "bottleneck_mbps": config.bottleneck_mbps,
                "direction": "h1-to-h2",
                "return_traffic": "none required by unidirectional UDP",
                "scenario_placement": scenario.placement_id,
                "structural_check": scenario.structural_check(),
                "preinstalled_downstream": [
                    flow for flow in flows["initial"] + flows["backup"]
                ],
                "initial_flows": await self._thread(self.harness.inspect_flows),
            },
        )
        return {
            "initial_state": True,
            "fresh_context": True,
            "automatic_backup": False,
            "model_startup": verification,
            "scenario_placement": scenario.placement_id,
            "workload_feasibility": "not yet approved",
        }

    async def _warm_up_models(self) -> dict[str, Any]:
        """Pre-injection startup verification, identical across substrates."""

        self.recording = RecordingTransport(self.root, self.transport)
        verification = {}
        if self.config.treatment != "s1_only":
            if (
                self.config.treatment == "s2_only"
                and self.config.expected_identity is not None
            ):
                raise ValueError("S2 immutable identity verification is unavailable")
            response = await self._thread(
                self.recording.post_json,
                (self.config.s2_endpoint or self.config.endpoint).rstrip("/")
                + "/api/chat",
                {
                    "model": self.config.s2_model_name or self.config.model_name,
                    "messages": [{"role": "user", "content": "Reply OK."}],
                    "stream": False,
                    "think": False,
                    "keep_alive": -1,
                    "options": {"temperature": 0, "num_predict": 2},
                },
                headers={"Content-Type": "application/json"},
                timeout_seconds=120,
                max_response_bytes=1_048_576,
            )
            if (
                not isinstance(response, dict)
                or response.get("done") is not True
                or not isinstance(response.get("message"), dict)
                or response.get("error")
            ):
                raise ValueError("S2 warm-up failed")
            verification = {
                "provider": "ollama",
                "warmup_response": response,
                "immutable_identity_verified": False,
                "limitation": "model name is not immutable identity",
            }
        if self.config.treatment != "s2_only":
            self.client = NativeSystemOneClient(self.config.endpoint, self.recording)
            native_verification = await self._thread(
                self.client.verify_startup,
                model_name=self.config.model_name,
                expected_identity=self.config.expected_identity,
                instructions=INSTRUCTIONS,
                criteria=CRITERIA,
                choices={key: () for key in CATALOGUE},
                verify_identity=self.config.expected_identity is not None,
            )
            verification = (
                native_verification
                if self.config.treatment == "s1_only"
                else {
                    "s1": native_verification,
                    "s2": verification,
                }
            )
        write_json(self.root / "model-startup.json", verification)
        return verification

    async def _command(self, *parts):
        process = await asyncio.create_subprocess_exec(
            *parts, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        self.action_process = process
        try:
            out, err = await process.communicate()
            if process.returncode:
                raise RuntimeError(f"command {parts!r} failed: {err.decode()}")
            return out.decode()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 0.5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            raise
        finally:
            self.action_process = None

    async def start_traffic(self):
        assert self.harness is not None
        self.start_ns = self.clock.now_ns() + NS // 5
        self.safety_end = self.start_ns + round(
            (5 + self.config.deadline_seconds + 10) * NS
        )
        worker = Path(__file__).with_name("udp_worker.py")
        for role, host, address in (
            ("receive", "h2", "10.0.0.2"),
            ("send", "h1", "10.0.0.2"),
        ):
            log = (self.root / f"udp-{role}.stderr").open("w")
            self.process_logs.append(log)
            process = self.harness.net.get(host).popen(
                [
                    sys.executable,
                    str(worker),
                    role,
                    "--address",
                    address,
                    "--root",
                    str(self.root.resolve()),
                    "--start-ns",
                    str(self.start_ns),
                    "--end-ns",
                    str(self.safety_end),
                    "--rate",
                    str(self.config.packet_rate),
                ],
                stdout=log,
                stderr=log,
            )
            self.processes.append(process)
            if role == "receive":
                for _ in range(100):
                    if (self.root / "udp-ready.json").exists():
                        break
                    if process.poll() is not None:
                        raise RuntimeError("UDP receiver failed before readiness")
                    await asyncio.sleep(0.01)
                else:
                    raise RuntimeError("UDP receiver readiness missing")
                # Sender begins only after collector acknowledges readiness.
                self.start_ns = self.clock.now_ns() + NS // 5
        return self.start_ns

    async def inject(self, start_ns):
        assert self.harness is not None
        self.injection_ns = start_ns
        if self._has_scenario():
            await self._thread(self.harness.fault)
        else:
            await self._thread(self.harness.fault, "s1-s2")
        check = await self._check_fault(phase="injection")
        self.monitor = asyncio.create_task(self._monitor_fault())
        return check

    def _has_scenario(self) -> bool:
        # Fixture runtimes may subclass without LiveRuntime.__init__; every
        # scenario dispatch therefore probes the attribute defensively.
        return getattr(self, "plan", None) is not None

    async def _check_fault(self, *, phase="unspecified"):
        assert self.harness is not None
        check: dict[str, Any] = {
            "phase": phase,
            "started_ns": self.clock.now_ns(),
            "both_endpoints_down": None,
            "tick_ns": None,
        }
        if self._has_scenario():
            try:
                check.update(await self._thread(self.harness.check_fault))
            except Exception as error:
                check["error"] = {"type": type(error).__name__, "message": str(error)}
                check["tick_ns"] = self.clock.now_ns()
                self.fault_checks.append(check)
                raise
        else:
            link = self.harness.links["s1-s2"]
            check.update({"endpoint_up": None})
            try:
                states = await self._thread(
                    lambda: [link.intf1.isUp(), link.intf2.isUp()]
                )
                check.update(
                    both_endpoints_down=states == [False, False], endpoint_up=states
                )
            except Exception as error:
                check["error"] = {"type": type(error).__name__, "message": str(error)}
                raise
            finally:
                check["tick_ns"] = self.clock.now_ns()
                self.fault_checks.append(check)
            return check
        check["tick_ns"] = self.clock.now_ns()
        self.fault_checks.append(check)
        return check

    async def _monitor_fault(self):
        monitor = asyncio.current_task()
        assert monitor is not None
        try:
            # _thread drains synchronous checks after cancellation to retain
            # their terminal evidence. Do not then start another monitor cycle.
            while not monitor.cancelling():
                await self.clock.sleep_until(self.clock.now_ns() + BIN)
                await self._check_fault(phase="periodic")
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.measurement_errors.append(
                f"fault_monitor_failed:{type(error).__name__}:{error}"
            )

    async def _stop_fault_monitor(self):
        if self.monitor is not None:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)

    async def diagnose(self):
        # Only the affected switch is visible to S1. No evaluator fault labels,
        # expected repair identities or global harness manifests cross this boundary.
        ports = await self._command(
            "ovs-ofctl", "-O", "OpenFlow13", "dump-ports-desc", "s1"
        )
        flows = await self._command("ovs-ofctl", "-O", "OpenFlow13", "dump-flows", "s1")
        return {
            "switch": "s1",
            "ports": ports,
            "flows": flows,
            "observed_ns": self.clock.now_ns(),
        }

    async def decide(self, diagnostics, remaining_seconds):
        self.inference_request_id = self.config.trial_id + ":s1"
        result = await self._thread(
            self.client.decide,
            state=json.dumps(diagnostics),
            instructions=INSTRUCTIONS,
            criteria=CRITERIA,
            choices={key: () for key in CATALOGUE},
            timeout_seconds=remaining_seconds,
            request_id=self.inference_request_id,
        )
        response = self.recording.last_response
        if not isinstance(response, dict):
            raise TypeError("native response is not an object")
        answer = (
            response.get("answers", {}).get("next_action", {})
            if isinstance(response, dict)
            else {}
        )
        confidence = response.get("confidence", answer.get("confidence"))
        valid_confidence = (
            not isinstance(confidence, bool)
            and isinstance(confidence, (int, float))
            and math.isfinite(confidence)
            and 0 <= confidence <= 1
        )

        return {
            "choice": result.choice_id,
            "confidence": confidence,
            "confidence_status": "valid" if valid_confidence else "missing_or_invalid",
            "request": self.recording.last_request,
            "response": response,
            "tokens": decode_tokens(response),
            "reasoning_tokens_unavailable": "native choice protocol has no separate reasoning-token usage",
            "provider": "llamacpp-systemone",
            "remote_terminal": "normal_native_response",
        }

    async def execute(self, choice):
        entry = CATALOGUE[choice]
        if entry is None:
            raise ValueError("choice has no executable repair")
        await self._command(
            "ovs-ofctl", "-O", "OpenFlow13", "add-flow", entry["switch"], entry["flow"]
        )
        return {
            "success": True,
            "meaning": "ovs-ofctl exit zero, not service restoration",
            "switch": entry["switch"],
            "flow": entry["flow"],
        }

    async def observe_network(self):
        if self._has_scenario():
            peers_map: Any = {
                switch: dict(ports)
                for switch, ports in self.config.scenario_instance.ports.items()
            }
        else:
            from examples.diamond_recovery import PORTS

            peers_map = PORTS
        switches = {}
        for switch, peers in peers_map.items():
            raw_ports = await self._command(
                "ovs-ofctl", "-O", "OpenFlow13", "dump-ports-desc", switch
            )
            ports = {}
            for match in re.finditer(
                r"^\s*(\d+)\([^\n]*\):[^\n]*\n(.*?)(?=^\s*\d+\(|\Z)",
                raw_ports,
                re.MULTILINE | re.DOTALL,
            ):
                state = re.search(r"^\s*state:\s*(\S+)", match[2], re.MULTILINE)
                config = re.search(r"^\s*config:\s*(\S+)", match[2], re.MULTILINE)
                ports[match[1]] = bool(
                    state and config and state[1] == "0" and config[1] == "0"
                )
            switches[switch] = {
                "ports": ports,
                "port_descriptions": raw_ports,
                "peers": peers,
                "flows": await self._command(
                    "ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch
                ),
            }
        observation = {"switches": switches, "observed_ns": self.clock.now_ns()}
        self.s2_observation = observation
        return observation

    async def decide_s2(self, context, remaining_seconds):
        from .s2 import INSTRUCTIONS as S2_INSTRUCTIONS
        from .s2 import MODEL_SETTINGS, Repair

        payload = {
            "model": self.config.s2_model_name or self.config.model_name,
            "messages": [
                {"role": "system", "content": S2_INSTRUCTIONS},
                {"role": "user", "content": json.dumps(context)},
            ],
            "format": Repair.model_json_schema(),
            "stream": False,
            **MODEL_SETTINGS,
        }
        self.inference_request_id = self.config.trial_id + ":s2"
        response = await self._thread(
            self.recording.post_json,
            (self.config.s2_endpoint or self.config.endpoint).rstrip("/") + "/api/chat",
            payload,
            headers={"Content-Type": "application/json"},
            timeout_seconds=remaining_seconds,
            max_response_bytes=1_048_576,
            request_id=self.inference_request_id,
        )
        tokens: dict[str, int | None] = {
            "input": None,
            "output": None,
            "reasoning": None,
        }
        if isinstance(response, dict):
            for field, key in (
                ("input", "prompt_eval_count"),
                ("output", "eval_count"),
            ):
                value = response.get(key)
                if type(value) is int and value >= 0:
                    tokens[field] = value
        repair = None
        error = None
        try:
            if (
                not isinstance(response, dict)
                or response.get("done") is not True
                or response.get("error")
            ):
                raise ValueError("incomplete or failed S2 response")
            repair = json.loads(response["message"]["content"])
        except (ValueError, KeyError, TypeError) as problem:
            error = str(problem)
        return {
            "repair": repair,
            "parse_error": error,
            "request": payload,
            "response": response,
            "tokens": tokens,
            "provider": "ollama",
            "remote_terminal": "normal_ollama_response"
            if isinstance(response, dict) and response.get("done") is True
            else "unconfirmed",
            "reasoning_tokens_unavailable": "think disabled; separate usage unavailable",
        }

    async def execute_s2(self, repair):
        from .s2 import validate_repair

        # Re-observe at execution admission: never authorize using a stale up port.
        observation = await self.observe_network()
        permitted = validate_repair(repair, observation)
        results = []

        def record(**entry):
            with (self.root / "s2-actions.jsonl").open("a") as stream:
                stream.write(
                    json.dumps({"tick_ns": self.clock.now_ns(), **entry}) + "\n"
                )

        for action in permitted["actions"]:
            if self.clock.now_ns() >= self.injection_ns + round(
                self.config.deadline_seconds * NS
            ):
                raise TimeoutError("no forwarding-command admission after deadline")
            flow = (
                "cookie=0x3201,priority=300,idle_timeout=0,hard_timeout=0,"
                f"ip,in_port={action['in_port']},nw_src=10.0.0.1,nw_dst=10.0.0.2,"
                f"actions=output:{action['out_port']}"
            )
            record(status="started", action=action, flow=flow)
            try:
                await self._command(
                    "ovs-ofctl", "-O", "OpenFlow13", "add-flow", action["switch"], flow
                )
                results.append({**action, "flow": flow, "success": True})
                record(status="completed", result=results[-1])
            except asyncio.CancelledError:
                record(status="cancelled_execution_unknown", action=action, flow=flow)
                raise
            except Exception as error:
                results.append(
                    {**action, "flow": flow, "success": False, "error": str(error)}
                )
                record(status="failed", result=results[-1])
                return {
                    "success": False,
                    "actions": results,
                    "admission_observation": observation,
                    "partial_effects_possible": True,
                }
        return {
            "success": True,
            "actions": results,
            "admission_observation": observation,
            "meaning": "ovs-ofctl exit zero, not service restoration",
        }

    async def service_signal(self, since_ns):
        # Full one-second send interval plus delivery allowance, aligned to the
        # next evaluator send bin. This is an operational projection, not authority.
        first_bin = math.ceil((since_ns - self.injection_ns) / BIN)
        end_ns = self.injection_ns + (first_bin + 11) * BIN
        await self.clock.sleep_until(end_ns)
        collected = self._packet_records(partial=True)
        evidence = measure(self.config, collected["packets"], self.injection_ns)
        window = evidence["loss_timeline"][first_bin : first_bin + 10]
        return {
            "recovered": len(window) == 10
            and all(item["qualifies"] for item in window),
            "interval_start_ns": self.injection_ns + first_bin * BIN,
            "verification_complete_ns": end_ns,
            "source": "operational-packet-projection",
        }

    async def cancel(self, phase):
        # Closing a local socket is not proof of remote inference termination.
        if phase == "inference":
            asyncio.create_task(
                asyncio.to_thread(
                    self.transport.cancel_request,
                    getattr(self, "inference_request_id", self.config.trial_id),
                )
            )
        if self.action_process is not None and self.action_process.returncode is None:
            try:
                self.action_process.terminate()
            except ProcessLookupError:
                pass
        return {"local_cancel_requested": True, "remote_termination": "unknown"}

    def _packet_records(self, *, partial=False):
        errors = []

        def read(name):
            path = self.root / name
            if not path.exists():
                errors.append("missing_" + name)
                return []
            rows = []
            for line in path.read_text().splitlines(keepends=True):
                if partial and not line.endswith("\n"):
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    errors.append("truncated_or_invalid_" + name)
            return rows

        sends, arrivals = read("udp-sends.jsonl"), read("udp-arrivals.jsonl")
        records = {}
        for row in sends:
            if row.get("kind") != "send":
                if row.get("kind") == "error" or row.get("missed_slots", 0):
                    errors.append("sender_error_or_missed_slots")
                continue
            if row["seq"] in records:
                errors.append("duplicate_send_sequence")
            records[row["seq"]] = {
                "seq": row["seq"],
                "send_ns": row["send_ns"],
                "arrivals_ns": [],
            }
        for row in arrivals:
            if row.get("kind") == "error":
                errors.append("receiver_error")
            if row.get("kind") != "arrival":
                continue
            packet = records.get(row["seq"])
            if (
                packet is None
                or packet["send_ns"] != row["send_ns"]
                or row["payload_bytes"] != 1200
            ):
                errors.append("unmatched_or_malformed_arrival")
            else:
                packet["arrivals_ns"].append(row["arrival_ns"])
        if any(seq != expected for expected, seq in enumerate(sorted(records))):
            errors.append("send_sequence_gap")
        summaries = {
            "sender": [row for row in sends if row.get("kind") == "sender_summary"],
            "receiver": [
                row for row in arrivals if row.get("kind") == "receiver_summary"
            ],
        }
        if not partial:
            for role, rows, fields in (
                ("sender", sends, ("next_sequence", "missed_slots")),
                ("receiver", arrivals, ("received",)),
            ):
                terminal = summaries[role]
                if not terminal:
                    errors.append(f"missing_{role}_terminal_evidence")
                elif (
                    len(terminal) != 1
                    or rows[-1] != terminal[0]
                    or any(
                        type(terminal[0].get(field)) is not int
                        or terminal[0][field] < 0
                        for field in fields
                    )
                ):
                    errors.append(f"invalid_{role}_terminal_evidence")
                elif role == "sender":
                    summary = terminal[0]
                    if summary["missed_slots"]:
                        errors.append("sender_error_or_missed_slots")
                    if (
                        summary["next_sequence"]
                        != len(records) + summary["missed_slots"]
                    ):
                        errors.append("sender_summary_count_mismatch")
                elif terminal[0]["received"] != sum(
                    row.get("kind") == "arrival" for row in arrivals
                ):
                    errors.append("receiver_summary_count_mismatch")
        return {
            "packets": list(records.values()),
            "worker_summaries": summaries,
            "measurement_errors": sorted(set(errors)),
        }

    async def collect(self, observation_end_ns):
        self._cleanup_remaining()
        # Mininet Node.cmd uses one non-thread-safe interactive shell per node.
        # Drain the periodic check before issuing a final check on those shells.
        await self._stop_fault_monitor()
        try:
            await self._check_fault(phase="final")
        except Exception as error:
            # Keep packet evidence while retaining the failed inspection as
            # experimental invalidity, never as confirmed persistent link-down.
            self.measurement_errors.append(
                f"final_fault_check_failed:{type(error).__name__}:{error}"
            )
        await self._stop_traffic()
        collected = self._packet_records()
        collected["measurement_errors"].extend(self.measurement_errors)
        collected["fault_checks"] = self.fault_checks
        return collected

    def _cleanup_remaining(self):
        if self.cleanup_started_ns is None:
            self.cleanup_started_ns = self.clock.now_ns()
            self.cleanup_budget_seconds = getattr(
                getattr(self, "config", None), "cleanup_budget_seconds", 30.0
            )
        return max(
            0.0,
            self.cleanup_budget_seconds
            - (self.clock.now_ns() - self.cleanup_started_ns) / NS,
        )

    async def _stop_traffic(self):
        # Collection's worker stop and subsequent cleanup share this same budget.
        self._cleanup_remaining()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            elif self.clock.now_ns() < self.safety_end and not getattr(
                self, "traffic_stopped", False
            ):
                # A collector crash cannot be classified as data-plane loss.
                self.measurement_errors.append("traffic_worker_exited_early")
            try:
                timeout = min(2, self._cleanup_remaining())
                if timeout <= 0:
                    raise subprocess.TimeoutExpired("traffic worker", timeout)
                await self._thread(process.wait, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                timeout = min(2, self._cleanup_remaining())
                if timeout > 0:
                    try:
                        await self._thread(process.wait, timeout=timeout)
                    except subprocess.TimeoutExpired:
                        self.measurement_errors.append("traffic_worker_stop_unverified")
                else:
                    self.measurement_errors.append("traffic_worker_stop_unverified")
                self.measurement_errors.append("traffic_worker_required_kill")
        self.traffic_stopped = True
        for log in self.process_logs:
            log.close()

    async def cleanup(self):
        self._cleanup_remaining()
        await self._stop_fault_monitor()
        traffic_error = None
        try:
            await self._stop_traffic()
        except Exception as error:
            traffic_error = str(error)
        result = {"status": "not_created"}
        inspections, errors = [], []
        remaining = bridges = None
        if self.harness is not None:
            try:
                # Reuse owned Mininet teardown, not the legacy harness cleanup's
                # unbounded/ambiguous inspection commands. Python net.stop() may
                # overrun the best-effort budget; do not claim a hard deadline.
                if self.harness.net is not None:
                    await self._thread(self.harness.net.stop)
                    self.harness.net = None
                result = {"status": "stopped", "method": "owned_mininet_net.stop"}
            except Exception as error:
                result = {"status": "failed", "error": str(error)}
            # A successful inventory can prove absence. A nonzero per-device
            # probe cannot distinguish absence from an inspection failure.
            if self._has_scenario():
                scenario = self.config.scenario_instance
                owned_names = set(cleanup_inventory(scenario)["interfaces"])
                plan = self.plan
                assert plan is not None
                owned_bridges = set(plan["topology"]["switches"])
            else:
                from examples.diamond_recovery import PORTS

                owned_names = {
                    f"{switch}-eth{port}"
                    for switch, peers in PORTS.items()
                    for port in range(1, len(peers) + 1)
                } | {"h1-eth0", "h2-eth0"}
                owned_bridges = set(PORTS)
            for command in (["ip", "-j", "link", "show"], ["ovs-vsctl", "list-br"]):
                try:
                    timeout = self._cleanup_remaining()
                    if timeout <= 0:
                        raise TimeoutError("shared cleanup budget exhausted")
                    check = await self._thread(
                        subprocess.run,
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=timeout,
                    )
                    inspections.append(
                        {
                            "command": command,
                            "returncode": check.returncode,
                            "stdout": check.stdout,
                            "stderr": check.stderr,
                        }
                    )
                    if check.returncode:
                        raise RuntimeError(check.stderr or "nonzero inspection exit")
                    if command[0] == "ip":
                        inventory = json.loads(check.stdout)
                        if not isinstance(inventory, list) or any(
                            not isinstance(row, dict)
                            or not isinstance(row.get("ifname"), str)
                            for row in inventory
                        ):
                            raise ValueError("malformed interface inventory")
                        remaining = sorted(
                            owned_names & {row["ifname"] for row in inventory}
                        )
                    else:
                        bridges = sorted(owned_bridges & set(check.stdout.splitlines()))
                except Exception as error:
                    errors.append(f"{command[0]}:{error}")
        exhausted = self._cleanup_remaining() <= 0
        assert self.cleanup_started_ns is not None
        workers_stopped = all(process.poll() is not None for process in self.processes)
        return {
            "verified": result["status"] != "failed"
            and not errors
            and not remaining
            and not bridges
            and traffic_error is None
            and workers_stopped
            and not exhausted,
            "result": result,
            "remaining_owned_interfaces": remaining,
            "remaining_owned_bridges": bridges,
            "inspections": inspections,
            "inspection_errors": errors,
            "traffic_error": traffic_error,
            "workers_stopped": workers_stopped,
            "budget_seconds": self.cleanup_budget_seconds,
            "elapsed_seconds": (self.clock.now_ns() - self.cleanup_started_ns) / NS,
            "budget_exhausted": exhausted,
            "hard_return_deadline": False,
            "limitation": "synchronous Mininet teardown can exceed the best-effort budget",
        }
