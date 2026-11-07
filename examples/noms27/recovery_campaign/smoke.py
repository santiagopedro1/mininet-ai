"""Real-substrate mechanism smoke for one declared six-class placement.

Builds the scenario's Mininet/OVS substrate and drives a full incident at the
configuration-to-evidence boundary while every model response is scripted
locally: no provider, model machine or held-out effectiveness evidence exists
here. Requires root plus Linux/Open vSwitch; requires an evidence directory
that does not exist yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from examples.noms27.recovery_campaign.live import LiveRuntime
from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign
from examples.noms27.recovery_campaign.scenarios import CLASSES, make_scenario
from mininet_ai.transports.http import HttpxJsonTransport

FIXTURE_TOKENS = {"input": 5, "output": 2, "reasoning": None}


def scripted_s2_repair(scenario) -> dict[str, Any]:
    """The controlled adapter's repair set, validated live port authority later."""

    actions = []
    for node, target in scenario.backup_forwarding.items():
        ports = scenario.ports[node]
        previous = (
            "h1"
            if node == "s1"
            else next(
                peer
                for peer, value in scenario.backup_forwarding.items()
                if value == node
            )
        )
        actions.append(
            {"switch": node, "in_port": ports[previous], "out_port": ports[target]}
        )
    return {"status": "repair", "actions": actions}


def make_model_transport(scenario, model_alias: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ready"})
        if path == "/props":
            return httpx.Response(
                200,
                json={
                    "model_alias": model_alias,
                    "limitation": "scripted smoke fixture, not a served model",
                },
            )
        if path == "/v1/systemone":
            return httpx.Response(
                200,
                json={
                    "answers": {
                        "next_action": {
                            "type": "choice",
                            "choice": "bypass",
                            "confidence": 0.9,
                        }
                    },
                    "usage": {
                        "input_tokens": FIXTURE_TOKENS["input"],
                        "output_tokens": FIXTURE_TOKENS["output"],
                    },
                },
            )
        if path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "message": {"content": json.dumps(scripted_s2_repair(scenario))},
                    "prompt_eval_count": 11,
                    "eval_count": 19,
                },
            )
        return httpx.Response(404, json={"error": f"unexpected path {path}"})

    return httpx.MockTransport(handler)


class SmokeRuntime(LiveRuntime):
    """Real substrate, scripted models: mechanism-validation evidence only."""

    evidence_kind = "mechanism_validation"

    def __init__(self, scenario, model_alias):
        super().__init__()
        self.transport = HttpxJsonTransport(make_model_transport(scenario, model_alias))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--class-id", choices=sorted(CLASSES), required=True)
    parser.add_argument("--placement", type=int, default=0)
    parser.add_argument(
        "--treatment",
        choices=("s1_only", "s2_only", "combined_ungated"),
        default="s1_only",
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--model-alias", default="smoke-fixture")
    args = parser.parse_args()
    scenario = make_scenario(args.class_id, args.placement)
    config = CampaignConfig(
        trial_id=f"smoke-{scenario.placement_id}-{args.treatment}",
        treatment=args.treatment,
        scenario="matched_scenario",
        scenario_instance=scenario,
        deadline_seconds=6,
        s1_cap_seconds=3,
        bottleneck_mbps=9.6,
        seed=1000,
        model_name=args.model_alias,
    )
    evidence = asyncio.run(
        run_campaign(
            config, args.evidence, runtime=SmokeRuntime(scenario, args.model_alias)
        )
    )
    print(
        json.dumps(
            {
                "trial_id": config.trial_id,
                "evidence_kind": evidence["evidence_kind"],
                "validity": evidence["validity"],
                "outcome": evidence["outcome"],
                "cleanup": evidence["cleanup"],
                "containment": evidence["containment"],
            }
        )
    )
    valid = evidence["validity"]["valid"]
    cleanup = evidence["cleanup"].get("verified") is True
    return 0 if valid and cleanup else 2


if __name__ == "__main__":
    raise SystemExit(main())
