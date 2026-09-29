"""Run the rootless Phase 5 coordination acceptance experiment."""

from __future__ import annotations

import json
from pathlib import Path

from mininet_ai.agents import register_builtin_providers
from mininet_ai.compiler import compile_experiment
from mininet_ai.experiment import ExperimentRuntime, ExperimentRuntimeState
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.substrates import ActionStatus, FakeSubstrateRuntime

EXPERIMENT = Path(__file__).with_name("experiment.yaml")
REQUESTED_AGENT = "primary-remediator@s1"


def main() -> int:
    plan = compile_experiment(EXPERIMENT)
    substrate = FakeSubstrateRuntime(run_id_factory=lambda: "phase5-demo-run")
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    owner = ExperimentRuntime(plan, substrate, registries)
    report = None
    try:
        owner.start()
        event = owner.submit_intent(
            REQUESTED_AGENT,
            "Coordinate a safe forwarding repair for s1.",
            source="phase5-demo",
        )
        report = owner.stop()
    finally:
        if owner.state == ExperimentRuntimeState.RUNNING:
            report = owner.stop(drain=False)

    assert report is not None
    record = report.continuous.invocations[0]
    outcome = record.coordination
    payload = {
        "event": event.model_dump(mode="json", by_alias=True),
        "report": report.model_dump(mode="json", by_alias=True, exclude_none=True),
    }
    print(json.dumps(payload, indent=2))
    if outcome is None:
        return 1
    action_results = tuple(
        result
        for invocation in outcome.invocations
        for result in invocation.result.action_results
    )
    decisions = outcome.arbitration.decisions if outcome.arbitration else ()
    succeeded = (
        report.state == ExperimentRuntimeState.STOPPED
        and report.continuous.completed == 1
        and report.continuous.failed == 0
        and outcome.entry_agent_id == "global-coordinator"
        and len(outcome.messages) == 3
        and len(outcome.invocations) == 3
        and [decision.disposition for decision in decisions]
        == ["execute", "reject"]
        and [result.status for result in action_results]
        == [ActionStatus.SUCCEEDED, ActionStatus.REJECTED]
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
