"""THROWAWAY GH-99: authorized owner-bootstrap two-role toy test. NEVER MERGE.

Temporary setup authority only in a newly owned user/network namespace. Retain
all final guards, drop every capability before toy data, never run acquired code.
"""

import fcntl
import hashlib
import importlib.util
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
HELPER_HASH = "a0065e77b60ca3b31cba8afa9119aaf77334e4850b95c4d6c995fcf71364845d"
NS_GET_USERNS = 0xB701
MODE = sys.argv[1] if len(sys.argv) > 1 else "controller"
sys.dont_write_bytecode = True
if MODE.startswith("--toy-"):
    helper_path = Path("/helper.py")
elif MODE.startswith("--bootstrap-"):
    helper_path = Path(__file__).with_name("helper.py")
else:
    helper_path = Path(__file__).with_name("ollama_containment_service_fixtures_prototype.py")
helper_bytes = helper_path.read_bytes()
if hashlib.sha256(helper_bytes).hexdigest() != HELPER_HASH:
    raise RuntimeError("Pinned trusted fixture helper changed")
spec = importlib.util.spec_from_file_location("trusted_gh99_helper", helper_path)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def capability_masks():
    return {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in helper.own_status() if line.startswith("Cap")}


def net_owner(pid="self"):
    fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    owner = None
    try:
        owner = fcntl.ioctl(fd, NS_GET_USERNS)
        return f"user:[{os.fstat(owner).st_ino}]"
    finally:
        if owner is not None:
            os.close(owner)
        os.close(fd)


def make_sandbox(source, helper_copy, evidence, staged, role, nonce, host_user, host_net, port=None, inode=None):
    args = helper.sandbox_args(source, evidence, staged, "toy-" + role, nonce,
                               host_user, host_net, port=port)
    args.insert(args.index("--unshare-all") + 1, "--share-net")
    # Narrow privileges: old constructor's diagnostic setup caps must not remain
    # in either application. Network setup now happens earlier in its owner.
    while "--cap-add" in args:
        index = args.index("--cap-add")
        del args[index:index + 2]
    index = args.index("--remount-ro")
    args[index:index] = ["--ro-bind", str(helper_copy), "/helper.py"]
    if inode is not None:
        args.append(str(inode))
    return args


def bootstrap_provider(source, helper_copy, evidence, staged, nonce, host_user, host_net):
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    ns = helper.namespaces()
    owner = net_owner()
    masks = capability_masks()
    if (ns["user"] == host_user or ns["net"] == host_net or owner != ns["user"]
            or os.getuid() != 0 or not int(masks["CapEff"], 16) & (1 << 12)
            or "NoNewPrivs:\t1" not in helper.own_status()):
        raise RuntimeError("Private setup owner/authority not established before interface action")
    if socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("Unexpected interface in new setup network")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as control:
        query = struct.pack("16sH14s", b"lo", 0, bytes(14))
        old_flags = struct.unpack("16sH14s", fcntl.ioctl(control, 0x8913, query))[1]
        fcntl.ioctl(control, 0x8914, struct.pack("16sH14s", b"lo", old_flags | 1, bytes(14)))
        new_flags = struct.unpack("16sH14s", fcntl.ioctl(control, 0x8913, query))[1]
    if not new_flags & 1:
        raise RuntimeError("Private owner setup did not enable loopback")
    helper.publish(Path(evidence) / "bootstrap.json", {
        "nonce": nonce, "namespaces": ns, "net_owning_user": owner,
        "uid_map": Path("/proc/self/uid_map").read_text(),
        "uid_in_namespace": os.getuid(), "capability_masks_during_setup": masks,
        "cgroup_before_sandbox": Path("/proc/self/cgroup").read_text(),
        "loopback_flags_before": old_flags, "loopback_flags_after": new_flags,
        "interface_changed": "lo only, in newly verified owned network namespace",
    })
    command = make_sandbox(Path(source), Path(helper_copy), Path(evidence), Path(staged),
                           "provider", nonce, host_user, host_net)
    os.execve(command[0], command, {"PATH": "/usr/bin:/bin", "LANG": "C"})


def bootstrap_worker(args):
    pid, birth, group, expected_net, expected_owner, source, helper_copy, evidence, staged, nonce, host_user, host_net, port, inode = args
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    before = helper.process(int(pid))
    if (before.get("start_ticks") != birth or before.get("cgroup", "").strip() != "0::" + group
            or before.get("namespaces", {}).get("net") != expected_net
            or expected_net == host_net or expected_owner == host_user):
        raise RuntimeError("Owned Provider-fixture incarnation changed before worker setup")
    fd = os.open(f"/proc/{pid}/ns/net", os.O_RDONLY)
    owner_fd = None
    initial_cgroup = Path("/proc/self/cgroup").read_text()
    try:
        owner_fd = fcntl.ioctl(fd, NS_GET_USERNS)
        if f"user:[{os.fstat(owner_fd).st_ino}]" != expected_owner:
            raise RuntimeError("Worker setup owner differs from pinned private NET owner")
        after_open = helper.process(int(pid))
        if after_open.get("start_ticks") != birth or after_open.get("namespaces") != before["namespaces"]:
            raise RuntimeError("Provider identity changed while opening setup namespace handles")
        os.setns(owner_fd, 0)
        os.setns(fd, 0)
    finally:
        if owner_fd is not None:
            os.close(owner_fd)
        os.close(fd)
    ns = helper.namespaces()
    if (ns["user"] != expected_owner or ns["net"] != expected_net or net_owner() != expected_owner
            or Path("/proc/self/cgroup").read_text() != initial_cgroup or os.getuid() != 0):
        raise RuntimeError("Worker did not join only the verified setup user/NET while retaining its own cgroup")
    helper.publish(Path(evidence) / "bootstrap.json", {
        "nonce": nonce, "namespaces": ns, "net_owning_user": net_owner(),
        "cgroup_unchanged": initial_cgroup, "capability_masks_during_setup": capability_masks(),
        "joined_verified_net_owner_not_final_provider_user": ns["user"] != before["namespaces"]["user"],
        "provider_process_before": before,
    })
    command = make_sandbox(Path(source), Path(helper_copy), Path(evidence), Path(staged), "worker",
                           nonce, host_user, host_net, port=port, inode=inode)
    os.execve(command[0], command, {"PATH": "/usr/bin:/bin", "LANG": "C"})


def receive_exact(connection, size):
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise RuntimeError("Partial fixed toy frame")
        data.extend(chunk)
    return bytes(data)


def toy_role(role, nonce, host_user, host_net, port=None, old_inode=None):
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    ns = helper.namespaces()
    masks = capability_masks()
    if (set(masks) != {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"}
            or any(int(value, 16) for value in masks.values()) or "NoNewPrivs:\t1" not in helper.own_status()
            or ns["user"] == host_user or ns["net"] == host_net):
        raise RuntimeError("Final guarded application did not drop all capabilities or enter private namespaces")
    bus_absent = not any(name in os.environ for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"))
    if not bus_absent or socket.if_nameindex() != [(1, "lo")]:
        raise RuntimeError("Unexpected control environment or network interface in toy application")
    filesystem = {
        "cwd": os.getcwd(), "cwd_entries": os.listdir("."), "build_exists": Path("/build").exists(),
        "home_entries": os.listdir("/home"), "run_entries": os.listdir("/run"), "sys_entries": os.listdir("/sys"),
        "package_read_only": bool(os.statvfs("/opt/ollama").f_flag & os.ST_RDONLY),
        "server_mode": stat.S_IMODE(Path("/opt/ollama/bin/ollama").stat().st_mode),
        "runner_mode": stat.S_IMODE(Path("/opt/ollama/lib/ollama/llama-server").stat().st_mode),
        "devices": sorted(os.listdir("/dev")),
    }
    basic_devices = {"core", "fd", "full", "null", "ptmx", "pts", "random", "shm", "stderr", "stdin", "stdout", "tty", "urandom", "zero"}
    if (filesystem["cwd"] != "/work" or filesystem["cwd_entries"] or filesystem["build_exists"]
            or filesystem["home_entries"] or filesystem["run_entries"] or filesystem["sys_entries"]
            or not filesystem["package_read_only"] or filesystem["server_mode"] != 0o600
            or filesystem["runner_mode"] != 0o600 or not set(filesystem["devices"]) <= basic_devices):
        raise RuntimeError("Guarded filesystem/search-path/device constraint failed before toy traffic")
    with socket.socket() as negative:
        negative.settimeout(0.5)
        try:
            negative.connect(("192.0.2.1", 9))
            raise RuntimeError("Unexpected outward connection in private fixture network")
        except OSError as error:
            if error.errno != 101:
                raise
            egress = {"errno": error.errno, "error": "network unreachable"}
    common = {"role": role, "nonce": nonce, "namespaces": ns, "capability_masks": masks,
              "status": helper.own_status(), "controller_bus_absent": bus_absent, "filesystem": filesystem,
              "egress_probe": egress, "interfaces": socket.if_nameindex(),
              "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "helper_sha256": HELPER_HASH,
              "cgroup": Path("/proc/self/cgroup").read_text()}
    request = ("GH99-TOY:" + nonce).encode()
    reply = request + b":ACK"
    if role == "provider":
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(2)
            server.settimeout(0.1)
            common["listener"] = list(server.getsockname())
            common["listener_fd"] = server.fileno()
            common["listener_inode_link"] = os.readlink(f"/proc/self/fd/{server.fileno()}")
            common["tcp_table_before"] = Path("/proc/net/tcp").read_text()
            helper.publish(Path("/evidence/ready.json"), common)
            accepted = False
            while True:
                try:
                    connection, peer = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(1)
                    actual = receive_exact(connection, len(request))
                    if accepted or actual != request:
                        raise RuntimeError("Unexpected/duplicate fixed toy request")
                    connection.sendall(reply)
                    accepted = True
                    helper.publish(Path("/evidence/accepted.json"), {"nonce": nonce, "peer": list(peer),
                                                                "request_sha256": hashlib.sha256(actual).hexdigest()})
    else:
        with socket.socket() as client:
            client.settimeout(1)
            client.connect(("127.0.0.1", int(port)))
            common["client_local"] = list(client.getsockname())
            common["client_peer"] = list(client.getpeername())
            client.sendall(request)
            if receive_exact(client, len(reply)) != reply:
                raise RuntimeError("Fixed toy reply correlation failed")
            common["toy_reply_correlated"] = True
            common["request_sha256"] = hashlib.sha256(request).hexdigest()
        helper.publish(Path("/evidence/ready.json"), common)
        while not Path("/evidence/probe-after-provider-stop").exists():
            time.sleep(0.02)
        with socket.socket() as after:
            after.settimeout(0.5)
            try:
                after.connect(("127.0.0.1", int(port)))
                result = {"unexpected_connect": True}
            except OSError as error:
                result = {"errno": error.errno, "error": str(error)}
        table = Path("/proc/net/tcp").read_text()
        listener_present = any(len(parts := line.split()) > 9 and parts[3] == "0A" and parts[9] == old_inode
                               for line in table.splitlines()[1:])
        helper.publish(Path("/evidence/after-stop.json"), {"nonce": nonce, "outcome": result, "tcp_table": table,
                                                        "old_listener_inode_present": listener_present,
                                                        "namespaces": helper.namespaces(), "capability_masks": capability_masks()})
        while True:
            time.sleep(0.1)


def controller():
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    started = time.monotonic()
    source = Path(__file__).resolve()
    repo = source.parents[2]
    output = repo / "docs/prototypes/ollama-containment-owner-bootstrap-fixtures.observations.json"
    if output.exists():
        raise RuntimeError("Fixture evidence exists; no overwrite or automatic rerun")
    uid = os.getuid()
    runtime = Path("/run/user") / str(uid)
    bus = runtime / "bus"
    rs, bs = runtime.lstat(), bus.lstat()
    if (not stat.S_ISDIR(rs.st_mode) or rs.st_uid != uid or stat.S_IMODE(rs.st_mode) != 0o700
            or not stat.S_ISSOCK(bs.st_mode) or bs.st_uid != uid):
        raise RuntimeError("Controller user-bus endpoint is not the owned approved socket")
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "XDG_RUNTIME_DIR": str(runtime),
           "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(bus)}
    stage_bytes = (repo / "docs/prototypes/ollama-containment-extraction.observations.json").read_bytes()
    stage = json.loads(stage_bytes)
    if not stage["success"]:
        raise RuntimeError("Staging prerequisite failed")
    root = repo / ".prototype-gh99-artifacts" / ("owner-bootstrap-fixtures-" + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    copied, helper_copy = root / "fixture.py", root / "helper.py"
    for target, data in [(copied, source.read_bytes()), (helper_copy, helper_bytes)]:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    host = helper.namespaces()
    nonce = uuid.uuid4().hex
    report = {"throwaway": True, "issue": 99, "scope": "two owner-bootstrap harmless fixtures only",
              "started_utc": helper.now(), "root": str(root), "nonce": nonce, "host_namespaces": host,
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "helper_sha256": HELPER_HASH,
              "staging_receipt_sha256": hashlib.sha256(stage_bytes).hexdigest(), "units": {}, "commands": [], "checks": {},
              "controller_as_limit": list(resource.getrlimit(resource.RLIMIT_AS)),
              "acquired_code_executed_or_loaded": False, "acquired_permissions_changed": False,
              "model_store_provisioned_or_loaded": False, "inference_performed": False,
              "host_elevation_or_firewall_change": False, "external_network_or_download": False,
              "guard_relaxation": False, "journal_read": False, "deletion_performed": False}
    launched = []
    stop_attempted = set()
    snapshots = {}

    def run(argv, timeout=8):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)
        report["commands"].append({"argv": argv, "returncode": result.returncode, "stdout": result.stdout,
                                   "stderr": result.stderr, "elapsed_seconds": time.monotonic() - started})
        return result

    def show(unit):
        result = run(["systemctl", "--user", "show", unit,
                      "--property=Id,LoadState,ActiveState,SubState,InvocationID,MainPID,ControlGroup,"
                      "MemoryMax,MemoryPeak,TasksMax,RuntimeMaxUSec,TimeoutStopUSec,Restart,NoNewPrivileges,KillMode,SendSIGKILL,Result"])
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def wait_json(path):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if Path(str(path) + ".complete").exists():
                return json.loads(path.read_text())
            time.sleep(0.02)
        raise TimeoutError("Expected complete fixture evidence missing")

    def launch(role, build):
        directory = root / role
        directory.mkdir(mode=0o700)
        unit = "prototype-gh99-owner-" + role + "-" + uuid.uuid4().hex + ".service"
        report["units"][role] = {"name": unit, "evidence_directory": str(directory)}
        # A fresh exact name must be absent; never adopt a pre-existing unit.
        if show(unit).get("LoadState") != "not-found":
            raise RuntimeError("Generated unit name is not absent; refuse adoption")
        command = ["systemd-run", "--user", "--no-ask-password", "--unit=" + unit,
                   "--description=THROWAWAY GH99 owner-bootstrap " + role + " " + nonce,
                   "--property=RuntimeMaxSec=50", "--property=TimeoutStopSec=3", "--property=MemoryMax=128M",
                   "--property=TasksMax=16", "--property=Restart=no", "--property=NoNewPrivileges=yes",
                   "--property=KillMode=control-group", "--property=SendSIGKILL=yes",
                   "--property=UnsetEnvironment=LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT LD_PROFILE LD_PROFILE_OUTPUT GLIBC_TUNABLES GCONV_PATH",
                   "--property=StandardOutput=append:" + str(directory / "service.stdout"),
                   "--property=StandardError=append:" + str(directory / "service.stderr"),
                   "/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "LANG=C", *build(directory)]
        if run(command).returncode != 0:
            raise RuntimeError("Transient fixture creation command failed")
        launched.append(unit)
        report["units"][role]["initial_state"] = show(unit)
        return unit, directory

    def census(unit, role):
        state = show(unit)
        group = state.get("ControlGroup", "")
        if not group.endswith("/" + unit) or ".." in Path(group).parts:
            raise RuntimeError("Unexpected owned cgroup identity")
        cg = Path("/sys/fs/cgroup") / group.lstrip("/")
        pids = sorted({int(pid) for directory in [cg, *cg.rglob("*")] if directory.is_dir()
                       for pid in (directory / "cgroup.procs").read_text().split()})
        result = {"unit_state": state, "cgroup_path": str(cg), "processes": [helper.process(pid) for pid in pids],
                  "cgroup_files": {name: (cg / name).read_text() for name in
                                   ("cgroup.events", "memory.max", "memory.peak", "memory.events", "pids.max")}}
        snapshots[unit] = result
        report["units"][role]["before_stop"] = result
        targets = [p for p in result["processes"] if "/fixture.py" in p.get("cmdline", [])
                   and "--toy-" + role in p["cmdline"] and Path(p.get("exe", "")).name.startswith("python")]
        if len(targets) != 1:
            raise RuntimeError("Final toy application is not unique in its owned service")
        result["application"] = targets[0]
        result["net_owning_user_from_controller"] = net_owner(targets[0]["pid"])
        return result

    def stop(unit, role):
        stop_attempted.add(unit)
        before = snapshots.get(unit)
        result = run(["systemctl", "--user", "--no-ask-password", "stop", unit])
        record = {"returncode": result.returncode, "unit_after": show(unit)}
        if before:
            cg = Path(before["cgroup_path"])
            after = [helper.process(p["pid"]) for p in before["processes"]]
            record.update({"processes_after": after, "cgroup_removed": not cg.exists(),
                           "cgroup_events_after": (cg / "cgroup.events").read_text() if cg.exists() else None})
            record["independent_exit_witness"] = (
                result.returncode == 0 and all(p.get("absent_or_exited_during_read") or p.get("start_ticks") != old.get("start_ticks")
                                               or p.get("state") == "Z" for old, p in zip(before["processes"], after))
                and (record["cgroup_removed"] or "populated 0" in (record["cgroup_events_after"] or ""))
                and record["unit_after"].get("ActiveState") in ("inactive", "failed"))
        report["units"][role]["stop"] = record
        return record

    def expired(signum, frame):
        raise TimeoutError("Owner-bootstrap controller two-minute limit reached")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    try:
        provider_unit, provider_dir = launch("provider", lambda directory: [
            "/usr/bin/unshare", "--user", "--map-root-user", "--net", "/usr/bin/python3", "-I", "-S",
            str(copied), "--bootstrap-provider", str(copied), str(helper_copy), str(directory), str(stage["directory"]),
            nonce, host["user"], host["net"]])
        provider = wait_json(provider_dir / "ready.json")
        pboot = wait_json(provider_dir / "bootstrap.json")
        report["provider_ready"], report["provider_bootstrap"] = provider, pboot
        pin = census(provider_unit, "provider")
        target = pin["application"]
        if target["namespaces"] != provider["namespaces"]:
            raise RuntimeError("Provider namespace witness differs from correlated host process")
        inode_link = os.readlink(f"/proc/{target['pid']}/fd/{provider['listener_fd']}")
        inode = inode_link.removeprefix("socket:[").removesuffix("]")
        if inode_link != provider["listener_inode_link"] or not inode.isdecimal():
            raise RuntimeError("Provider socket descriptor/inode ownership mismatch")
        port = provider["listener"][1]
        worker_unit, worker_dir = launch("worker", lambda directory: [
            "/usr/bin/python3", "-I", "-S", str(copied), "--bootstrap-worker", str(target["pid"]), target["start_ticks"],
            pin["unit_state"]["ControlGroup"], provider["namespaces"]["net"], pboot["net_owning_user"],
            str(copied), str(helper_copy), str(directory), str(stage["directory"]), nonce, host["user"], host["net"], str(port), inode])
        worker = wait_json(worker_dir / "ready.json")
        wboot = wait_json(worker_dir / "bootstrap.json")
        accepted = wait_json(provider_dir / "accepted.json")
        report["worker_ready"], report["worker_bootstrap"], report["provider_accepted"] = worker, wboot, accepted
        win = census(worker_unit, "worker")
        if win["application"]["namespaces"] != worker["namespaces"]:
            raise RuntimeError("Worker namespace witness differs from correlated host process")
        report["checks"].update({
            "new_owner_before_loopback_setup": pboot["net_owning_user"] == pboot["namespaces"]["user"] != host["user"]
                                               and pboot["namespaces"]["net"] != host["net"] and bool(pboot["loopback_flags_after"] & 1),
            "shared_private_network": provider["namespaces"]["net"] == worker["namespaces"]["net"] == pboot["namespaces"]["net"] != host["net"],
            "controller_verified_net_owner_both": pin["net_owning_user_from_controller"] == win["net_owning_user_from_controller"] == pboot["net_owning_user"],
            "worker_joined_verified_setup_owner": wboot["net_owning_user"] == wboot["namespaces"]["user"] == pboot["net_owning_user"]
                                                  and wboot["joined_verified_net_owner_not_final_provider_user"],
            "distinct_final_user_pid_mount_views": all(provider["namespaces"][name] != worker["namespaces"][name]
                                                       and provider["namespaces"][name] != host[name] and worker["namespaces"][name] != host[name]
                                                       for name in ("user", "pid", "mnt")),
            "separate_owned_service_cgroups": pin["unit_state"]["ControlGroup"] != win["unit_state"]["ControlGroup"]
                                               and pin["unit_state"]["InvocationID"] != win["unit_state"]["InvocationID"],
            "all_final_capabilities_zero": all(not int(value, 16) for item in (provider, worker) for value in item["capability_masks"].values()),
            "source_and_helper_match_both": all(item["source_sha256"] == report["source_sha256"] and item["helper_sha256"] == HELPER_HASH for item in (provider, worker)),
            "controller_bus_absent_both": provider["controller_bus_absent"] and worker["controller_bus_absent"],
            "controlled_filesystem_search_paths": all(not item["filesystem"]["build_exists"] and not item["filesystem"]["cwd_entries"]
                                                      and item["filesystem"]["cwd"] == "/work" for item in (provider, worker)),
            "read_only_non_executable_acquired_package": all(item["filesystem"]["package_read_only"] and item["filesystem"]["server_mode"] == 0o600
                                                            and item["filesystem"]["runner_mode"] == 0o600 for item in (provider, worker)),
            "no_outward_route_observed": provider["egress_probe"]["errno"] == worker["egress_probe"]["errno"] == 101,
            "direct_correlated_toy_request": worker["toy_reply_correlated"] and worker["client_peer"] == provider["listener"]
                                             and accepted["peer"] == worker["client_local"] and accepted["request_sha256"] == worker["request_sha256"],
            "owned_listener_inode_correlated": inode_link == provider["listener_inode_link"],
            "unit_limits_observed_both": all(item["cgroup_files"]["memory.max"].strip() == str(MEMORY) and item["cgroup_files"]["pids.max"].strip() == "16"
                                              and item["unit_state"]["RuntimeMaxUSec"] == "50s" and item["unit_state"]["NoNewPrivileges"] == "yes"
                                              and item["unit_state"]["Restart"] == "no" for item in (pin, win)),
        })
        pstop = stop(provider_unit, "provider")
        report["checks"]["provider_exit_independently_observed"] = bool(pstop.get("independent_exit_witness"))
        worker_state = show(worker_unit)
        worker_process = helper.process(win["application"]["pid"])
        report["worker_after_provider_stop"] = {"unit": worker_state, "application": worker_process}
        report["checks"]["worker_continuity_without_replacement"] = (worker_state.get("ActiveState") == "active"
            and worker_state.get("InvocationID") == win["unit_state"]["InvocationID"]
            and worker_process.get("start_ticks") == win["application"]["start_ticks"])
        fd = os.open(worker_dir / "probe-after-provider-stop", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        after = wait_json(worker_dir / "after-stop.json")
        report["worker_after_stop_probe"] = after
        report["checks"]["old_listener_absent_and_refused"] = after["outcome"].get("errno") == 111 and not after["old_listener_inode_present"]
        report["checks"]["worker_still_unprivileged_same_network"] = (after["namespaces"] == worker["namespaces"]
            and all(not int(value, 16) for value in after["capability_masks"].values()))
        wstop = stop(worker_unit, "worker")
        report["checks"]["worker_exit_independently_observed"] = bool(wstop.get("independent_exit_witness"))
        report["exercise_completed"] = True
    except Exception as error:
        report["exercise_completed"] = False
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        for role, entry in reversed(list(report["units"].items())):
            if entry["name"] in launched and entry["name"] not in stop_attempted:
                try:
                    stop(entry["name"], role)
                except Exception as error:
                    report.setdefault("cleanup_errors", []).append({"role": role, "type": type(error).__name__, "message": str(error)})
            directory = Path(entry["evidence_directory"])
            entry["service_stderr"] = (directory / "service.stderr").read_text() if (directory / "service.stderr").exists() else None
        signal.setitimer(signal.ITIMER_REAL, 0)
        report["elapsed_seconds"] = time.monotonic() - started
        report["finished_utc"] = helper.now()
        report["controller_status"] = helper.own_status()
        report["checks"]["controller_wall_within_limit"] = report["elapsed_seconds"] <= 120
        report["success"] = bool(report.get("exercise_completed")) and len(report["checks"]) == 21 and all(report["checks"].values()) and not report.get("cleanup_errors")
        helper.publish(root / "receipt.json", report, mark_complete=False)
        helper.publish(output, report, mark_complete=False)
        print(json.dumps({"evidence": str(output), "success": report["success"], "failure": report.get("failure"),
                          "checks": report["checks"], "service_stderr": {r: e.get("service_stderr") for r, e in report["units"].items()},
                          "elapsed_seconds": report["elapsed_seconds"]}, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    if MODE == "--bootstrap-provider":
        bootstrap_provider(*sys.argv[2:])
    elif MODE == "--bootstrap-worker":
        bootstrap_worker(sys.argv[2:])
    elif MODE == "--toy-provider":
        toy_role("provider", *sys.argv[2:])
    elif MODE == "--toy-worker":
        toy_role("worker", *sys.argv[2:])
    elif MODE == "controller":
        raise SystemExit(controller())
    else:
        raise SystemExit("Unexpected fixture mode")
