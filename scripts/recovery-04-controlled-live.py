"""Live confidence-gate boundary mechanism checks; scripted S1 is NOT acceptance.

The normal campaign runner, Mininet adapter, fault, packet verifier, contained
escalation, model-driven S2 route-v2 stage and cleanup are retained. Only the
S1 decision is replaced with a declared controlled output so the remaining
confidence-gate boundary conditions are deterministic on the live network:

- threshold equality (confidence == threshold) must proceed to local execution;
- missing confidence must escalate without local execution;
- invalid confidence must escalate without local execution.

The evidence kind is ``mechanism_validation``; the S1 model does not decide and
no gate threshold or effectiveness claim is selected.
"""

import argparse
import asyncio
import json
from pathlib import Path

from examples.noms27.recovery_campaign.confidence import valid_confidence
from examples.noms27.recovery_campaign.live import LiveRuntime
from examples.noms27.recovery_campaign.runner import CampaignConfig, run_campaign


class ControlledGateRuntime(LiveRuntime):
    """Scripted S1 decision; the S2 stage stays model-driven (route-v2)."""

    evidence_kind = "mechanism_validation"

    def __init__(self, scripted: dict):
        super().__init__()
        self.scripted = scripted

    async def decide(self, diagnostics, remaining_seconds):
        # Mirror the native S1 decision contract without contacting a provider.
        self.inference_request_id = self.config.trial_id + ":s1"
        confidence = self.scripted.get("confidence")
        return {
            **self.scripted,
            "confidence_present": "confidence" in self.scripted,
            "confidence_status": (
                "valid" if valid_confidence(confidence) else "missing_or_invalid"
            ),
            "request": {
                "kind": "controlled_mechanism",
                "observation": diagnostics,
            },
            "response": {"kind": "scripted_s1_not_model_output"},
            "tokens": {"input": None, "output": None, "reasoning": None},
            "provider": "scripted_mechanism_no_incident_inference",
            "remote_terminal": "not_applicable_no_incident_inference",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument(
        "--s1-decision",
        required=True,
        help='Scripted S1 decision JSON, e.g. \'{"choice":"bypass","confidence":0.5}\'',
    )
    args = parser.parse_args()
    config = CampaignConfig.model_validate_json(args.config.read_text())
    if config.scenario != "diamond_s1_s2_link_broader" or config.treatment != (
        "combined_gated"
    ):
        parser.error(
            "controlled gate check requires the broader combined-gated diamond"
        )
    if not config.confidence_gate_enabled or config.confidence_threshold is None:
        parser.error("controlled gate check requires an enabled confidence gate")
    if config.purpose != "live_feasibility":
        parser.error("controlled check requires explicit live-feasibility permission")
    scripted = json.loads(args.s1_decision)
    if not isinstance(scripted, dict) or scripted.get("choice") != "bypass":
        parser.error("scripted S1 decision must select the executable 'bypass' choice")
    confidence = scripted.get("confidence")
    if valid_confidence(confidence) and float(confidence) != (
        config.confidence_threshold
    ):
        parser.error(
            "scripted valid confidence is only permitted at exact threshold equality"
        )
    evidence = asyncio.run(
        run_campaign(config, args.evidence, runtime=ControlledGateRuntime(scripted))
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
