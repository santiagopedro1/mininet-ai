#!/usr/bin/env python3
"""Smoke-test a local Ollama System One decision endpoint.

This script does not deploy a network. It verifies the native decision
interface, model tag, optional digest, and one choice request.

Example:
    python scripts/smoke-ollama-systemone.py \
        --host http://10.10.10.152:11434 \
        --model clef-flash:9b
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from mininet_ai.agents.bounded import OllamaSystemOneClient
from mininet_ai.durations import duration_seconds
from mininet_ai.errors import AgentRuntimeError
from mininet_ai.transports.http import HttpxJsonTransport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://127.0.0.1:11434")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--digest",
        help="Optional pinned Ollama digest, with or without the sha256: prefix.",
    )
    parser.add_argument("--startup-timeout", default="300s")
    parser.add_argument("--decision-timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = OllamaSystemOneClient(
        args.host,
        args.model,
        HttpxJsonTransport(),
        startup_timeout_seconds=duration_seconds(args.startup_timeout),
    )
    criteria = {
        "healthy": "All required ports are up and every required flow is present.",
        "degraded": "A required port is down or a required flow is missing.",
    }
    choices = {key: () for key in criteria}
    try:
        startup = client.verify_startup(
            model_name=args.model,
            expected_identity={"digest": args.digest} if args.digest else None,
            instructions="Choose the single condition that matches the state.",
            criteria=criteria,
            choices=choices,
            interface_timeout_seconds=args.decision_timeout,
        )
        state: dict[str, Any] = {
            "switch": {
                "targets": ["s1"],
                "diagnosis": {
                    "portState": {
                        "s1": {
                            "ports": [
                                {"name": "s1-eth1", "state": "down"},
                                {"name": "s1-eth2", "state": "up"},
                            ]
                        }
                    }
                },
            }
        }
        result = client.decide(
            state=json.dumps(state, sort_keys=True, separators=(",", ":")),
            instructions="Choose the single condition that matches the state.",
            criteria=criteria,
            choices=choices,
            timeout_seconds=args.decision_timeout,
        )
    except AgentRuntimeError as error:
        print(json.dumps({"status": "failed", "error": str(error), "code": error.code}))
        return 1
    print(
        json.dumps(
            {
                "status": "succeeded",
                "startup": startup,
                "selectedChoice": result.choice_id,
                "probabilities": result.probabilities,
                "confidence": result.confidence,
                "response": result.native_response,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
