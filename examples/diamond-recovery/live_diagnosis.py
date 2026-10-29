"""Evaluate all four compiled diagnosis agents on real Mininet/OVS telemetry.

Each trial is a fresh compiled run. ExperimentRuntime owns scheduling, native
inference, coordination, Run Evidence and teardown. Trusted harness setup and
fault injection do not confer any model action authority.
"""

from __future__ import annotations

import argparse
import json
import platform
from functools import partial
from pathlib import Path
from typing import Any

import yaml
from live_harness import DiamondLiveHarness

from examples.diamond_recovery import LINK_ENDPOINTS
from examples.diamond_recovery.diagnosis import evaluate_runtime
from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates.mininet_ovs.observations import MininetOVSObservations
from mininet_ai.substrates.mininet_ovs.runtime import MininetOVSRuntime
from mininet_ai.transports.http import HttpxJsonTransport

TRIALS = ("healthy", "s1-s2", "s2-s4", "missing-flow", "unavailable")


class TrialObservations(MininetOVSObservations):
    """Real collectors with recorded, trial-scoped trusted fault injection."""

    def __init__(self, plan, network: Any, *, trial: str, harness: DiamondLiveHarness):
        super().__init__(plan, network)
        self.trial = trial
        self.harness = harness
        self.injected = False
        harness.attach(network)
        for name, endpoints in LINK_ENDPOINTS.items():
            matches = [
                link
                for link in network.links
                if {link.intf1.node.name, link.intf2.node.name} == set(endpoints)
            ]
            if len(matches) != 1:
                raise RuntimeError(f"expected one deployed link for {name}")
            harness.links[name] = matches[0]
        harness.readiness()

    def snapshot(self):
        baseline = super().snapshot()
        if not self.injected:
            self.injected = True
            if self.trial in {"s1-s2", "s2-s4"}:
                self.harness.fault(self.trial)
            elif self.trial == "missing-flow":
                self.harness.remove_local_flow("s2")
            elif self.trial == "unavailable":
                self.harness.record_telemetry_outage("ovs.port-state")
        return baseline

    def collect(self, query):
        if self.trial == "unavailable" and query.name == "ovs.port-state":
            return {target: None for target in query.targets}
        return super().collect(query)


def run(args: argparse.Namespace) -> int:
    """Stop on any failed trial or unverified cleanup; never retry blindly."""
    root = args.evidence.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).with_name("experiment.yaml")
    document = yaml.safe_load(source.read_text())
    document["substrate"]["driver"] = "mininet-ovs"
    props = HttpxJsonTransport().get_json(
        f"{args.endpoint.rstrip('/')}/props",
        headers={"Accept": "application/json"},
        timeout_seconds=5,
        max_response_bytes=1_048_576,
    )
    if not isinstance(props, dict):
        raise TypeError("native service properties must be an object")
    model = document["blueprints"][0]["model"]
    model["name"] = args.model_name or props.get("model_alias")
    if not model["name"]:
        raise ValueError("specify --model-name when /props has no model_alias")
    model["parameters"] = {
        "endpoint": args.endpoint,
        "expectedIdentity": {
            "repository": args.model_repository,
            "revision": args.model_revision,
            "artifact": args.model_artifact,
            "sha256": args.model_sha256,
        },
    }
    config = root / "experiment.yaml"
    config.write_text(yaml.safe_dump(document, sort_keys=False))
    plan = compile_experiment(config)
    (root / "observed-props.json").write_text(json.dumps(props, indent=2) + "\n")
    (root / "evaluation-provenance.json").write_text(
        json.dumps(
            {
                "serverProperties": props,
                "runnerHost": platform.uname()._asdict(),
                "modelServerHardware": "unverified; not inferred from the runner host",
                "backend": "unavailable unless exposed by server properties",
                "serverLaunch": "unavailable; existing service left unchanged",
                "artifactHashSource": "operator declaration; not independently hashed by this runner",
                "warmth": "each runtime verifies the native interface before scheduled decisions",
                "coldLatency": "not measured by this run",
            },
            indent=2,
        )
        + "\n"
    )

    for trial in TRIALS:
        harness = DiamondLiveHarness(root / f"{trial}-harness")

        substrate = MininetOVSRuntime(
            observation_factory=partial(TrialObservations, trial=trial, harness=harness)
        )
        summary = evaluate_runtime(
            plan,
            substrate,
            root / trial,
            timeout_seconds=args.trial_timeout,
        )
        # The real Substrate, not the harness, owns and verifies teardown.
        harness.net = None
        print(json.dumps({"trial": trial, **summary}, sort_keys=True))
        if not summary["passed"]:
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--model-name")
    parser.add_argument("--model-repository", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-artifact", required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--trial-timeout", type=float, default=30)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
