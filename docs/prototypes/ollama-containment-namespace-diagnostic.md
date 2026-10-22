# Observational namespace diagnostic — THROWAWAY GH-99, NEVER MERGE

2026-10-22. Human separately authorized one harmless observational user service,
retaining guards, <=60s / 128 MiB / 16 tasks, controller <=128 MiB / 2min. No
interface changes, socket/traffic tests, worker migration, acquired-code loading/
execution/permission changes, model loading, inference, downloads, host elevation,
firewall changes or deletion. Exactly one diagnostic service ran.

## Evidence and command

- [Executed diagnostic](ollama_containment_namespace_diagnostic_prototype.py),
  SHA-256 `86fc0564383fa87a9db37ba282e4f9a9f1f5ec80958a7911e5a3bbe585c3ebe2`.
- [Full namespace/capability/resource/exit observations](ollama-containment-namespace-diagnostic.observations.json).
- The trusted helper remains exactly the corrected-attempt source at
  `a0065e77b60ca3b31cba8afa9119aaf77334e4850b95c4d6c995fcf71364845d`.
  It was hash-checked before import and copied read-only into the sandbox; only
  its construction/observation helpers ran, not its loopback setter, worker join
  or toy request roles. No acquired executable/library was imported or executed.

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_namespace_diagnostic_prototype.py
```

Controller checked `/usr/include/linux/nsfs.h`, SHA-256
`1b8e12c26323274b831f6a28bf908d8a2a866eb9bdbc67c45412579261c96d70`,
for the read-only namespace ioctl ABI: NS_GET_USERNS 0xb701, NS_GET_PARENT 0xb702,
NS_GET_NSTYPE 0xb703. These obtain namespace handles/metadata, not setns or a
namespace/interface mutation. The attempted installed user_namespaces(7) manual
path was absent; no claim is made that that local manual was read or validated.
The earlier installed bwrap manual remains the documented nested-guard source.

## Observed result: 10/10 diagnostic checks pass

One newly created unit, `prototype-gh99-nsdiag-a51740cce1c549e0951755de854cb2a6.service`,
invocation `21ebe33d31664148a27613ae416adc0b`, owned the whole diagnostic before
exec. Live observations include MemoryMax128MiB, TasksMax16, RuntimeMax50s,
TimeoutStop3s, NoNewPrivileges yes, Restart no and KillMode control-group.
No existing unit was adopted or signaled; no worker service was created.

The controller correlated the fixture by exact service cgroup, host PID/start
ticks, executable/arguments, nonce, source/helper hashes and kernel namespace
handles, not just the child's JSON. It opened handles only for that owned target;
the source did not call socket APIs or modify interfaces.

| Observed namespace | Identity |
| --- | --- |
| Fixture current user namespace | `user:[4026533869]` |
| Fixture private network namespace | `net:[4026533816]` |
| Network namespace's owning user namespace | `user:[4026533810]` |
| Parent of fixture's current user namespace | `user:[4026533810]` |

Kernel namespace types were USER/NET/USER as expected. Target user/net/pid/mount
namespaces differed from the controller's baseline. The owning user namespace
is therefore **the parent of**, not equal to, the fixture's current user namespace.

All five capability masks (inheritable/permitted/effective/bounding/ambient) were
**0x1100**: CAP_SETPCAP plus CAP_NET_ADMIN, scoped to that **child** user namespace.
Both requested bits were present, so simply adding the same capability inside
the final child namespace cannot fix authority over an ancestor-owned network.
NoNewPrivs was1; UID/GID were0 inside the namespace, with current-to-parent
uid/gid mappings0->0. This is namespace-local identity, not host-root elevation.
The controller-only public bus environment variables were observed absent in
the sandbox. No capability changes or interface setter was called by this role.

**Interpretation:** the unchanged guarded profile exhibits the ownership mismatch
predicted by bwrap's documented nested-user-namespace implementation, even with
CAP_NET_ADMIN present. It supports the explanation for the previous loopback
EPERM. This diagnostic did not rerun that ioctl or retroactively capture the
previous process's missing masks/owner; those historical records remain partial.
Do not fix the profile by removing the user-namespace creation guard or broadly
granting runtime privileges.

## Independent exit/resource witnesses

Three sampled owned processes (host PIDs32393,32395,32396) were inventoried before
stop. All observer namespace handles were closed **before** stop, avoiding hidden
controller references extending namespace lifetime. Exact-unit stop returned0;
all sampled processes were absent or exited during post-stop inspection, the
owned cgroup was removed and manager reported inactive/dead/not-found. Default
properties returned after unload are not prior-incarnation settings.

Elapsed **0.0886 seconds**. Controller effective address-space limit128MiB, own
VmHWM21,528KiB/VmPeak33,292KiB. Fixture reported VmHWM20,748KiB/VmPeak33,092KiB;
live cgroup memory.max134217728 / pids.max16 were independently read, with sampled
memory.events max/oom/oom_kill all0. Manager's sampled MemoryPeak12,062,720 bytes is
cgroup charge accounting, not the same metric as process RSS or a whole-host
reservation. This short stop does not exercise timeout escalation or controller loss.

No journal command ran: direct fixture stderr was empty, and primary evidence was
the live namespace/cgroup/process census plus post-stop witnesses. The former
journal allocation failure was not hidden by increasing a resource limit.

**Bounded verdict:** the ownership/capability mismatch is observed and this
harmless service's cleanup is witnessed. It does not validate shared usable
loopback, separate two-role shutdown, loader-search filesystem constraints,
real server/runner/backend/model bytes, native request completion/accounting,
device cessation, controller loss or complete Provider containment. No new work
admission or architecture acceptance follows; #99 remains open and claimed.

## Next proposed scope: owner-bootstrap two-role toy test, not authorized yet

Before any actual Ollama, propose a revised two-fixture test with **temporary
namespace-local bootstrap authority only**:

1. Within the exclusively owned simulated-Provider unit, trusted bootstrap creates
   a new user/network namespace pair. Observe that the current user namespace
   actually owns that new private network namespace **before** enabling only its
   loopback. No host interface, host firewall or external endpoint is touched.
2. Enter the full guarded application sandbox afterward, retaining
   `--disable-userns` / `--assert-userns-disabled`, private PID/mount/cgroup views,
   read-only root/package, hidden `/build` and fresh controlled `/work`. Shared
   network is preserved explicitly; **all** capability masks must be0 before toy
   application work, not the diagnostic's0x1100 setup mask.
3. A distinct newly owned worker unit verifies the simulated Provider's incarnation,
   obtains only that network namespace's owning-user handle, joins the owned
   setup namespace/network, then enters its **own** guarded application sandbox.
   Final per-role user/PID/mount namespaces and cgroups remain separate; loopback
   is direct/shared, not a proxy or host-network bridge. Drop all capabilities
   before the worker sends fixed toy data. Reject any missing ownership witness.
4. Observe direct correlated toy request/reply, no outward route, constrained
   filesystem/CWD search paths, exact listener ownership, Provider-fixture stop
   without silently replacing the worker, old-listener refusal and independent
   worker termination. No native Provider request/model/tool is involved.

Proposed caps unchanged: **two** uniquely named disposable user services, each
<=60s / 128MiB / 16tasks; trusted controller <=128MiB address space / 2min.
Stop only the exact new fixtures, preserve all failures, no retries/restarts,
adoption or broader privileges. Namespace-local setup capabilities are temporary;
none may remain in final toy applications. No acquired-code/permission changes,
model-store provisioning/loading, downloads, inference, host elevation/firewall
changes, guard relaxation, binary patching or deletion. The diagnostic grant is
consumed and does **not** authorize this new bootstrap or interface change.

The human subsequently authorized that two-role owner-bootstrap toy test.
[Its final observations](ollama-containment-owner-bootstrap-fixtures.md) passed
21 bounded checks without any Ollama/model execution. The user then explicitly
paused Provider investigation, requested local cleanup and chose a narrower #38
scope preserving existing persistence/lifecycle behavior. No further runtime
experiments are authorized by these historical grants.
