"""Run the rootless Phase 3 example as one complete workflow."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Sequence

from mininet_ai.agents import OneShotAgentRuntime, register_builtin_providers
from mininet_ai.audit import AuditRecorder, JsonLinesAuditSink
from mininet_ai.compiler import compile_experiment
from mininet_ai.plugins import ProviderRegistries, discover_plugins
from mininet_ai.sdk import InvocationStatus
from mininet_ai.substrates import FakeSubstrateRuntime


EXPERIMENT = Path(__file__).with_name("experiment.yaml")
DEFAULT_AUDIT_LOG = Path(".mininet-ai/phase3-demo-audit.jsonl")


def _entry_points() -> tuple[EntryPoint, ...]:
    group = "mininet_ai.capabilities"
    return (
        EntryPoint(
            name="example.telemetry",
            value="examples.phase3.providers:telemetry_plugin",
            group=group,
        ),
        EntryPoint(
            name="example.action",
            value="examples.phase3.providers:action_plugin",
            group=group,
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the complete rootless Phase 3 acceptance experiment."
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=DEFAULT_AUDIT_LOG,
        help=f"JSONL audit destination (default: {DEFAULT_AUDIT_LOG})",
    )
    parser.add_argument(
        "--intent",
        default="Inspect s1 and apply the declared safe change",
        help="Intent passed to the edge-operator agent.",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    options.audit_log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    plan = compile_experiment(EXPERIMENT)
    substrate = FakeSubstrateRuntime()
    run = substrate.deploy(plan)
    teardown = None
    try:
        registries = ProviderRegistries()
        register_builtin_providers(registries, substrate)
        loaded = discover_plugins(registries, entry_points=_entry_points())
        runtime = OneShotAgentRuntime(
            plan,
            substrate,
            registries,
            audit=AuditRecorder(JsonLinesAuditSink(options.audit_log, sync=True)),
        )
        result = runtime.invoke(run.id, "edge-operator@s1", options.intent)
    finally:
        teardown = substrate.teardown(run.id)

    payload = {
        "auditLog": str(options.audit_log),
        "loadedPlugins": [plugin.name for plugin in loaded],
        "result": result.model_dump(mode="json", by_alias=True, exclude_none=True),
        "teardown": teardown.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.status == InvocationStatus.SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
