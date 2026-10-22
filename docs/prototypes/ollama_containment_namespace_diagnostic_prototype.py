"""THROWAWAY GH-99: one observational namespace service. NEVER MERGE.

No interface changes, sockets/traffic tests, worker migration, acquired code,
permission changes, model loading, inference, downloads, journal read or deletion.
"""

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import time
import uuid


MEMORY = 128 * 1024**2
HELPER_SHA256 = "a0065e77b60ca3b31cba8afa9119aaf77334e4850b95c4d6c995fcf71364845d"
NS_GET_USERNS = 0xB701
NS_GET_PARENT = 0xB702
NS_GET_NSTYPE = 0xB703
sys.dont_write_bytecode = True
GUEST = sys.argv[1:2] == ["--namespace-diagnostic"]
helper_path = Path("/helper.py") if GUEST else Path(__file__).with_name("ollama_containment_service_fixtures_prototype.py")
helper_bytes = helper_path.read_bytes()
if hashlib.sha256(helper_bytes).hexdigest() != HELPER_SHA256:
    raise RuntimeError("Trusted fixture helper differs from the pinned failed-attempt source")
spec = importlib.util.spec_from_file_location("trusted_gh99_fixture_helper", helper_path)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def diagnostic_role(nonce, host_user, host_net):
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    namespaces = helper.namespaces()
    if namespaces["user"] == host_user or namespaces["net"] == host_net or os.getuid() != 0:
        raise RuntimeError("Diagnostic did not enter the intended private user/network namespaces")
    bus_absent = not any(key in os.environ for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"))
    if not bus_absent:
        raise RuntimeError("Controller bus environment leaked into diagnostic")
    status = helper.own_status()
    masks = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
             for line in status if line.startswith("Cap")}
    if "NoNewPrivs:\t1" not in status:
        raise RuntimeError("Diagnostic lacks requested no-new-privileges")
    helper.publish(Path("/evidence/ready.json"), {
        "nonce": nonce, "role": "namespace-diagnostic", "namespaces": namespaces,
        "uid_in_namespace": os.getuid(), "gid_in_namespace": os.getgid(),
        "uid_map": Path("/proc/self/uid_map").read_text(),
        "gid_map": Path("/proc/self/gid_map").read_text(),
        "status": status, "capability_masks": masks,
        "net_admin_effective": bool(int(masks["CapEff"], 16) & (1 << 12)),
        "setpcap_effective": bool(int(masks["CapEff"], 16) & (1 << 8)),
        "controller_bus_environment_absent": bus_absent,
        "cgroup": Path("/proc/self/cgroup").read_text(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "helper_sha256": HELPER_SHA256, "interface_setup_attempted": False,
    })
    # Deliberately hold without sockets, ioctl interface setters, worker joins or
    # capability changes; the controller observes namespace metadata and stops us.
    while True:
        time.sleep(0.1)


def main():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    started = time.monotonic()
    source = Path(__file__).resolve()
    repo = source.parents[2]
    output = repo / "docs/prototypes/ollama-containment-namespace-diagnostic.observations.json"
    if output.exists():
        raise RuntimeError("Diagnostic receipt exists; no automatic rerun or overwrite")
    uid = os.getuid()
    runtime = Path("/run/user") / str(uid)
    bus = runtime / "bus"
    rs, bs = runtime.lstat(), bus.lstat()
    if (not stat.S_ISDIR(rs.st_mode) or rs.st_uid != uid or stat.S_IMODE(rs.st_mode) != 0o700
            or not stat.S_ISSOCK(bs.st_mode) or bs.st_uid != uid):
        raise RuntimeError("Unapproved controller bus endpoint metadata")
    control_env = {"PATH": "/usr/bin:/bin", "LANG": "C", "XDG_RUNTIME_DIR": str(runtime),
                   "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(bus)}
    header = Path("/usr/include/linux/nsfs.h").read_bytes()
    if not re.search(rb"#define\s+NSIO\s+0xb7", header):
        raise RuntimeError("Unexpected installed namespace ioctl ABI header")
    for name, number in [("NS_GET_USERNS", 1), ("NS_GET_PARENT", 2), ("NS_GET_NSTYPE", 3)]:
        if not re.search(rb"#define\s+" + name.encode() + rb"\s+_IO\(NSIO,\s*0x" + str(number).encode() + rb"\)", header):
            raise RuntimeError("Namespace ioctl constants do not match installed header")
    stage_bytes = (repo / "docs/prototypes/ollama-containment-extraction.observations.json").read_bytes()
    stage = json.loads(stage_bytes)
    if not stage["success"]:
        raise RuntimeError("Staging prerequisite not verified")
    root = repo / ".prototype-gh99-artifacts" / ("namespace-diagnostic-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    evidence = root / "evidence"
    evidence.mkdir(mode=0o700)
    copied = root / "diagnostic.py"
    copied_helper = root / "helper.py"
    for target, data in [(copied, source.read_bytes()), (copied_helper, helper_bytes)]:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    host = helper.namespaces()
    nonce = uuid.uuid4().hex
    unit = "prototype-gh99-nsdiag-" + uuid.uuid4().hex + ".service"
    report = {"throwaway": True, "issue": 99, "scope": "one observational namespace service only",
              "started_utc": helper.now(), "root": str(root), "unit": unit, "nonce": nonce,
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "helper_sha256": HELPER_SHA256,
              "staging_receipt_sha256": hashlib.sha256(stage_bytes).hexdigest(),
              "nsfs_header_sha256": hashlib.sha256(header).hexdigest(),
              "host_namespaces": host, "commands": [], "checks": {},
              "controller_bus": {"runtime": str(runtime), "socket": str(bus), "uid": uid, "passed_to_guest": False},
              "controller_as_limit": list(resource.getrlimit(resource.RLIMIT_AS)),
              "interface_changes_or_tests": False, "socket_or_traffic_tests": False,
              "worker_namespace_migration": False, "acquired_code_executed_or_loaded": False,
              "acquired_permissions_changed": False, "model_loaded": False, "inference_performed": False,
              "network_or_download": False, "host_elevation_or_firewall_change": False,
              "journal_read": False, "deletion_performed": False}
    launched = False
    before = None
    handles = []

    def run(argv, timeout=8):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=control_env)
        report["commands"].append({"argv": argv, "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr,
                                   "elapsed_seconds": time.monotonic() - started})
        return result

    def show():
        result = run(["systemctl", "--user", "show", unit,
                      "--property=Id,InvocationID,LoadState,ActiveState,SubState,MainPID,ControlGroup,"
                      "MemoryMax,MemoryPeak,TasksMax,RuntimeMaxUSec,TimeoutStopUSec,Restart,"
                      "NoNewPrivileges,KillMode,SendSIGKILL,Result"])
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def expired(signum, frame):
        raise TimeoutError("Diagnostic controller two-minute budget reached")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        sandbox = helper.sandbox_args(copied, evidence, Path(stage["directory"]), "namespace-diagnostic",
                                      nonce, host["user"], host["net"])
        # Bind the pinned trusted helper before the root is remounted read-only.
        insertion = sandbox.index("--remount-ro")
        sandbox[insertion:insertion] = ["--ro-bind", str(copied_helper), "/helper.py"]
        command = ["systemd-run", "--user", "--no-ask-password", "--unit=" + unit,
                   "--description=THROWAWAY GH99 observational namespace diagnostic",
                   "--property=RuntimeMaxSec=50", "--property=TimeoutStopSec=3",
                   "--property=MemoryMax=128M", "--property=TasksMax=16", "--property=Restart=no",
                   "--property=NoNewPrivileges=yes", "--property=KillMode=control-group", "--property=SendSIGKILL=yes",
                   "--property=StandardOutput=append:" + str(evidence / "service.stdout"),
                   "--property=StandardError=append:" + str(evidence / "service.stderr"),
                   "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "LANG=C", *sandbox]
        if run(command).returncode != 0:
            raise RuntimeError("Diagnostic transient-unit creation command failed")
        launched = True
        report["initial_state"] = show()
        until = time.monotonic() + 8
        while time.monotonic() < until and not (evidence / "ready.json.complete").exists():
            time.sleep(0.02)
        if not (evidence / "ready.json.complete").exists():
            raise TimeoutError("Diagnostic did not publish complete pre-network evidence")
        ready = json.loads((evidence / "ready.json").read_text())
        report["ready"] = ready
        state = show()
        group = state.get("ControlGroup", "")
        if not group.endswith("/" + unit) or ".." in Path(group).parts:
            raise RuntimeError("Unexpected diagnostic cgroup identity")
        cg = Path("/sys/fs/cgroup") / group.lstrip("/")
        pids = sorted({int(pid) for directory in [cg, *cg.rglob("*")] if directory.is_dir()
                       for pid in (directory / "cgroup.procs").read_text().split()})
        before = {"unit_state": state, "cgroup_path": str(cg), "processes": [helper.process(pid) for pid in pids],
                  "cgroup_files": {name: (cg / name).read_text() for name in
                                   ("cgroup.events", "memory.max", "memory.peak", "memory.events", "pids.max")}}
        report["before_stop"] = before
        selected = [item for item in before["processes"] if "/fixture.py" in item.get("cmdline", [])
                    and "--namespace-diagnostic" in item["cmdline"]
                    and Path(item.get("exe", "")).name.startswith("python")]
        if len(selected) != 1:
            raise RuntimeError("Diagnostic fixture process is not unique in its owned cgroup")
        target = selected[0]
        report["target"] = target
        user_fd = os.open(f"/proc/{target['pid']}/ns/user", os.O_RDONLY)
        handles.append(user_fd)
        net_fd = os.open(f"/proc/{target['pid']}/ns/net", os.O_RDONLY)
        handles.append(net_fd)
        owner_fd = fcntl.ioctl(net_fd, NS_GET_USERNS)
        handles.append(owner_fd)
        parent_user_fd = fcntl.ioctl(user_fd, NS_GET_PARENT)
        handles.append(parent_user_fd)
        identity = lambda fd, kind: f"{kind}:[{os.fstat(fd).st_ino}]"
        ownership = {"target_user": identity(user_fd, "user"), "target_net": identity(net_fd, "net"),
                     "net_owning_user": identity(owner_fd, "user"),
                     "target_user_parent": identity(parent_user_fd, "user"),
                     "target_user_type": fcntl.ioctl(user_fd, NS_GET_NSTYPE),
                     "target_net_type": fcntl.ioctl(net_fd, NS_GET_NSTYPE),
                     "owner_type": fcntl.ioctl(owner_fd, NS_GET_NSTYPE)}
        ownership["net_owner_differs_from_current_user"] = ownership["net_owning_user"] != ownership["target_user"]
        ownership["net_owner_is_current_user_parent"] = ownership["net_owning_user"] == ownership["target_user_parent"]
        report["ownership"] = ownership
        after_handles = helper.process(target["pid"])
        report["target_after_namespace_queries"] = after_handles
        report["checks"].update({
            "correlated_live_process": after_handles.get("start_ticks") == target["start_ticks"]
                                       and after_handles.get("namespaces") == ready["namespaces"] == target["namespaces"],
            "private_namespaces": all(ready["namespaces"][name] != host[name] for name in ("user", "net", "pid", "mnt")),
            "namespace_handle_identities": ownership["target_user"] == ready["namespaces"]["user"]
                                           and ownership["target_net"] == ready["namespaces"]["net"],
            "expected_namespace_handle_types": ownership["target_user_type"] == ownership["owner_type"] == 0x10000000
                                               and ownership["target_net_type"] == 0x40000000,
            "fixture_source_identity": ready["source_sha256"] == report["source_sha256"] and ready["helper_sha256"] == HELPER_SHA256,
            "controller_bus_absent_in_guest": ready["controller_bus_environment_absent"],
            "observed_unit_limits": before["cgroup_files"]["memory.max"].strip() == str(MEMORY)
                                    and before["cgroup_files"]["pids.max"].strip() == "16"
                                    and state["RuntimeMaxUSec"] == "50s" and state["NoNewPrivileges"] == "yes",
            "no_interface_setup_attempted": not ready["interface_setup_attempted"],
        })
        report["observations_completed"] = True
    except Exception as error:
        report["observations_completed"] = False
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        for fd in reversed(handles):
            os.close(fd)
        report["namespace_observer_handles_closed_before_stop"] = True
        if launched:
            try:
                stop_result = run(["systemctl", "--user", "--no-ask-password", "stop", unit])
                after_state = show()
                report["stop"] = {"returncode": stop_result.returncode, "unit_after": after_state}
                if before:
                    cg = Path(before["cgroup_path"])
                    after = [helper.process(item["pid"]) for item in before["processes"]]
                    report["stop"]["processes_after"] = after
                    report["stop"]["cgroup_removed"] = not cg.exists()
                    report["stop"]["cgroup_events_after"] = (cg / "cgroup.events").read_text() if cg.exists() else None
                    exited = all(item.get("absent_or_exited_during_read") or item.get("start_ticks") != prior.get("start_ticks")
                                 or item.get("state") == "Z" for prior, item in zip(before["processes"], after))
                    report["checks"]["independent_owned_exit_observed"] = (
                        stop_result.returncode == 0 and exited
                        and (report["stop"]["cgroup_removed"] or "populated 0" in (report["stop"]["cgroup_events_after"] or ""))
                        and after_state.get("ActiveState") in ("inactive", "failed"))
            except Exception as error:
                report["cleanup_failure"] = {"type": type(error).__name__, "message": str(error)}
        signal.setitimer(signal.ITIMER_REAL, 0)
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_utc"] = helper.now()
        report["controller_status"] = helper.own_status()
        report["service_stderr"] = (evidence / "service.stderr").read_text() if (evidence / "service.stderr").exists() else None
        report["checks"]["controller_wall_within_limit"] = report["elapsed_seconds"] <= 120
        report["success"] = (bool(report.get("observations_completed")) and len(report["checks"]) == 10
                             and all(report["checks"].values()) and not report.get("cleanup_failure"))
        helper.publish(root / "receipt.json", report, mark_complete=False)
        helper.publish(output, report, mark_complete=False)
        print(json.dumps({"evidence": str(output), "success": report["success"], "failure": report.get("failure"),
                          "ownership": report.get("ownership"), "capability_masks": report.get("ready", {}).get("capability_masks"),
                          "checks": report["checks"], "service_stderr": report["service_stderr"],
                          "elapsed_seconds": report["elapsed_seconds"]}, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    if GUEST:
        diagnostic_role(*sys.argv[2:])
    elif not sys.argv[1:]:
        raise SystemExit(main())
    else:
        raise SystemExit("Unexpected diagnostic arguments")
