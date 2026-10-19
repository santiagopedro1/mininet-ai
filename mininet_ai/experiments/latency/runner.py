"""Maintained experiment entry point, with injectable rootless runtime seams."""

from __future__ import annotations

import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from itertools import islice
from pathlib import Path
from typing import Literal
from uuid import uuid4

from mininet_ai.experiments.scenarios import GENERATOR_VERSION, FailurePasses
from mininet_ai.runtime.instrumentation import PhaseRecorder

from .evidence import EvidenceWriter, paths
from .session import SubstrateTrialSession
from .summary import derive_summary
from .trials import ThreadRuntime, TrialConfig, TrialCoordinator
from .validation import validate_artifact


def manifest(scenario, config):
    replay = scenario.to_record()
    # V2's closed network contract must describe the deployment, not requested
    # constants that an edited replay specification no longer satisfies.
    for resource in scenario.plan.resources:
        if resource.kind == "link" and any(
            getattr(resource, key, None) != value
            for key, value in (
                ("bandwidth", 100),
                ("delay", "1ms"),
                ("loss", 0),
                ("jitter", None),
            )
        ):
            raise ValueError(
                "v2 requires 100 Mbps, 1ms, zero-loss links without jitter"
            )
        if resource.kind == "switch" and (
            getattr(resource, "controllers", ())
            or getattr(resource, "fail_mode", None) != "secure"
            or tuple(getattr(resource, "protocols", ())) != ("OpenFlow13",)
        ):
            raise ValueError("v2 requires controller-free secure OpenFlow13 switches")
        if resource.kind == "port" and getattr(resource, "parent", None) in (
            "h1",
            "h2",
        ):
            host = resource.parent
            if (getattr(resource, "ipv4", None), getattr(resource, "mac", None)) != (
                f"10.0.0.{host[-1]}/24",
                f"02:00:00:00:00:0{host[-1]}",
            ):
                raise ValueError("v2 requires the agreed fixed host addresses")
    model = scenario.plan.snapshot["blueprints"][0]["model"]
    populations: tuple[tuple[Literal["warmup", "measured"], int], ...] = (
        ("warmup", config.warmup),
        ("measured", config.measured),
    )

    def rules(selected, failed=False):
        result = []
        for switch in selected.switches:
            for rule in selected.rules(switch, failed=failed):
                match = rule["match"]
                host = next(
                    h
                    for h in ("h1", "h2")
                    if match.get("arp_tpa") == f"10.0.0.{h[-1]}"
                    or match.get("dl_dst") == f"02:00:00:00:00:0{h[-1]}"
                )
                result.append(
                    {
                        "switch_id": switch,
                        "destination": host,
                        "eth_type": "arp" if match.get("arp") else "ipv4",
                        "output_port": int(rule["actions"].split(":")[1]),
                    }
                )
        return result

    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(["git", "status", "--porcelain"], text=True)
        )
    except OSError, subprocess.CalledProcessError:
        revision, dirty = "unknown", None
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "generator_version": GENERATOR_VERSION,
        "seed": scenario.seed,
        "scenario_id": scenario.plan.digest,
        "agents": [
            {"agent_id": a.id, "switch_id": a.attachment.targets[0]}
            for a in scenario.plan.agents
        ],
        "configuration": {
            "measured_attempts": config.measured,
            "warmup_attempts": config.warmup,
            "budgets_seconds": {
                k: getattr(config, f"{k}_seconds")
                for k in ("setup", "injection", "response", "cleanup")
            },
            "sampling_period_seconds": config.sample_seconds,
            "probe_period_seconds": config.probe_seconds,
        },
        "scenario": {
            "graph": replay["edges"],
            "ports_by_switch": {
                s: [
                    {"neighbor": n, "port": p}
                    for (owner, n), p in sorted(
                        scenario.ports.items(), key=lambda item: item[1]
                    )
                    if owner == s
                ]
                for s in scenario.switches
            },
            "host_attachments": replay["host_attachments"],
            "host_addresses": {
                h: {"ipv4": f"10.0.0.{h[-1]}/24", "mac": f"02:00:00:00:00:0{h[-1]}"}
                for h in ("h1", "h2")
            },
            "designated_traffic": [
                {"source": a, "destination": b} for a, b in (("h1", "h2"), ("h2", "h1"))
            ],
            "eligible_failure_links": [list(p) for p in scenario.eligible_failures],
            "failure_order": {
                kind: [
                    list(p)
                    for p in islice(
                        FailurePasses(
                            scenario.seed, scenario.eligible_failures, kind=kind
                        ),
                        count,
                    )
                ]
                for kind, count in populations
            },
            "healthy_paths": paths(scenario),
            "policies": {
                "healthy": rules(scenario),
                "recovery": [
                    {
                        "failed_link": list(pair),
                        "paths": paths(scenario.select_failure(pair), True),
                        "rules": rules(scenario.select_failure(pair), True),
                    }
                    for pair in scenario.eligible_failures
                ],
            },
            "network": {
                "bandwidth_mbps": 100,
                "delay_ms": 1,
                "loss_percent": 0,
                "openflow": "OpenFlow13",
                "fail_mode": "secure",
                "controller": None,
            },
        },
        "provenance": {
            "code_revision": revision,
            "provider": model["provider"],
            "model": model["name"],
            "provider_configuration": model.get("parameters", {}),
            "resolved_model_identity": None,
            "environment": {
                "platform": platform.platform(),
                "python": sys.version,
                "packages": {
                    name: version(name)
                    for name in ("mininet-ai", "agno", "pydantic", "jsonschema")
                },
                "code_dirty": dirty,
                "experiment": scenario.plan.snapshot,
                "replay": replay,
                "limitations": [
                    "pooled agent observations are descriptive and correlated",
                    "recovery timings are conditional on confirmed recovery",
                    "global harness detection; sampled link checks are not atomic",
                ],
            },
        },
    }


def run_experiment(
    scenario, directory: Path, *, config=None, runtime=None, session_factory=None
):
    config, runtime = config or TrialConfig(), runtime or ThreadRuntime()
    if scenario.generator_version != GENERATOR_VERSION:
        raise ValueError("v2 requires explicit version-1 generated scenario")
    payload = manifest(scenario, config)
    writer = EvidenceWriter(Path(directory), str(uuid4()), str(uuid4()), runtime.clock)
    writer.write("run", payload)
    # Empty finalized streams are meaningful when no attempt can be finalized.
    for filename in ("events.jsonl", "trials.jsonl", "agents.jsonl"):
        (writer.directory / filename).touch(exist_ok=False)
    recorder = PhaseRecorder(
        writer.domain,
        clock=runtime.clock,
        sink=writer.phase,
        request_sink=writer.request,
    )
    if session_factory is None:
        from mininet_ai.substrates import MininetOVSRuntime

        session_factory = lambda selected, ident: SubstrateTrialSession(
            selected, ident, MininetOVSRuntime()
        )

    sessions = {}
    create_session = session_factory

    def fresh_session(selected, ident):
        session = create_session(selected, ident)
        sessions[ident] = session
        return session

    def finalized(attempt, selected):
        for loss in recorder.losses:
            if loss.context.trial_id == attempt.trial_id:
                writer.event(
                    loss.context,
                    "recorder_loss",
                    {"reason": loss.reason, "lost_count": 1},
                )
        trustworthy = recorder.trustworthy(attempt.execution.context)
        session = sessions.get(attempt.trial_id)
        metrics = {
            r.agent_id: {
                "eventDetectionSeconds": r.timings.event_detection_seconds,
                "reasoningSeconds": r.timings.reasoning_seconds,
                "actionEffectSeconds": r.timings.action_effect_seconds,
                "native": {
                    "contextBuildSeconds": r.timings.context_build_seconds,
                    "modelQueueingSeconds": r.timings.model_queueing_seconds,
                    "actionExecutionSeconds": r.timings.action_execution_seconds,
                    "totalSeconds": r.timings.total_seconds,
                },
            }
            for r in getattr(session, "agent_results", ())
        }
        with writer.lock:
            writer.project(
                attempt, selected, trustworthy=trustworthy, sdk_metrics=metrics
            )
            writer.frozen_trials.add(attempt.trial_id)

    def evidence(context, kind, data, tick):
        try:
            return writer.event(context, kind, data, tick)
        except Exception as error:
            recorder.report_loss(context, f"evidence_sink_failure: {error}")
            return None

    try:
        result = TrialCoordinator(
            runtime, recorder, config, evidence=evidence, finalized=finalized
        ).run(scenario, fresh_session, run_id=writer.run_id)
        # No further callbacks can revise a finalized projection after cleanup expiry.
        with writer.lock:
            writer.event(
                None,
                "run_finished" if result.status == "completed" else "run_aborted",
                {"reason": result.reason or "attempts_completed"},
            )
            summary = writer.write("summary", derive_summary(writer.records))
            writer.sealed = True
    finally:
        writer.sealed = True
    validation = validate_artifact(directory)
    if validation.integrity != "valid" or validation.state == "incomplete":
        raise ValueError(
            f"artifact validation failed: {validation.issues}; {validation.incomplete_reasons}"
        )
    return summary
