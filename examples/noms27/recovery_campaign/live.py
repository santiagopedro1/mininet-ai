"""Mininet diamond adapter, reusing the maintained harness and native S1 client.

Only setup/cleanup own topology state. Incident policy can install one literal
s1 flow, never call a shell supplied by a model or restore an interface.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mininet_ai.agents.bounded import NativeSystemOneClient
from mininet_ai.transports.http import HttpxJsonTransport

from .runner import (
    BIN,
    CATALOGUE,
    CRITERIA,
    INSTRUCTIONS,
    NS,
    Clock,
    measure,
    write_json,
)


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
        # Confidence is diagnostic-only for S1-only. Do not widen the core native
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
        startup_transport = RecordingTransport(root, self.transport)
        self.client = NativeSystemOneClient(config.endpoint, startup_transport)
        self.recording = startup_transport
        verification = await self._thread(
            self.client.verify_startup,
            model_name=config.model_name,
            expected_identity=config.expected_identity,
            instructions=INSTRUCTIONS,
            criteria=CRITERIA,
            choices={key: () for key in CATALOGUE},
            verify_identity=config.expected_identity is not None,
        )
        write_json(root / "model-startup.json", verification)
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
        await self._thread(self.harness.fault, "s1-s2")
        check = await self._check_fault()
        self.monitor = asyncio.create_task(self._monitor_fault())
        return check

    async def _check_fault(self):
        assert self.harness is not None
        link = self.harness.links["s1-s2"]
        states = await self._thread(lambda: [link.intf1.isUp(), link.intf2.isUp()])
        check = {
            "tick_ns": self.clock.now_ns(),
            "both_endpoints_down": states == [False, False],
            "endpoint_up": states,
        }
        self.fault_checks.append(check)
        return check

    async def _monitor_fault(self):
        try:
            while True:
                await self.clock.sleep_until(self.clock.now_ns() + BIN)
                await self._check_fault()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.measurement_errors.append("fault_monitor_failed:" + str(error))

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
        result = await self._thread(
            self.client.decide,
            state=json.dumps(diagnostics),
            instructions=INSTRUCTIONS,
            criteria=CRITERIA,
            choices={key: () for key in CATALOGUE},
            timeout_seconds=remaining_seconds,
            request_id=self.config.trial_id,
        )
        response = self.recording.last_response
        if not isinstance(response, dict):
            raise TypeError("native response is not an object")
        usage = response.get("usage", {}) if isinstance(response, dict) else {}
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

        def token(name):
            field = name.removesuffix("_tokens")
            value = usage.get(name, usage.get(field + "Tokens"))
            return (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else None
            )

        return {
            "choice": result.choice_id,
            "confidence": confidence,
            "confidence_status": "valid" if valid_confidence else "missing_or_invalid",
            "request": self.recording.last_request,
            "response": response,
            "tokens": {
                "input": token("input_tokens"),
                "output": token("output_tokens"),
                "reasoning": None,
            },
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
                asyncio.to_thread(self.transport.cancel_request, self.config.trial_id)
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
        if not partial:
            if not any(row.get("kind") == "sender_summary" for row in sends):
                errors.append("missing_sender_terminal_evidence")
            if not any(row.get("kind") == "receiver_summary" for row in arrivals):
                errors.append("missing_receiver_terminal_evidence")
        return {
            "packets": list(records.values()),
            "measurement_errors": sorted(set(errors)),
        }

    async def collect(self, observation_end_ns):
        await self._check_fault()
        if self.monitor is not None:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        await self._stop_traffic()
        collected = self._packet_records()
        collected["measurement_errors"].extend(self.measurement_errors)
        collected["fault_checks"] = self.fault_checks
        return collected

    async def _stop_traffic(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            elif self.clock.now_ns() < self.safety_end and not getattr(
                self, "traffic_stopped", False
            ):
                # A collector crash cannot be classified as data-plane loss.
                self.measurement_errors.append("traffic_worker_exited_early")
            try:
                await self._thread(process.wait, timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                await self._thread(process.wait, timeout=2)
                self.measurement_errors.append("traffic_worker_required_kill")
        self.traffic_stopped = True
        for log in self.process_logs:
            log.close()

    async def cleanup(self):
        traffic_error = None
        try:
            await self._stop_traffic()
        except Exception as error:
            traffic_error = str(error)
        if self.monitor is not None:
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        if self.harness is not None:
            result = await self._thread(self.harness.cleanup)
            # Verify actual owned names independently. The legacy harness's
            # interface-name check predates this campaign and is not authority.
            from examples.diamond_recovery import PORTS

            remaining = []
            for switch, peers in PORTS.items():
                for port in range(1, len(peers) + 1):
                    name = f"{switch}-eth{port}"
                    check = await self._thread(
                        subprocess.run,
                        ["ip", "-j", "link", "show", "dev", name],
                        capture_output=True,
                        check=False,
                    )
                    if check.returncode == 0:
                        remaining.append(name)
            return {
                "verified": result.get("status") == "verified"
                and not remaining
                and traffic_error is None,
                "result": result,
                "remaining_owned_interfaces": remaining,
                "traffic_error": traffic_error,
            }
        return {"verified": True, "reason": "no network created"}
