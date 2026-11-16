"""Diagnostic harness: intentionally red on legacy baseline false positives.

Run: python3 scripts/debug/diagnose-legacy-cleanup.py -v
Uses simulated network commands; this is not live host-cleanup evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]


class CleanupDiagnosisTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="cleanup-probe-", dir=ROOT)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.tmp = self.directory / "tmp"
        self.tmp.mkdir()
        self.state = self.directory / "state.json"
        self.baseline = self.directory / "baseline"
        self.script = self.directory / "checker.sh"
        original = (ROOT / "scripts/check-mininet-cleanup.sh").read_text()
        # Only bypass privilege and redirect host filesystem paths. Keep capture,
        # cleanup, comparison, and exit-status logic unchanged.
        self.script.write_text(
            original.replace("    require_root\n", "    : # isolated probe\n")
            .replace("/run/mininet-ai", str(self.directory / "runtime"))
            .replace("/tmp", str(self.tmp))
        )
        command = self.bin / "command"
        command.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "state = json.loads(pathlib.Path(os.environ['PROBE_STATE']).read_text())\n"
            "name = pathlib.Path(sys.argv[0]).name\n"
            "args = sys.argv[1:]\n"
            "if name == 'tc':\n"
            "    print(state['qdisc'])\n"
            "elif name == 'ovs-vsctl' and args[-1] == 'list-br':\n"
            "    print(state.get('bridge', ''))\n"
            "elif name == 'mn':\n"
            "    pathlib.Path(os.environ['PROBE_CLEANED']).touch()\n"
        )
        command.chmod(0o755)
        for name in ("ovs-vsctl", "ip", "tc", "ps", "mn"):
            (self.bin / name).symlink_to(command)
        self.environment = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PROBE_STATE": str(self.state),
            "PROBE_CLEANED": str(self.directory / "cleaned"),
        }
        self.set_state()

    def set_state(self, *, refcnt: int = 2, bridge: str = "") -> None:
        self.state.write_text(
            json.dumps(
                {
                    "qdisc": f"qdisc fq_codel 0: dev eth0 root refcnt {refcnt} limit 10240p",
                    "bridge": bridge,
                }
            )
        )

    def run_checker(self, action: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script), action, "--baseline", str(self.baseline)],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    def snapshot(self) -> None:
        result = self.run_checker("snapshot")
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_recovery_passes(self) -> None:
        result = self.run_checker("recover")
        self.assertTrue((self.directory / "cleaned").exists())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unchanged_network_passes(self) -> None:
        self.snapshot()
        self.assert_recovery_passes()

    def test_qdisc_reference_count_only_does_not_fail_cleanup(self) -> None:
        self.snapshot()
        self.set_state(refcnt=3)
        self.assert_recovery_passes()

    def test_unrelated_log_does_not_fail_cleanup(self) -> None:
        self.state.write_text('{"qdisc": ""}')
        self.snapshot()
        (self.tmp / "unrelated-service.log").touch()
        self.assert_recovery_passes()

    def test_removing_unrelated_log_restores_success(self) -> None:
        self.state.write_text('{"qdisc": ""}')
        self.snapshot()
        log = self.tmp / "unrelated-service.log"
        log.touch()
        self.assertEqual(self.run_checker("recover").returncode, 1)
        log.unlink()
        self.assert_recovery_passes()

    def test_restoring_qdisc_reference_count_restores_success(self) -> None:
        self.snapshot()
        self.set_state(refcnt=3)
        self.assertEqual(self.run_checker("recover").returncode, 1)
        self.set_state(refcnt=2)
        self.assert_recovery_passes()

    def test_remaining_bridge_fails_cleanup(self) -> None:
        self.snapshot()
        self.set_state(bridge="s1")
        result = self.run_checker("recover")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("+ovs-bridge\ts1", result.stderr)


if __name__ == "__main__":
    unittest.main()
