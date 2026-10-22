"""THROWAWAY GH-99: two separately authorized harmless service fixtures only.

NEVER MERGE. No acquired-code execution, permission changes, Provider API, model
loading, inference, external traffic, host elevation or cleanup deletion.
"""

import ctypes
import datetime
import errno
import json
import os
from pathlib import Path
import resource
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid


MEMORY = 128 * 1024**2


def now():
    return datetime.datetime.now(datetime.UTC).isoformat()


def publish(path, value, mark_complete=True):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    if mark_complete:
        fd = os.open(str(path) + ".complete", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)


def namespaces(pid="self"):
    return {name: os.readlink(f"/proc/{pid}/ns/{name}")
            for name in ("user", "net", "pid", "mnt", "cgroup")}


def own_status():
    keys = ("Cap", "NoNewPrivs", "VmPeak", "VmHWM", "VmRSS", "VmSize", "NSpid")
    return [line for line in Path("/proc/self/status").read_text().splitlines()
            if line.startswith(keys)]


def process(pid):
    try:
        root = Path(f"/proc/{pid}")
        text = (root / "stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        return {"pid": pid, "state": fields[0], "start_ticks": fields[19],
                "exe": os.readlink(root / "exe"),
                "cmdline": (root / "cmdline").read_bytes().decode().split("\0"),
                "cgroup": (root / "cgroup").read_text(), "namespaces": namespaces(pid)}
    except (FileNotFoundError, ProcessLookupError):
        return {"pid": pid, "absent_or_exited_during_read": True}


def clear_caps():
    # Only called after entry into a newly owned user/network namespace.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    for cap in range(64):
        if libc.prctl(24, cap, 0, 0, 0) != 0 and ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), "Could not drop namespace-local bounding capability")

    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]

    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]

    header = Header(0x20080522, 0)
    data = (Data * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise OSError(ctypes.get_errno(), "Could not clear namespace-local capabilities")
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Could not set no-new-privileges")


def sandbox_args(source, evidence, staged, role, nonce, host_user, host_net, port=None, shared_user=False):
    args = ["/usr/bin/bwrap"]
    if shared_user:
        # Caller joined the existing owned user/net namespaces; do not create or
        # join the Provider PID namespace or accidentally unshare the shared net.
        args += ["--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup",
                 "--assert-userns-disabled", "--cap-drop", "ALL"]
    else:
        args += ["--unshare-all", "--unshare-user", "--uid", "0", "--gid", "0",
                 "--disable-userns", "--assert-userns-disabled", "--cap-drop", "ALL",
                 "--cap-add", "CAP_NET_ADMIN", "--cap-add", "CAP_SETPCAP"]
    args += ["--new-session", "--die-with-parent", "--ro-bind", "/usr", "/usr",
             "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib",
             "--symlink", "usr/lib", "/lib64", "--proc", "/proc", "--dev", "/dev",
             "--tmpfs", "/tmp", "--tmpfs", "/work", "--dir", "/sys", "--dir", "/run",
             "--dir", "/home", "--ro-bind", str(source), "/fixture.py",
             "--ro-bind", str(staged), "/opt/ollama", "--bind", str(evidence), "/evidence",
             "--chdir", "/work", "--remount-ro", "/", "--clearenv",
             "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C",
             "--setenv", "HOME", "/nonexistent", "/usr/bin/python3", "-I", "-S",
             "/fixture.py", "--" + role, nonce, host_user, host_net]
    if port is not None:
        args.append(str(port))
    return args


def fixture(role, nonce, host_user, host_net, port=None):
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    ns = namespaces()
    if ns["user"] == host_user or ns["net"] == host_net or os.getuid() != 0:
        raise RuntimeError("Fixture did not enter a distinct owned user/network namespace")
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("Unexpected interface in private namespace")
    setup = {"namespaces": ns, "status": own_status(), "uid_in_namespace": os.getuid()}
    if role == "provider":
        # Namespace identity and interfaces were checked before this ioctl. It
        # cannot be applied to a host interface by this fixture's command path.
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
            query = struct.pack("16sH14s", b"lo", 0, bytes(14))
            flags = struct.unpack("16sH14s", fcntl.ioctl(control, 0x8913, query))[1]
            fcntl.ioctl(control, 0x8914, struct.pack("16sH14s", b"lo", flags | 1, bytes(14)))
        clear_caps()
    status = own_status()
    if ("CapEff:\t0000000000000000" not in status
            or "CapBnd:\t0000000000000000" not in status or "NoNewPrivs:\t1" not in status):
        raise RuntimeError("Fixture retained capabilities or lacked no-new-privileges")
    controller_bus_absent = not any(key in os.environ for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"))
    if not controller_bus_absent:
        raise RuntimeError("Controller-only bus locator leaked into fixture environment")
    filesystem = {
        "cwd": os.getcwd(), "cwd_entries": os.listdir("."),
        "build_exists": Path("/build").exists(), "home_entries": os.listdir("/home"),
        "run_entries": os.listdir("/run"), "sys_entries": os.listdir("/sys"),
        "package_read_only": bool(os.statvfs("/opt/ollama").f_flag & os.ST_RDONLY),
        "package_server_mode": stat.S_IMODE(Path("/opt/ollama/bin/ollama").stat().st_mode),
        "package_runner_mode": stat.S_IMODE(Path("/opt/ollama/lib/ollama/llama-server").stat().st_mode),
    }
    if (filesystem["cwd"] != "/work" or filesystem["cwd_entries"] or filesystem["build_exists"]
            or filesystem["home_entries"] or filesystem["run_entries"] or filesystem["sys_entries"]
            or not filesystem["package_read_only"] or filesystem["package_server_mode"] != 0o600
            or filesystem["package_runner_mode"] != 0o600):
        raise RuntimeError("Filesystem/search-path staging constraint not established")
    with socket.socket() as negative:
        negative.settimeout(0.5)
        try:
            negative.connect(("192.0.2.1", 9))
            raise RuntimeError("Unexpected outward connection from private namespace")
        except OSError as error:
            if error.errno != errno.ENETUNREACH:
                raise
            egress = {"errno": error.errno, "error": "network unreachable"}
    common = {"role": role, "nonce": nonce, "namespaces": namespaces(), "setup": setup,
              "status": status, "filesystem": filesystem, "egress_probe": egress,
              "controller_bus_environment_absent": controller_bus_absent,
              "interfaces": socket.if_nameindex(), "cgroup": Path("/proc/self/cgroup").read_text()}
    if role == "provider":
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(2)
            server.settimeout(0.1)
            common["listener"] = list(server.getsockname())
            common["listener_fd"] = server.fileno()
            common["listener_fd_link"] = os.readlink(f"/proc/self/fd/{server.fileno()}")
            publish(Path("/evidence/ready.json"), common)
            accepted = False
            while True:
                try:
                    connection, peer = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(1)
                    data = connection.recv(256)
                    expected = ("GH99-TOY:" + nonce).encode()
                    if data != expected or accepted:
                        raise RuntimeError("Unexpected toy request or duplicate connection")
                    connection.sendall(expected + b":ACK")
                    accepted = True
                    publish(Path("/evidence/accepted.json"), {"peer": list(peer), "bytes": len(data), "nonce": nonce})
    else:
        expected = ("GH99-TOY:" + nonce).encode()
        with socket.socket() as client:
            client.settimeout(1)
            client.connect(("127.0.0.1", int(port)))
            common["client_local"] = list(client.getsockname())
            common["client_peer"] = list(client.getpeername())
            client.sendall(expected)
            response = client.recv(256)
            if response != expected + b":ACK":
                raise RuntimeError("Toy response correlation failed")
            common["toy_reply_correlated"] = True
        publish(Path("/evidence/ready.json"), common)
        while not Path("/evidence/probe-after-provider-stop").exists():
            time.sleep(0.02)
        with socket.socket() as after:
            after.settimeout(0.5)
            try:
                after.connect(("127.0.0.1", int(port)))
                outcome = {"unexpected_connect": True}
            except OSError as error:
                outcome = {"errno": error.errno, "error": str(error)}
        publish(Path("/evidence/after-stop.json"), {
            "outcome": outcome, "namespace_still_alive": namespaces(),
            "tcp_table": Path("/proc/net/tcp").read_text(), "status": own_status(), "nonce": nonce,
        })
        while True:
            time.sleep(0.1)


def join_worker(args):
    provider_pid, birth, target_cgroup, nonce, host_user, host_net, source, evidence, staged, port = args
    before = process(int(provider_pid))
    if before.get("start_ticks") != birth or before.get("cgroup", "").strip() != "0::" + target_cgroup:
        raise RuntimeError("Provider fixture birth/cgroup identity changed before namespace join")
    if before["namespaces"]["user"] == host_user or before["namespaces"]["net"] == host_net:
        raise RuntimeError("Provider fixture namespace is not private")
    user_fd = os.open(f"/proc/{provider_pid}/ns/user", os.O_RDONLY)
    net_fd = os.open(f"/proc/{provider_pid}/ns/net", os.O_RDONLY)
    try:
        after_open = process(int(provider_pid))
        if after_open.get("start_ticks") != birth or after_open.get("namespaces") != before["namespaces"]:
            raise RuntimeError("Provider namespace identity changed while opening handles")
        initial_cgroup = Path("/proc/self/cgroup").read_text()
        os.setns(user_fd, 0)
        os.setns(net_fd, 0)
    finally:
        os.close(user_fd)
        os.close(net_fd)
    joined = namespaces()
    if (joined["user"] != before["namespaces"]["user"] or joined["net"] != before["namespaces"]["net"]
            or Path("/proc/self/cgroup").read_text() != initial_cgroup):
        raise RuntimeError("Worker namespace join or independent cgroup identity mismatch")
    publish(Path(evidence) / "join.json", {"provider_before": before, "joined_namespaces": joined,
                                         "worker_cgroup_unchanged": initial_cgroup, "namespace_uid": os.getuid()})
    command = sandbox_args(Path(source), Path(evidence), Path(staged), "worker", nonce,
                           host_user, host_net, port=port, shared_user=True)
    os.execve(command[0], command, {"PATH": "/usr/bin:/bin", "LANG": "C"})


def controller():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    start = time.monotonic()
    nonce = uuid.uuid4().hex
    host = namespaces()
    source = Path(__file__).resolve()
    repo = source.parents[2]
    output = repo / "docs/prototypes/ollama-containment-service-fixtures.corrected.observations.json"
    if output.exists():
        raise RuntimeError("Fixture evidence exists; no automatic rerun or overwrite")
    stage_bytes = (repo / "docs/prototypes/ollama-containment-extraction.observations.json").read_bytes()
    stage = json.loads(stage_bytes)
    if not stage["success"]:
        raise RuntimeError("Staging prerequisite is not verified")
    staged = Path(stage["directory"])
    runtime = Path("/run/user") / str(os.getuid())
    bus = runtime / "bus"
    runtime_stat, bus_stat = runtime.lstat(), bus.lstat()
    if (not stat.S_ISDIR(runtime_stat.st_mode) or runtime_stat.st_uid != os.getuid()
            or stat.S_IMODE(runtime_stat.st_mode) != 0o700
            or not stat.S_ISSOCK(bus_stat.st_mode) or bus_stat.st_uid != os.getuid()):
        raise RuntimeError("Current user's runtime directory/socket is not the approved owned endpoint")
    control_env = {"PATH": "/usr/bin:/bin", "LANG": "C", "XDG_RUNTIME_DIR": str(runtime),
                   "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(bus)}
    root = repo / ".prototype-gh99-artifacts" / ("service-fixtures-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    copied = root / "trusted-fixture.py"
    fd = os.open(copied, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(source.read_bytes())
    report = {"throwaway": True, "issue": 99, "scope": "two harmless user-service fixtures only",
              "started_utc": now(), "root": str(root), "nonce": nonce, "host_namespaces": host,
              "source_sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
              "staging_receipt_sha256": __import__("hashlib").sha256(stage_bytes).hexdigest(),
              "controller_bus": {"runtime_path": str(runtime), "bus_path": str(bus),
                                 "runtime_uid": runtime_stat.st_uid, "bus_uid": bus_stat.st_uid,
                                 "runtime_mode": oct(stat.S_IMODE(runtime_stat.st_mode)),
                                 "bus_mode": oct(stat.S_IMODE(bus_stat.st_mode)),
                                 "passed_to_sandbox": False},
              "commands": [], "units": {}, "checks": {}, "controller_as_limit": list(resource.getrlimit(resource.RLIMIT_AS)),
              "acquired_code_executed_or_loaded": False, "acquired_permissions_changed": False,
              "inference_performed": False, "model_loaded": False, "network_outside_private_namespace": False,
              "host_elevation_or_firewall_change": False, "deletion_performed": False}
    launched = []
    stopped = set()
    snapshots = {}

    def run(argv, timeout=8):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                                env=control_env)
        report["commands"].append({"argv": argv, "returncode": result.returncode,
                                   "stdout": result.stdout, "stderr": result.stderr,
                                   "elapsed_seconds": time.monotonic() - start})
        return result

    def show(unit):
        properties = ("Id,LoadState,ActiveState,SubState,MainPID,InvocationID,ControlGroup,"
                      "MemoryMax,MemoryCurrent,MemoryPeak,TasksMax,TasksCurrent,RuntimeMaxUSec,"
                      "TimeoutStopUSec,Restart,KillMode,SendSIGKILL,NoNewPrivileges,Result")
        result = run(["systemctl", "--user", "show", unit, "--property=" + properties])
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def wait_json(path, seconds=8):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if Path(str(path) + ".complete").exists():
                return json.loads(path.read_text())
            time.sleep(0.03)
        raise TimeoutError("Fixture did not publish complete expected evidence")

    def inventory(unit):
        state = show(unit)
        group = state.get("ControlGroup", "")
        if not group.endswith("/" + unit) or ".." in Path(group).parts:
            raise RuntimeError("Missing or unexpected owned fixture cgroup")
        cg = Path("/sys/fs/cgroup") / group.lstrip("/")
        pids = sorted({int(pid) for directory in [cg, *cg.rglob("*")] if directory.is_dir()
                       for pid in (directory / "cgroup.procs").read_text().split()})
        result = {"unit_state": state, "cgroup_path": str(cg), "processes": [process(pid) for pid in pids],
                  "cgroup": {name: (cg / name).read_text() for name in
                             ["cgroup.events", "memory.max", "memory.peak", "memory.events", "pids.max"]}}
        snapshots[unit] = result
        return result

    def launch(role, command):
        unit = "prototype-gh99-" + role + "-" + uuid.uuid4().hex + ".service"
        directory = root / role
        directory.mkdir(mode=0o700)
        report["units"][role] = {"name": unit, "evidence_directory": str(directory)}
        argv = ["systemd-run", "--user", "--no-ask-password", "--unit=" + unit,
                "--description=THROWAWAY GH99 harmless " + role + " fixture",
                "--property=RuntimeMaxSec=50", "--property=TimeoutStopSec=3",
                "--property=MemoryMax=128M", "--property=TasksMax=16",
                "--property=NoNewPrivileges=yes", "--property=KillMode=control-group",
                "--property=SendSIGKILL=yes", "--property=Restart=no",
                "--property=StandardOutput=append:" + str(directory / "service.stdout"),
                "--property=StandardError=append:" + str(directory / "service.stderr"),
                "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "LANG=C", *command(directory)]
        if run(argv).returncode != 0:
            raise RuntimeError("Fixture service launch command failed")
        launched.append(unit)
        report["units"][role]["initial_state"] = show(unit)
        return unit, directory

    def stop(unit):
        before = snapshots.get(unit)
        result = run(["systemctl", "--user", "--no-ask-password", "stop", unit], timeout=8)
        record = {"command_returncode": result.returncode, "unit_after": show(unit)}
        if before:
            cg = Path(before["cgroup_path"])
            record["cgroup_removed"] = not cg.exists()
            record["cgroup_events_after"] = (cg / "cgroup.events").read_text() if cg.exists() else None
            record["processes_after"] = [process(item["pid"]) for item in before["processes"]]
            record["all_sampled_processes_exited"] = all(
                after.get("absent_or_exited_during_read") or after.get("start_ticks") != prior.get("start_ticks")
                or after.get("state") == "Z"
                for prior, after in zip(before["processes"], record["processes_after"]))
            record["quiescence_witness"] = (
                result.returncode == 0 and record["all_sampled_processes_exited"]
                and (record["cgroup_removed"] or "populated 0" in (record["cgroup_events_after"] or ""))
                and record["unit_after"].get("ActiveState") in ("inactive", "failed"))
        for entry in report["units"].values():
            if entry["name"] == unit:
                entry["stop"] = record
        if result.returncode == 0:
            stopped.add(unit)
        return record

    def expired(signum, frame):
        raise TimeoutError("Controller two-minute budget reached")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        provider_unit, provider_dir = launch("provider", lambda directory: sandbox_args(
            copied, directory, staged, "provider", nonce, host["user"], host["net"]))
        provider = wait_json(provider_dir / "ready.json")
        report["provider_ready"] = provider
        pin = inventory(provider_unit)
        report["units"]["provider"]["before"] = pin
        providers = [item for item in pin["processes"] if "/fixture.py" in item.get("cmdline", [])
                     and "--provider" in item["cmdline"]
                     and Path(item.get("exe", "")).name.startswith("python")]
        if len(providers) != 1:
            raise RuntimeError("Provider fixture process inventory is not unique")
        selected = providers[0]
        report["provider_fixture_host_process"] = selected
        if selected["namespaces"] != provider["namespaces"]:
            raise RuntimeError("Provider namespace witness differs from owned host process")
        fd_link = os.readlink(f"/proc/{selected['pid']}/fd/{provider['listener_fd']}")
        report["checks"]["owned_provider_listener_inode"] = fd_link == provider["listener_fd_link"]
        port = provider["listener"][1]
        worker_unit, worker_dir = launch("worker", lambda directory: [
            "/usr/bin/python3", "-I", "-S", str(copied), "--join-worker",
            str(selected["pid"]), selected["start_ticks"], pin["unit_state"]["ControlGroup"],
            nonce, host["user"], host["net"], str(copied), str(directory), str(staged), str(port)])
        worker = wait_json(worker_dir / "ready.json")
        report["worker_ready"] = worker
        report["worker_join"] = wait_json(worker_dir / "join.json")
        report["provider_accepted"] = wait_json(provider_dir / "accepted.json")
        win = inventory(worker_unit)
        report["units"]["worker"]["before"] = win
        workers = [item for item in win["processes"] if "/fixture.py" in item.get("cmdline", [])
                   and "--worker" in item["cmdline"] and Path(item.get("exe", "")).name.startswith("python")]
        if len(workers) != 1 or workers[0]["namespaces"] != worker["namespaces"]:
            raise RuntimeError("Worker owned process/namespace inventory is not unique or correlated")
        report["worker_fixture_host_process"] = workers[0]
        report["checks"].update({
            "two_exclusive_service_cgroups": pin["unit_state"]["ControlGroup"] != win["unit_state"]["ControlGroup"],
            "shared_private_user_and_net": all(provider["namespaces"][name] == worker["namespaces"][name]
                                               and provider["namespaces"][name] != host[name] for name in ("user", "net")),
            "distinct_private_pid_and_mount": all(provider["namespaces"][name] != worker["namespaces"][name]
                                                  and provider["namespaces"][name] != host[name]
                                                  and worker["namespaces"][name] != host[name] for name in ("pid", "mnt")),
            "direct_correlated_private_loopback": worker["toy_reply_correlated"] and worker["client_peer"] == provider["listener"]
                                                  and report["provider_accepted"]["peer"] == worker["client_local"],
            "no_outward_route": provider["egress_probe"]["errno"] == worker["egress_probe"]["errno"] == errno.ENETUNREACH,
            "controlled_search_paths_both": all(not item["filesystem"]["build_exists"] and not item["filesystem"]["cwd_entries"]
                                                and item["filesystem"]["cwd"] == "/work" for item in (provider, worker)),
            "non_executable_read_only_package_both": all(item["filesystem"]["package_read_only"]
                                                         and item["filesystem"]["package_server_mode"] == 0o600
                                                         and item["filesystem"]["package_runner_mode"] == 0o600 for item in (provider, worker)),
            "unit_memory_and_task_limits": all(item["cgroup"]["memory.max"].strip() == str(MEMORY)
                                               and item["cgroup"]["pids.max"].strip() == "16" for item in (pin, win)),
        })
        provider_stop = stop(provider_unit)
        report["checks"]["provider_stop_observed"] = bool(provider_stop.get("quiescence_witness"))
        worker_alive = show(worker_unit)
        report["worker_after_provider_stop"] = worker_alive
        report["checks"]["worker_independent_after_provider_stop"] = worker_alive.get("ActiveState") == "active"
        fd = os.open(worker_dir / "probe-after-provider-stop", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        after = wait_json(worker_dir / "after-stop.json")
        report["worker_after_stop_probe"] = after
        report["checks"]["old_listener_refused_after_provider_stop"] = after["outcome"].get("errno") == errno.ECONNREFUSED
        worker_stop = stop(worker_unit)
        report["checks"]["worker_stop_independently_observed"] = bool(worker_stop.get("quiescence_witness"))
        report["exercise_completed"] = True
    except Exception as error:
        report["exercise_completed"] = False
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        for unit in reversed(launched):
            if unit not in stopped:
                try:
                    if unit not in snapshots:
                        try:
                            snapshots[unit] = inventory(unit)
                        except Exception as error:
                            report.setdefault("missing_cleanup_inventory", []).append({"unit": unit, "type": type(error).__name__})
                    stop(unit)
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append({"unit": unit, "type": type(error).__name__, "message": str(error)})
        signal.setitimer(signal.ITIMER_REAL, 0)
        for role, entry in report["units"].items():
            if entry["name"] not in launched:
                continue
            directory = Path(entry["evidence_directory"])
            entry["service_stderr"] = (directory / "service.stderr").read_text() if (directory / "service.stderr").exists() else None
            try:
                run(["journalctl", "--user", "--unit=" + entry["name"], "--no-pager", "--output=short-monotonic"])
            except Exception as error:
                report.setdefault("journal_errors", []).append(type(error).__name__)
        report["finished_utc"] = now()
        report["elapsed_seconds"] = time.monotonic() - start
        report["controller_status"] = own_status()
        report["success"] = bool(report.get("exercise_completed")) and bool(report["checks"]) and all(report["checks"].values())
        if report.get("cleanup_errors") or report["elapsed_seconds"] > 120:
            report["success"] = False
        publish(root / "receipt.json", report, mark_complete=False)
        publish(output, report, mark_complete=False)
        print(json.dumps({"evidence": str(output), "root": str(root), "success": report["success"],
                          "failure": report.get("failure"), "checks": report["checks"],
                          "service_stderr": {role: entry.get("service_stderr") for role, entry in report["units"].items()},
                          "elapsed_seconds": report["elapsed_seconds"]}, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--provider"]:
        fixture("provider", *sys.argv[2:])
    elif sys.argv[1:2] == ["--worker"]:
        fixture("worker", *sys.argv[2:])
    elif sys.argv[1:2] == ["--join-worker"]:
        join_worker(sys.argv[2:])
    elif not sys.argv[1:]:
        raise SystemExit(controller())
    else:
        raise SystemExit("Unexpected fixture arguments")
