"""Run or validate one declared bounded recovery incident."""

import argparse
import asyncio
import json
from pathlib import Path

from .runner import CATALOGUE, CampaignConfig, run_campaign


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--check", action="store_true", help="validate without network or model access"
    )
    args = parser.parse_args()
    config = CampaignConfig.model_validate_json(args.config.read_text())
    if args.check:
        authority = {"catalogue": CATALOGUE}
        if config.treatment != "s1_only":
            from .s2 import PERMISSIONS, Repair, RouteDecision

            authority = {
                "permissions": PERMISSIONS,
                "repair_schema": (
                    RouteDecision.model_json_schema()
                    if config.s2_decision_interface == "route-v2"
                    else Repair.model_json_schema()
                ),
            }
            if config.combined:
                authority["catalogue"] = CATALOGUE
                authority["confidence_gate"] = config.confidence_gate
        print(
            json.dumps(
                {
                    "config": config.model_dump(mode="json"),
                    "packet_rate": config.packet_rate,
                    "budget_status": config.budget_status,
                    **authority,
                    "surviving_path": config.scenario_instance.structural_check()[
                        "broader_path"
                    ]
                    if config.scenario_instance
                    else ["h1", "s1", "s3", "s4", "h2"],
                    "structural_check": config.scenario_instance.structural_check()
                    if config.scenario_instance
                    else None,
                }
            )
        )
        return 0
    if args.evidence is None:
        parser.error("--evidence is required unless --check is used")
    if config.purpose != "live_feasibility":
        parser.error(
            "CLI execution requires purpose=live_feasibility; scripted tests are mechanism_validation"
        )
    evidence = asyncio.run(run_campaign(config, args.evidence))
    print(
        json.dumps(
            {
                key: evidence[key]
                for key in ("validity", "outcome", "cleanup", "containment")
            }
        )
    )
    return (
        0
        if evidence["validity"]["valid"] and evidence["cleanup"].get("verified") is True
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
