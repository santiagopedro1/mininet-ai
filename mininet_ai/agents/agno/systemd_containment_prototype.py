"""THROWAWAY authorized transient-user-service containment probe.

Run directly with Python. Creates only uniquely named disposable probe units.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import containment_prototype as fixture


def command(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{args}: {result.stderr}")
    return {"argv": list(args), "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def properties(unit):
    result = command("systemctl", "--user", "show", unit,
                     "--property=ActiveState,SubState,Result,MainPID,ControlGroup,KillMode,"
                     "TimeoutStopUSec,SendSIGKILL,RuntimeMaxUSec,Type")
    return dict(line.split("=", 1) for line in result["stdout"].splitlines() if "=" in line)


def probe(root, scenario):
    root.mkdir()
    unit = f"mininet-ai-containment-prototype-{uuid.uuid4().hex}.service"
    mode = "prearm_guarded" if scenario == "prearm_main_loss" else "descendants"
    history = []
    started = time.monotonic()
    try:
        history.append(command(
            "systemd-run", "--user", f"--unit={unit}", "--service-type=exec",
            "--property=KillMode=control-group", "--property=TimeoutStopSec=1s",
            "--property=SendSIGKILL=yes", "--property=RuntimeMaxSec=15s",
            sys.executable, fixture.HERE, "supervisor", str(root), mode, "0"))
        event = "worker_prearm" if mode.startswith("prearm") else "ready"
        assert fixture.until(lambda: any(r["event"] == event for r in fixture.rows(root)))
        before = properties(unit)
        cgroup = Path("/sys/fs/cgroup") / before["ControlGroup"].lstrip("/")
        membership = (cgroup / "cgroup.procs").read_text()
        pids = {r["pid"] for r in fixture.rows(root)}
        assert pids <= {int(pid) for pid in membership.split()}, "fixture escaped service cgroup"
        if scenario == "explicit_stop":
            history.append(command("systemctl", "--user", "stop", unit))
        else:
            history.append(command("systemctl", "--user", "kill", "--kill-whom=main",
                                   "--signal=SIGKILL", unit))
        exited = fixture.until(lambda: not any(map(fixture.live, pids)))
        after = properties(unit)
        # A unit-state transition alone is not proof that the cgroup is empty.
        empty = fixture.until(lambda: not cgroup.exists() or
                              "populated 0" in (cgroup / "cgroup.events").read_text())
        checks = {
            "fixture_pids_in_service_cgroup": True,
            "all_observed_fixture_processes_exited": exited,
            "cgroup_unpopulated_or_removed": empty,
            "unit_not_active": after["ActiveState"] in ("failed", "inactive"),
        }
        if scenario == "prearm_main_loss":
            checks["worker_never_ready"] = not any(
                r["event"] == "ready" for r in fixture.rows(root))
        else:
            checks["tracked_child_and_grandchild_present"] = all(
                any(r["event"] == event for r in fixture.rows(root))
                for event in ("child_ready", "grandchild_ready"))
        return {"scenario": scenario, "unit": unit, "before": before, "after": after,
                "cgroup_members_before": membership, "checks": checks,
                "trace": fixture.rows(root), "commands": history,
                "elapsed_seconds": time.monotonic() - started}
    finally:
        command("systemctl", "--user", "stop", unit, check=False)
        command("systemctl", "--user", "reset-failed", unit, check=False)
        pids = {r["pid"] for r in fixture.rows(root)}
        assert fixture.until(lambda: not any(map(fixture.live, pids))), "cleanup unverified"
        for pid in pids:
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


def main():
    fixture.prctl(36, 1)
    with tempfile.TemporaryDirectory(prefix="systemd-containment-prototype-",
                                     dir="/tmp/opencode") as tmp:
        cases = [probe(Path(tmp) / scenario, scenario) for scenario in (
            "prearm_main_loss", "ready_main_loss", "explicit_stop")]
    result = {
        "prototype": True, "platform": platform.platform(), "python": sys.version,
        "systemd": command("systemd-run", "--version")["stdout"],
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "fixture_sha256": hashlib.sha256(Path(fixture.HERE).read_bytes()).hexdigest(),
        "cases": cases,
        "limits": "Local modeled fixtures only; no Agno/gate/admission/Provider guarantees",
    }
    print(json.dumps(result, indent=2))
    assert all(value for case in cases for value in case["checks"].values())


if __name__ == "__main__":
    main()
