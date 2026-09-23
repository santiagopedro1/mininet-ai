from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from mininet_ai.compiler import compile_experiment
from mininet_ai.substrates import RunInfo, RunState
from mininet_ai.substrates.mininet_ovs.state import (
    STATE_API_VERSION,
    PersistedRun,
    ProcessOwner,
    RunStateStore,
    StateLockHeld,
    StateStoreError,
)
from tests.compiler.helpers import example_snapshot, experiment_from


class RunStateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.store = RunStateStore(root / "state", root / "runtime.lock")

    def record(self) -> PersistedRun:
        snapshot = example_snapshot()
        snapshot["substrate"]["driver"] = "mininet-ovs"
        plan = compile_experiment(experiment_from(snapshot))
        return PersistedRun(
            apiVersion=STATE_API_VERSION,
            run=RunInfo(
                id="state-test",
                substrate="mininet-ovs",
                plan_digest=plan.digest,
                state=RunState.DEPLOYING,
                started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            owner=ProcessOwner.current(),
            plan=plan.model_dump(mode="json", by_alias=True),
            processGroups=(),
        )

    def test_state_round_trips_atomically_with_private_permissions(self) -> None:
        self.store.acquire()
        self.addCleanup(self.store.release)

        self.store.write(self.record())

        self.assertEqual(self.store.read(), self.record())
        self.assertEqual(self.store.state_path.stat().st_mode & 0o777, 0o600)

    def test_lock_excludes_a_second_store_until_release(self) -> None:
        other = RunStateStore(self.store.state_directory, self.store.lock_path)
        self.addCleanup(other.release)
        self.store.acquire()

        self.assertTrue(other.is_locked())
        with self.assertRaises(StateLockHeld):
            other.acquire()

        self.store.release()
        self.assertFalse(other.is_locked())
        other.acquire()
        self.assertTrue(other.acquired)

    def test_process_identity_detects_live_and_reused_pids(self) -> None:
        current = ProcessOwner.current()

        self.assertTrue(current.is_alive())
        self.assertFalse(
            current.model_copy(
                update={"start_ticks": current.start_ticks + 1}
            ).is_alive()
        )

    def test_invalid_state_is_reported_without_guessing(self) -> None:
        self.store.state_directory.mkdir()
        self.store.state_path.write_text("not-json", encoding="utf-8")

        with self.assertRaises(StateStoreError):
            self.store.read()

    def test_clear_removes_the_active_record_and_empty_directory(self) -> None:
        self.store.acquire()
        self.addCleanup(self.store.release)
        self.store.write(self.record())
        abandoned_write = self.store.state_directory / ".mininet-ovs.old.tmp"
        abandoned_write.write_text("partial", encoding="utf-8")

        self.store.clear()

        self.assertFalse(self.store.state_path.exists())
        self.assertFalse(self.store.state_directory.exists())
        self.assertTrue(self.store.lock_path.exists())


if __name__ == "__main__":
    unittest.main()
