"""THROWAWAY GH-99 capability fixture. NEVER MERGE. No Ollama or inference."""

import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def namespaces():
    return {
        name: os.readlink(f"/proc/self/ns/{name}")
        for name in ("net", "pid", "mnt", "user", "cgroup")
    }


def fixture():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child_code = """
import json, os, signal, socket, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
s = socket.socket()
s.bind(('127.0.0.1', 0))
s.listen(1)
Path('/evidence/child.json').write_text(json.dumps({
    'pid': os.getpid(), 'listener': s.getsockname(), 'ignores_sigterm': True
}))
while True: time.sleep(0.1)
"""
    child = subprocess.Popen([sys.executable, "-I", "-S", "-c", child_code])
    network = {}
    with socket.socket() as connection:
        connection.settimeout(0.5)
        try:
            # TEST-NET, inside a mandatory fresh network namespace only.
            connection.connect(("192.0.2.1", 9))
            network = {"unexpected_connect": True}
        except OSError as exc:
            network = {"errno": exc.errno, "error": str(exc)}
    status = Path("/proc/self/status").read_text()
    write_json(Path("/evidence/fixture.json"), {
        "pid": os.getpid(), "child_pid": child.pid,
        "namespaces": namespaces(), "devices": sorted(os.listdir("/dev")),
        "network_probe": network, "ignores_sigterm": True,
        "sys_contents": os.listdir("/sys"),
        "run_contents": os.listdir("/run"),
        "home_contents": os.listdir("/home"),
        "status": [line for line in status.splitlines()
                   if line.startswith(("Cap", "NoNewPrivs", "Seccomp"))],
        "cgroup": Path("/proc/self/cgroup").read_text(),
        "routes": Path("/proc/net/route").read_text(),
        "mountinfo": Path("/proc/self/mountinfo").read_text(),
    })
    while True:
        time.sleep(0.1)


def process(pid):
    base = Path(f"/proc/{pid}")
    try:
        stat = (base / "stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        return {
            "pid": pid, "state": fields[0], "start_ticks": fields[19],
            "exe": os.readlink(base / "exe"),
            "cmdline": (base / "cmdline").read_bytes().decode().split("\0"),
            "cgroup": (base / "cgroup").read_text(),
        }
    except (FileNotFoundError, ProcessLookupError):
        return {"pid": pid, "absent_or_exited_during_read": True}


def controller():
    start = time.monotonic()
    root = Path("/tmp/opencode/PROTOTYPE-GH99") / ("capability-" + uuid.uuid4().hex)
    root.mkdir(parents=True, mode=0o700)
    evidence = root / "sandbox-evidence"
    evidence.mkdir(mode=0o700)
    source = Path(__file__).resolve()
    retained_source = root / source.name
    retained_source.write_bytes(source.read_bytes())
    unit = "prototype-gh99-" + uuid.uuid4().hex + ".service"
    report = {
        "throwaway": True, "unit": unit, "root": str(root),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "host": {"system": platform.system(), "release": platform.release(),
                 "machine": platform.machine(), "namespaces": namespaces()},
        "commands": [], "checks": {}, "coverage": "one harmless capability fixture only",
    }
    samples = []
    stop_sampling = threading.Event()
    launched = False

    def run(argv, timeout=10):
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        report["commands"].append({
            "argv": argv, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
            "elapsed": time.monotonic() - start,
        })
        return completed

    def show():
        result = run(["systemctl", "--user", "show", unit,
                      "--property=Id,LoadState,ActiveState,SubState,Result,MainPID,"
                      "ControlGroup,InvocationID,ExecMainCode,ExecMainStatus,"
                      "KillMode,TimeoutStopUSec,RuntimeMaxUSec,MemoryMax,TasksMax,"
                      "Restart,SendSIGKILL,NoNewPrivileges"])
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    try:
        for argv in (["systemctl", "--version"], ["bwrap", "--version"]):
            run(argv)
        sandbox = [
            "/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--disable-userns",
            "--assert-userns-disabled", "--cap-drop", "ALL", "--new-session",
            "--die-with-parent", "--ro-bind", "/usr", "/usr",
            "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib", "/lib64", "--proc", "/proc",
            "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/sys",
            "--dir", "/run", "--dir", "/home", "--chdir", "/",
            "--ro-bind", str(retained_source), "/fixture.py",
            "--bind", str(evidence), "/evidence", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C",
            "/usr/bin/python3", "-I", "-S", "/fixture.py", "--fixture",
        ]
        command = [
            "systemd-run", "--user", "--no-ask-password", "--unit=" + unit,
            "--description=THROWAWAY GH99 harmless containment capability fixture",
            "--property=RuntimeMaxSec=50", "--property=MemoryMax=128M",
            "--property=TasksMax=16", "--property=TimeoutStopSec=3",
            "--property=KillMode=control-group", "--property=SendSIGKILL=yes",
            "--property=Restart=no", "--property=NoNewPrivileges=yes",
            "--property=StandardOutput=append:" + str(root / "service.stdout"),
            "--property=StandardError=append:" + str(root / "service.stderr"),
            "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", *sandbox,
        ]
        # Exactly one launch; no fallback to weaker sandbox settings or privilege.
        launched = True
        launch = run(command)
        report["checks"]["service_launch_command_succeeded"] = launch.returncode == 0
        state = show()
        report["unit_before"] = state
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if (evidence / "fixture.json").exists() and (evidence / "child.json").exists():
                break
            time.sleep(0.05)
        ready = (evidence / "fixture.json").exists() and (evidence / "child.json").exists()
        report["checks"]["sandbox_and_child_ready"] = ready
        state = show()
        report["unit_running"] = state
        group = state.get("ControlGroup", "")
        expected = "/" + unit
        if not group.endswith(expected) or ".." in Path(group).parts:
            raise RuntimeError("Missing or unexpected fixture cgroup; no cgroup inspection")
        cgroup = Path("/sys/fs/cgroup") / group.lstrip("/")
        report["cgroup"] = str(cgroup)
        report["cgroup_before"] = {
            name: (cgroup / name).read_text()
            for name in ["cgroup.events", "cgroup.type", "memory.max", "pids.max"]
        }
        pids = sorted({int(pid) for directory in [cgroup, *cgroup.rglob("*")]
                       if directory.is_dir()
                       for pid in (directory / "cgroup.procs").read_text().split()})
        report["processes_before"] = [process(pid) for pid in pids]
        report["checks"]["owned_process_inventory_present"] = len(pids) >= 3
        report["checks"]["memory_and_task_limits"] = (
            report["cgroup_before"]["memory.max"].strip() == "134217728"
            and report["cgroup_before"]["pids.max"].strip() == "16")
        if ready:
            inside = json.loads((evidence / "fixture.json").read_text())
            report["fixture"] = inside
            report["child"] = json.loads((evidence / "child.json").read_text())
            report["checks"]["distinct_namespaces"] = all(
                inside["namespaces"][name] != report["host"]["namespaces"][name]
                for name in report["host"]["namespaces"])
            report["checks"]["test_net_connect_network_unreachable"] = (
                inside["network_probe"].get("errno") == 101)
            report["checks"]["host_control_paths_hidden"] = all(
                not inside[name] for name in ["sys_contents", "run_contents", "home_contents"])
            report["checks"]["no_accelerator_device_nodes_exposed"] = set(inside["devices"]) <= {
                "core", "fd", "full", "null", "ptmx", "pts", "random", "shm",
                "stderr", "stdin", "stdout", "tty", "urandom", "zero"}
            report["checks"]["no_new_privileges_no_effective_caps"] = (
                "NoNewPrivs:\t1" in inside["status"]
                and "CapEff:\t0000000000000000" in inside["status"])

        def sample():
            while not stop_sampling.is_set():
                try:
                    event = (cgroup / "cgroup.events").read_text().strip()
                except FileNotFoundError:
                    event = "cgroup_removed"
                samples.append({"elapsed": time.monotonic() - start, "events": event})
                stop_sampling.wait(0.01)

        sampler = threading.Thread(target=sample)
        sampler.start()
        try:
            before_stop = time.monotonic()
            stopped = run(["systemctl", "--user", "--no-ask-password", "stop", unit], timeout=10)
            report["stop_elapsed"] = time.monotonic() - before_stop
            report["checks"]["stop_command_succeeded"] = stopped.returncode == 0
            report["unit_after"] = show()
            report["processes_after"] = [process(pid) for pid in pids]
            report["checks"]["all_sampled_fixture_processes_exited"] = all(
                after.get("absent_or_exited_during_read")
                or after.get("start_ticks") != before.get("start_ticks")
                or after.get("state") == "Z"
                for before, after in zip(report["processes_before"], report["processes_after"]))
            report["checks"]["unit_not_active"] = report["unit_after"].get("ActiveState") in {
                "inactive", "failed"}
            report["checks"]["cgroup_empty_or_removed"] = (
                not cgroup.exists()
                or "populated 0" in (cgroup / "cgroup.events").read_text())
        finally:
            stop_sampling.set()
            sampler.join(timeout=2)
            report["cgroup_samples"] = samples
        run(["journalctl", "--user", "--unit=" + unit, "--no-pager", "--output=short-monotonic"], timeout=10)
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if launched:
            # Only the newly generated exact unit name; never a wildcard or sibling.
            try:
                run(["systemctl", "--user", "--no-ask-password", "stop", unit], timeout=10)
                report["final_unit"] = show()
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        report["elapsed"] = time.monotonic() - start
        report["passed"] = bool(report["checks"]) and all(report["checks"].values()) and not (
            report.get("error") or report.get("cleanup_error"))
        write_json(root / "report.json", report)
        print(json.dumps({"report": str(root / "report.json"), "passed": report["passed"],
                          "checks": report["checks"], "error": report.get("error")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--fixture"]:
        fixture()
    elif not sys.argv[1:]:
        raise SystemExit(controller())
    else:
        raise SystemExit("No arguments accepted except internal --fixture mode")
