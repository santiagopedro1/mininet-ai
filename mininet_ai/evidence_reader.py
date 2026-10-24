"""Validated read-only ledger JSON shared by exports and local inspection.

Callers own source identity validation and writer/exclusive-reader coordination.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from mininet_ai.runtime.ledger import LedgerRecord, RunManifest


def read_ledger(path: Path, run_id: str) -> dict[str, Any]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError(f"unsupported ledger database schema at {path}")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError(f"corrupt ledger database at {path}")
        row = connection.execute(
            "SELECT manifest_json FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        manifest = (
            RunManifest.model_validate_json(row[0]).model_dump(mode="json", by_alias=True)
            if row else None
        )
        records = [
            LedgerRecord.model_validate_json(row[0]).model_dump(mode="json", by_alias=True)
            for row in connection.execute(
                "SELECT record_json FROM records WHERE run_id=? ORDER BY sequence", (run_id,)
            )
        ]
        if manifest is not None and manifest["runId"] != run_id:
            raise ValueError("ledger manifest identity mismatch")
        if any(record["runId"] != run_id or record["sequence"] != index
               for index, record in enumerate(records, 1)):
            raise ValueError("ledger record identity/sequence mismatch")
        return {"schema_version": 1, "run_id": run_id,
                "manifest": manifest, "records": records}
