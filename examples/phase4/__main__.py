"""Run the rootless Phase 4 autonomous experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from mininet_ai.agents import (
    AgnoAgentFactory,
    create_agno_database,
    register_builtin_providers,
)
from mininet_ai.audit import AuditRecorder
from mininet_ai.compiler import compile_experiment
from mininet_ai.experiment import ExperimentRuntime, ExperimentRuntimeState
from mininet_ai.plugins import ProviderRegistries
from mininet_ai.runtime import LedgerAuditSink, SQLiteRunLedger, SQLiteSharedStateStore

from .runtime import CongestedFakeRuntime


EXPERIMENT = Path(__file__).with_name("experiment.yaml")
DEFAULT_LEDGER_DB = Path(".mininet-ai/phase4-demo-ledger.sqlite3")
DEFAULT_AGNO_DB = Path(".mininet-ai/phase4-demo-agno.sqlite3")
DEFAULT_SHARED_STATE_DB = Path(".mininet-ai/phase4-demo-state.sqlite3")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the rootless Phase 4 autonomous acceptance experiment."
    )
    parser.add_argument("--ledger-db", type=Path, default=DEFAULT_LEDGER_DB)
    parser.add_argument("--agno-db", type=Path, default=DEFAULT_AGNO_DB)
    parser.add_argument(
        "--shared-state-db",
        type=Path,
        default=DEFAULT_SHARED_STATE_DB,
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5,
        help="Maximum seconds to wait for the autonomous action.",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    plan = compile_experiment(EXPERIMENT)
    substrate = CongestedFakeRuntime()
    registries = ProviderRegistries()
    register_builtin_providers(registries, substrate)
    ledger = SQLiteRunLedger(options.ledger_db)
    state_store = SQLiteSharedStateStore(options.shared_state_db)
    owner = ExperimentRuntime(
        plan,
        substrate,
        registries,
        audit=AuditRecorder(LedgerAuditSink(ledger)),
        agent_factory=AgnoAgentFactory(db=create_agno_database(options.agno_db)),
        shared_state=state_store,
        ledger=ledger,
    )
    report = None
    records = ()
    try:
        run = owner.start()
        observed = substrate.action_observed.wait(options.timeout)
        report = owner.stop()
        records = ledger.records(run.id)
    finally:
        if owner.state == ExperimentRuntimeState.RUNNING:
            report = owner.stop(drain=False)
        state_store.close()
        ledger.close()

    assert report is not None
    payload = {
        "report": report.model_dump(mode="json", by_alias=True, exclude_none=True),
        "recordTypes": [record.type for record in records],
    }
    print(json.dumps(payload, indent=2))
    succeeded = (
        observed
        and report.state == ExperimentRuntimeState.STOPPED
        and report.continuous.completed >= 1
        and report.continuous.failed == 0
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
