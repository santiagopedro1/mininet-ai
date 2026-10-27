"""Validated read-only ledger JSON shared by exports and local inspection.

Callers own source identity validation and writer/exclusive-reader coordination.
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mininet_ai.runtime.ledger import LedgerRecord, RunManifest


def read_ledger(
    path: Path, run_id: str, *, partial_inspection: bool = False
) -> dict[str, Any]:
    """Strict by default; inspection alone may retain independently valid rows.

    Partial reads never bypass database, manifest identity or row attribution
    checks. Rejected JSON and sequence gaps are explicit, not repaired tails.
    """
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
        problems = []
        manifest = None
        if row:
            try:
                manifest = RunManifest.model_validate_json(row[0]).model_dump(
                    mode="json", by_alias=True
                )
            except ValidationError:
                if not partial_inspection:
                    raise
                # A malformed manifest may still explicitly identify another run.
                # Never turn that conflict into an ordinary missing-manifest gap.
                try:
                    raw_manifest = json.loads(row[0])
                except json.JSONDecodeError:
                    raw_manifest = None
                if isinstance(raw_manifest, dict) and any(
                    key in raw_manifest and raw_manifest[key] != run_id
                    for key in ("runId", "run_id")
                ):
                    raise ValueError("ledger manifest identity mismatch")
                problems.append(
                    {
                        "reference": f"{path}#manifest",
                        "reason": "Malformed run manifest; manifest identity, start time and provenance unavailable",
                    }
                )
        if manifest is not None and manifest["runId"] != run_id:
            raise ValueError("ledger manifest identity mismatch")
        records = []
        for index, (sequence, category, kind, timestamp, correlation, raw) in enumerate(
            connection.execute(
                "SELECT sequence,category,type,recorded_at,correlation_id,record_json "
                "FROM records WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ),
            1,
        ):
            reference = f"{path}#sequence={sequence}"
            if sequence != index:
                if not partial_inspection:
                    raise ValueError("ledger record identity/sequence mismatch")
                problems.append(
                    {
                        "reference": reference,
                        "reason": "Ledger sequence gap; totals and execution conclusion unknown",
                    }
                )
            try:
                record = LedgerRecord.model_validate_json(raw).model_dump(
                    mode="json", by_alias=True
                )
            except ValidationError:
                if not partial_inspection:
                    raise
                problems.append(
                    {
                        "reference": reference,
                        "reason": "Malformed ledger record; classification unavailable",
                    }
                )
                continue
            if record["runId"] != run_id or record["sequence"] != sequence:
                raise ValueError("ledger record identity/sequence mismatch")
            if partial_inspection and (
                record["category"] != category
                or record["type"] != kind
                or datetime.fromisoformat(record["recordedAt"])
                != datetime.fromisoformat(timestamp)
                or record["correlationId"] != correlation
            ):
                raise ValueError("ledger record attribution mismatch")
            records.append(record)
        result = {
            "schema_version": 1,
            "run_id": run_id,
            "manifest": manifest,
            "records": records,
        }
        if partial_inspection:
            result["validation_problems"] = problems
        return result
