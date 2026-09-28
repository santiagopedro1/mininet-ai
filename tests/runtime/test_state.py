from __future__ import annotations

import stat
import sqlite3
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from mininet_ai.runtime import (
    InMemorySharedStateStore,
    SharedStateAccess,
    SharedStateError,
    SharedStateStore,
    SQLiteSharedStateStore,
)
from mininet_ai.sdk import SharedStateUpdate


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def access(
    *,
    run_id: str = "run-1",
    deployment: str = "routers",
    agent_id: str = "router@s1",
    limit: int = 10,
) -> SharedStateAccess:
    return SharedStateAccess(
        run_id=run_id,
        deployment=deployment,
        agent_id=agent_id,
        limits={"run": limit, "deployment": limit},
    )


class SharedStateStoreContractTests(unittest.TestCase):
    def stores(self, temporary: str) -> tuple[SharedStateStore, ...]:
        return (
            InMemorySharedStateStore(clock=lambda: NOW),
            SQLiteSharedStateStore(
                Path(temporary) / "state.sqlite3",
                clock=lambda: NOW,
            ),
        )

    def test_scopes_are_isolated_and_run_state_is_shared(self) -> None:
        with TemporaryDirectory() as temporary:
            for store in self.stores(temporary):
                with self.subTest(store=type(store).__name__):
                    first = access()
                    store.apply(
                        first,
                        (
                            SharedStateUpdate(
                                scope="run",
                                key="active-route",
                                value="west",
                                expectedVersion=0,
                            ),
                            SharedStateUpdate(
                                scope="deployment",
                                key="leader",
                                value="s1",
                            ),
                        ),
                    )

                    peer = store.snapshot(
                        access(deployment="observers", agent_id="observer@s1")
                    )
                    other_run = store.snapshot(access(run_id="run-2"))

                    self.assertEqual(peer.run["active-route"].value, "west")
                    self.assertEqual(peer.deployment, {})
                    self.assertEqual(other_run.run, {})
                    store.close()

    def test_compare_and_set_capacity_and_batches_are_atomic(self) -> None:
        with TemporaryDirectory() as temporary:
            for store in self.stores(temporary):
                with self.subTest(store=type(store).__name__):
                    authorized = access(limit=1)
                    first = store.apply(
                        authorized,
                        (
                            SharedStateUpdate(
                                scope="run",
                                key="route",
                                value="west",
                                expectedVersion=0,
                            ),
                        ),
                    )[0]
                    self.assertEqual(first.version, 1)

                    with self.assertRaises(SharedStateError) as conflict:
                        store.apply(
                            authorized,
                            (
                                SharedStateUpdate(
                                    scope="run",
                                    key="route",
                                    value="east",
                                    expectedVersion=9,
                                ),
                            ),
                        )
                    self.assertEqual(conflict.exception.code, "state.version.conflict")

                    with self.assertRaises(SharedStateError) as capacity:
                        store.apply(
                            authorized,
                            (
                                SharedStateUpdate(
                                    scope="run",
                                    key="route",
                                    value="east",
                                    expectedVersion=1,
                                ),
                                SharedStateUpdate(
                                    scope="run",
                                    key="second",
                                    value=True,
                                ),
                            ),
                        )
                    self.assertEqual(capacity.exception.code, "state.capacity.exceeded")
                    snapshot = store.snapshot(authorized)
                    self.assertEqual(snapshot.run["route"].value, "west")
                    self.assertEqual(snapshot.run["route"].version, 1)
                    store.close()

    def test_undeclared_scope_is_rejected(self) -> None:
        store = InMemorySharedStateStore(clock=lambda: NOW)
        deployment_only = SharedStateAccess(
            run_id="run-1",
            deployment="routers",
            agent_id="router@s1",
            limits={"deployment": 10},
        )

        with self.assertRaises(SharedStateError) as forbidden:
            store.apply(
                deployment_only,
                (SharedStateUpdate(scope="run", key="route", value="west"),),
            )

        self.assertEqual(forbidden.exception.code, "state.scope.forbidden")


class SQLiteSharedStateStoreTests(unittest.TestCase):
    def test_values_survive_reopen_in_owner_only_file(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "state" / "shared.sqlite3"
            store = SQLiteSharedStateStore(path, clock=lambda: NOW)
            store.apply(
                access(),
                (SharedStateUpdate(scope="run", key="load", value=72),),
            )
            store.close()

            reopened = SQLiteSharedStateStore(path, clock=lambda: NOW)
            try:
                entry = reopened.snapshot(access()).run["load"]
            finally:
                reopened.close()

            self.assertEqual(entry.value, 72)
            self.assertEqual(entry.updated_by, "router@s1")
            self.assertEqual(entry.updated_at, NOW)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_unsafe_path_and_unknown_schema_version_are_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            unsafe_path = Path(temporary) / "unsafe.sqlite3"
            unsafe_path.touch(mode=0o644)
            unsafe_path.chmod(0o644)
            with self.assertRaises(SharedStateError) as unsafe:
                SQLiteSharedStateStore(unsafe_path)
            self.assertEqual(unsafe.exception.code, "state.path.unsafe")

            versioned_path = Path(temporary) / "newer.sqlite3"
            connection = sqlite3.connect(versioned_path)
            connection.execute("PRAGMA user_version = 99")
            connection.close()
            versioned_path.chmod(0o600)
            with self.assertRaises(SharedStateError) as versioned:
                SQLiteSharedStateStore(versioned_path)
            self.assertEqual(versioned.exception.code, "state.version.unsupported")


if __name__ == "__main__":
    unittest.main()
