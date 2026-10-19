"""CLI configuration without privileged work during validation/inspection."""

import argparse
import json
import os
from pathlib import Path

from .runner import run_experiment
from .trials import TrialConfig


def run_cli(builder, default_specification):
    parser = argparse.ArgumentParser(description="Bounded v2 link-failure Run Evidence")
    parser.add_argument(
        "specification", nargs="?", type=Path, default=default_specification
    )
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--generator-version",
        choices=["sha256-counter-v1"],
        default="sha256-counter-v1",
    )
    parser.add_argument("--measured", "--trials", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=1)
    for stage, value in (
        ("setup", 120),
        ("injection", 10),
        ("response", 120),
        ("cleanup", 30),
    ):
        parser.add_argument(f"--{stage}-budget", type=float, default=value)
    parser.add_argument("--sample-period", type=float, default=0.1)
    parser.add_argument("--probe-period", type=float, default=1)
    parser.add_argument("--model")
    parser.add_argument("--ollama-host")
    parser.add_argument(
        "--output", type=Path, help="Fresh directory, never an existing run"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--spec", action="store_true")
    args = parser.parse_args()
    try:
        config = TrialConfig(
            measured=args.measured,
            warmup=args.warmup,
            setup_seconds=args.setup_budget,
            injection_seconds=args.injection_budget,
            response_seconds=args.response_budget,
            cleanup_seconds=args.cleanup_budget,
            sample_seconds=args.sample_period,
            probe_seconds=args.probe_period,
        )
        scenario = builder(
            seed=args.seed,
            specification=args.specification,
            model=args.model,
            ollama_host=args.ollama_host,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.spec:
        print(json.dumps(scenario.plan.snapshot, indent=2))
        return
    if args.plan:
        print(scenario.plan.model_dump_json(by_alias=True, indent=2))
        return
    if args.output is None:
        parser.error("--output must name a fresh directory")
    if os.geteuid() != 0:
        parser.error(
            "live trials require root, Mininet, running Open vSwitch, iproute2, ping, and the configured Provider"
        )
    try:
        summary = run_experiment(scenario, args.output, config=config)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(
            1, f"Run Evidence failed; retain partial output for validation: {error}\n"
        )
    print(
        json.dumps(
            {
                "state": summary["state"],
                "counts": summary["counts"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    if summary["state"] != "completed":
        parser.exit(1, f"run aborted: {summary['terminal_reason']}\n")
