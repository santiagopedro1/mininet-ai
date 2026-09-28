from __future__ import annotations

import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mininet_ai.agents import AgnoAgentFactory
from mininet_ai.sdk import AgentProviderError


class AgnoStorageTests(unittest.TestCase):
    def test_database_file_is_created_private(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "state" / "agno.sqlite3"

            AgnoAgentFactory(database_path=path)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_insecure_existing_database_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "agno.sqlite3"
            path.touch(mode=0o644)
            os.chmod(path, 0o644)

            with self.assertRaises(AgentProviderError) as caught:
                AgnoAgentFactory(database_path=path)

        self.assertEqual(caught.exception.code, "agent.agno.database-invalid")


if __name__ == "__main__":
    unittest.main()
