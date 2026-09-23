"""Canonical rendering helpers shared by golden-plan tests and their updater."""

from __future__ import annotations

import json
from pathlib import Path

from mininet_ai.compiler import compile_experiment


ROOT = Path(__file__).parents[1]
GOLDEN_DIRECTORY = ROOT / "tests" / "golden"
GOLDEN_CASES = {
    "phase1": (
        ROOT / "examples" / "phase1" / "experiment.yaml",
        GOLDEN_DIRECTORY / "phase1-deployment-plan.json",
    )
}


def render_golden_plan(source: Path) -> str:
    """Compile and canonically serialize a plan for reviewable Git diffs."""

    plan = compile_experiment(source)
    payload = plan.model_dump(by_alias=True, mode="json", exclude_none=True)
    payload["source"] = Path(plan.source).relative_to(ROOT).as_posix()
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
