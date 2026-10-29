#!/usr/bin/env python3
"""Run current-only bounded diagnosis against a live diamond network.

This is an opt-in evaluation runner. It performs no model-selected network
actions: the harness owns setup, fault injection, reset, and cleanup while the
native client receives only the target switch's current local observations.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from live_harness import DiamondLiveHarness
from mininet_ai.agents.bounded import NativeSystemOneClient, _current_only_context
from mininet_ai.compiler import compile_experiment
from mininet_ai.transports.http import HttpxJsonTransport


SWITCHES = ("s1", "s2", "s3", "s4")
TRIALS = ("healthy", "s1-s2", "s2-s4")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def _local_observation(
    harness: DiamondLiveHarness,
    switch_name: str,
    flows: str,
) -> dict[str, Any]:
    if harness.net is None:
        raise RuntimeError("live harness is not running")
    switch = harness.net.get(switch_name)
    ports = []
    for interface, number in sorted(switch.ports.items(), key=lambda item: item[1]):
        if number <= 0 or interface.name == "lo":
            continue
        ports.append(
            {
                "name": interface.name,
                "number": number,
                "state": "up" if interface.isUp() else "down",
            }
        )
    return {
        "flowState": {
            "switch": switch_name,
            "entries": flows.splitlines(),
            "available": True,
            "observedAt": _now(),
        },
        "portState": {
            "switch": switch_name,
            "ports": ports,
            "available": True,
            "observedAt": _now(),
        },
    }


def run(args: argparse.Namespace) -> int:
    evidence = args.evidence
    evidence.mkdir(parents=True, exist_ok=True)
    decisions_path = evidence / "decisions.jsonl"
    config_path = (
        Path(__file__).resolve().parents[2]
        / "examples/diamond-recovery/experiment.yaml"
    )
    plan = compile_experiment(config_path)
    scenario = plan.agents[0].decision_scenario
    if scenario is None:
        raise RuntimeError("diamond example has no decision scenario")

    choices = {choice["id"]: tuple(choice["proposals"]) for choice in scenario["choices"]}
    criteria = {choice["id"]: choice["description"] for choice in scenario["choices"]}
    client = NativeSystemOneClient(args.endpoint, HttpxJsonTransport())
    harness = DiamondLiveHarness(evidence)
    run_started = _now()
    try:
        setup = harness.setup()
        readiness = harness.readiness()
        props = client.transport.get_json(
            f"{args.endpoint.rstrip('/')}/props",
            timeout_seconds=5.0,
            max_response_bytes=1_048_576,
        )
        model_name = props.get("model_alias", args.model_name)
        startup = client.verify_startup(
            model_name=model_name,
            expected_identity={
                "repository": args.model_repository,
                "revision": args.model_revision,
                "artifact": args.model_artifact,
                "sha256": args.model_sha256,
            },
            instructions=scenario["instructions"],
            criteria=criteria,
            choices=choices,
        )
        _write_jsonl(
            evidence / "diagnosis-run.jsonl",
            {
                "observedAt": run_started,
                "operation": "startup",
                "setup": setup,
                "readiness": readiness,
                "model": props,
                "interface": startup,
            },
        )

        for trial in TRIALS:
            if trial != "healthy":
                harness.fault(trial)
                harness.readiness()
            inspection = harness.inspect()
            for switch_name in SWITCHES:
                observations = _local_observation(
                    harness, switch_name, inspection["flows"][switch_name]
                )
                state = _current_only_context(
                    scenario=scenario["metadata"],
                    identity={
                        "runId": f"live-diagnosis-{run_started}",
                        "agentId": f"diamond-decision@{switch_name}",
                        "invocationId": f"{trial}-{switch_name}-{time.time_ns()}",
                        "tickId": f"{trial}-{switch_name}",
                        "invokedAt": _now(),
                    },
                    targets=[switch_name],
                    diagnosis=observations,
                )
                expected = (
                    "degraded"
                    if any(port["state"] == "down" for port in observations["portState"]["ports"])
                    else "healthy"
                )
                started = time.monotonic()
                record: dict[str, Any] = {
                    "observedAt": _now(),
                    "trial": trial,
                    "switch": switch_name,
                    "expected": expected,
                    "request": state,
                }
                try:
                    result = client.decide(
                        state=json.dumps(state, sort_keys=True, separators=(",", ":")),
                        instructions=scenario["instructions"],
                        criteria=criteria,
                        choices=choices,
                        timeout_seconds=5.0,
                        request_id=state["identity"]["invocationId"],
                    )
                    record.update(
                        {
                            "status": "succeeded",
                            "observed": result.choice_id,
                            "nativeResponse": result.native_response,
                            "probabilities": result.probabilities,
                            "confidence": result.confidence,
                        }
                    )
                except Exception as error:
                    record.update(
                        {
                            "status": "failed",
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )
                record["elapsedSeconds"] = time.monotonic() - started
                _write_jsonl(decisions_path, record)
                print(json.dumps(record, sort_keys=True))
            if trial != "s2-s4":
                harness.reset()
                harness.readiness()
        harness.reset()
        harness.readiness()
        harness.cleanup()
    finally:
        if harness.net is not None:
            harness.cleanup()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--model-name", default="ggml-org/Kev-4B-GGUF")
    parser.add_argument("--model-repository", default="ggml-org/Kev-4B-GGUF")
    parser.add_argument(
        "--model-revision",
        default="f4624b6bb7072f1bab726a622c1cad3f96030b9e",
    )
    parser.add_argument("--model-artifact", default="Kev-4B-Q8_0.gguf")
    parser.add_argument(
        "--model-sha256",
        default="7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=Path("/tmp/diamond-live-diagnosis"),
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
