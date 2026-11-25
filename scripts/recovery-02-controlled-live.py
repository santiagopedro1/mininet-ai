"""One live-network mechanism check; scripted actions are NOT S2 acceptance.

The normal campaign runner, Mininet adapter, fault, packet verifier and cleanup
are retained. Only the S2 decision is replaced with a declared controlled output.
"""

import argparse
import asyncio
import json
from pathlib import Path

from examples.noms27.recovery_campaign.live import LiveRuntime
from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign


class ControlledLiveRuntime(LiveRuntime):
    evidence_kind = "mechanism_validation"

    async def decide_s2(self, context, remaining_seconds):
        repair = {
            "status": "repair",
            "actions": [
                {"switch": "s1", "in_port": 1, "out_port": 3},
                {"switch": "s3", "in_port": 1, "out_port": 2},
                {"switch": "s4", "in_port": 3, "out_port": 1},
            ],
        }
        return {
            "repair": repair,
            "request": {"kind": "controlled_mechanism", "observation": context},
            "response": {"kind": "scripted_action_not_model_output"},
            "tokens": {"input": None, "output": None, "reasoning": None},
            "provider": "scripted_mechanism_no_incident_inference",
            "remote_terminal": "not_applicable_no_incident_inference",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    config = CampaignConfig.model_validate_json(args.config.read_text())
    if config.scenario != "diamond_s1_s2_link_broader" or config.treatment != "s2_only":
        parser.error("controlled check requires the broader direct-S2 diamond")
    if config.purpose != "live_feasibility":
        parser.error("controlled check requires explicit live-feasibility permission")
    evidence = asyncio.run(
        run_campaign(config, args.evidence, runtime=ControlledLiveRuntime())
    )
    print(
        json.dumps(
            {
                "evidence_kind": evidence["evidence_kind"],
                **{
                    key: evidence[key]
                    for key in ("validity", "outcome", "cleanup", "containment")
                },
            }
        )
    )


if __name__ == "__main__":
    main()
