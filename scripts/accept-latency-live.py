"""Opt-in, process-bounded acceptance. Exit 77 means UNVERIFIED, never passed."""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from importlib import import_module
from pathlib import Path

from mininet_ai.compiler import compile_experiment
from mininet_ai.experiments.latency.runner import run_experiment
from mininet_ai.experiments.latency.trials import TrialConfig
from mininet_ai.experiments.latency.validation import validate_artifact
from mininet_ai.specification.models import Experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["recovery", "provider"], default="recovery")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--ollama-host")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (
        os.environ.get("MININET_AI_LATENCY_LIVE") != "1"
        or os.geteuid() != 0
        or importlib.util.find_spec("mininet") is None
        or any(
            shutil.which(c) is None for c in ("ovs-vsctl", "ovs-ofctl", "ip", "ping")
        )
    ):
        parser.exit(
            77,
            "UNVERIFIED: requires explicit MININET_AI_LATENCY_LIVE=1, root, Mininet, OVS, iproute2 and ping\n",
        )
    try:
        subprocess.run(
            ["ovs-vsctl", "--timeout=2", "show"],
            check=True,
            capture_output=True,
            timeout=3,
        )
    except OSError, subprocess.SubprocessError:
        parser.exit(77, "UNVERIFIED: Open vSwitch service unavailable\n")
    if not args.worker:
        try:
            result = subprocess.run(
                [sys.executable, __file__, *sys.argv[1:], "--worker"],
                timeout=200,
                check=False,
            )
        except subprocess.TimeoutExpired:
            parser.exit(
                1,
                "UNVERIFIED: process bound expired; preserve evidence and inspect/remove remaining resources manually before any retry\n",
            )
        raise SystemExit(result.returncode)
    example = import_module("examples.link-failure-latency.run")
    scenario = example.build_scenario(
        seed=0, model=args.model, ollama_host=args.ollama_host
    )
    if args.mode == "recovery":
        snapshot = scenario.plan.snapshot
        snapshot["blueprints"][0]["implementation"]["entrypoint"] = (
            "tests.experiments.factories:create_seed_zero_recovery_agent"
        )
        snapshot["blueprints"][0]["model"] = {
            "provider": "deterministic",
            "name": "deterministic",
            "parameters": {},
        }
        scenario = replace(
            scenario, plan=compile_experiment(Experiment.model_validate(snapshot))
        )
    summary = run_experiment(
        scenario,
        args.output,
        config=TrialConfig(
            measured=1,
            warmup=0,
            setup_seconds=60,
            injection_seconds=10,
            response_seconds=60,
            cleanup_seconds=30,
        ),
    )
    validation = validate_artifact(args.output)
    passed = (
        validation.integrity == "valid"
        and summary["state"] == "completed"
        and summary["counts"]["measured"]["outcomes"]["recovered"] == 1
    )
    if args.mode == "provider":
        passed = passed and any(
            r.get("type") == "provider_request_terminal"
            and r["data"]["evidence"] == "normal_response"
            for r in validation.records
        )
    print(
        json.dumps(
            {
                "mode": args.mode,
                "acceptance": "passed" if passed else "UNVERIFIED",
                "output": str(args.output),
            }
        )
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
