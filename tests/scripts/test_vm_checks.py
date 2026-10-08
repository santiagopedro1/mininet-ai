from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "test-vm.sh"


class VMCheckScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.bin = Path(self.temporary.name)
        self.environment = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "MOCK_BIN": str(self.bin),
            "PROJECT_ROOT": str(ROOT),
            "TEST_PYTHON": sys.executable,
        }
        self.write_command(
            "vagrant",
            f"""#!{sys.executable}
import os
import subprocess
import sys

if os.environ.get("FAIL_SSH"):
    sys.exit(255)
script = sys.stdin.read()
script = script.replace("cd /vagrant", "cd " + os.environ["PROJECT_ROOT"])
script = script.replace("scripts/vm-run.sh", os.environ["MOCK_BIN"] + "/mock-vm-run")
script = script.replace("scripts/check-mininet-cleanup.sh", os.environ["MOCK_BIN"] + "/mock-cleanup")
sys.exit(subprocess.run(["/bin/bash", "-s"], input=script, text=True).returncode)
""",
        )
        self.write_command(
            "mock-vm-run",
            """#!/bin/bash
case "$*" in
    "${FAIL_COMMAND:-not-a-command}"*) exit 2 ;;
esac
if [[ "$1" == python && "$2" == -c && "$3" == *json.load* ]]; then
    exec "$TEST_PYTHON" "${@:2}"
fi
if [[ "$1" == mininet-ai ]]; then
    if [[ -n "${CLI_JSON:-}" ]]; then
        printf '%s\n' "$CLI_JSON"
    else
        printf '{}\n'
    fi
fi
exit 0
""",
        )
        self.write_command(
            "mock-cleanup",
            """#!/bin/bash
case "$1" in
    "${FAIL_CLEANUP:-not-an-action}") exit 3 ;;
esac
exit 0
""",
        )
        self.write_command(
            "sudo",
            """#!/bin/bash
if [[ "$1" == -n ]]; then shift; fi
if [[ "${FAIL_SUDO:-}" == 1 ]]; then exit 1; fi
exec "$@"
""",
        )
        self.write_command(
            "mn",
            """#!/bin/bash
if [[ "$*" == "${FAIL_MN:-not-a-command}" ]]; then exit 4; fi
if [[ "$*" == '--test pingall' ]]; then
    printf '*** Results: %s%% dropped\n' "${PING_LOSS:-0}"
fi
exit 0
""",
        )
        for command in (
            "ovs-vsctl",
            "ovs-ofctl",
            "ovs-testcontroller",
            "mnexec",
            "ip",
            "tc",
            "systemctl",
            "iperf",
            "iperf3",
            "tcpdump",
            "traceroute",
            "ethtool",
            "dig",
            "ss",
            "ping",
            "mtr",
            "nc",
        ):
            self.write_command(command, "#!/bin/bash\nexit 0\n")

    def write_command(self, name: str, content: str) -> None:
        file = self.bin / name
        file.write_text(content)
        file.chmod(0o755)

    def run_checks(self, **environment: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(SCRIPT)],
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def test_success_reports_each_step_and_summary(self) -> None:
        result = self.run_checks()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for step in (
            "Rootless test suite",
            "Lint",
            "Type checking",
            "Example validation",
            "Network tools",
            "Network diagnostic tools",
            "Live integration tests",
            "Mininet connectivity (pingall)",
            "Final cleanup and baseline verification",
        ):
            self.assertIn(f"PASS: {step}", result.stdout)
        self.assertIn("0 failed, 0 skipped", result.stdout)

    def test_independent_checks_continue_after_failure(self) -> None:
        result = self.run_checks(FAIL_COMMAND="pytest")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: Rootless test suite", result.stderr)
        self.assertIn("PASS: Lint", result.stdout)
        self.assertIn("PASS: Live integration tests", result.stdout)
        self.assertIn("1 failed, 0 skipped", result.stdout)

    def test_missing_diagnostic_tool_fails_without_skipping_live_checks(self) -> None:
        for command in ("bash", "env", "grep", "dirname", "true"):
            executable = shutil.which(command)
            assert executable is not None
            (self.bin / command).symlink_to(executable)
        (self.bin / "iperf3").unlink()
        result = self.run_checks(PATH=str(self.bin))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL: Network diagnostic tools", result.stderr)
        self.assertIn("iperf3", result.stderr)
        self.assertIn("PASS: Live integration tests", result.stdout)

    def test_network_prerequisite_failure_skips_destructive_tests(self) -> None:
        result = self.run_checks(FAIL_SUDO="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SKIP: Live network tests", result.stdout)
        self.assertNotIn("STEP: Initial Mininet cleanup", result.stdout)
        self.assertIn("PASS: Open vSwitch service", result.stdout)

    def test_initial_cleanup_failure_skips_live_tests(self) -> None:
        result = self.run_checks(FAIL_MN="-c")
        self.assertEqual(result.returncode, 1)
        self.assertIn("initial cleanup failed", result.stdout)
        self.assertNotIn("STEP: Live integration tests", result.stdout)

    def test_baseline_failure_skips_live_tests(self) -> None:
        result = self.run_checks(FAIL_CLEANUP="snapshot")
        self.assertEqual(result.returncode, 1)
        self.assertIn("baseline could not be recorded", result.stdout)
        self.assertNotIn("STEP: Live integration tests", result.stdout)

    def test_live_failure_still_checks_and_recovers_network(self) -> None:
        result = self.run_checks(FAIL_COMMAND="python -m unittest")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: Live integration tests", result.stderr)
        self.assertIn("PASS: Integration cleanup verification", result.stdout)
        self.assertIn("PASS: Final cleanup and baseline verification", result.stdout)

    def test_recovery_failure_skips_connectivity_and_attempts_cleanup(self) -> None:
        result = self.run_checks(FAIL_CLEANUP="recover")
        self.assertEqual(result.returncode, 1)
        self.assertIn("SKIP: Mininet connectivity (pingall)", result.stdout)
        self.assertIn("STEP: Final cleanup and baseline verification", result.stdout)
        self.assertIn("STEP: Emergency cleanup", result.stdout)
        self.assertIn("3 failed, 1 skipped", result.stdout)

    def test_ssh_failure_propagates_to_host(self) -> None:
        result = self.run_checks(FAIL_SSH="1")
        self.assertEqual(result.returncode, 255)
        self.assertIn("FAIL: VM verification (exit 255)", result.stderr)

    def test_packet_loss_fails_even_when_mininet_exits_successfully(self) -> None:
        result = self.run_checks(PING_LOSS="100")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: Mininet connectivity (pingall)", result.stderr)
        self.assertIn("PASS: Final cleanup and baseline verification", result.stdout)

    def test_invalid_cli_json_is_reported_and_checks_continue(self) -> None:
        result = self.run_checks(CLI_JSON="not-json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL: Example deployment plan", result.stderr)
        self.assertIn("FAIL: Schema: experiment", result.stderr)
        self.assertIn("PASS: Live integration tests", result.stdout)

    def test_missing_vagrant_fails_before_connecting(self) -> None:
        result = self.run_checks(PATH="/nonexistent")
        self.assertEqual(result.returncode, 1)
        self.assertIn("vagrant is not installed", result.stderr)
