# Ownership/network fixture attempt — THROWAWAY GH-99, NEVER MERGE

2026-10-22. The human explicitly authorized two harmless disposable user services,
each <=60 seconds / 128 MiB / 16 tasks, with a trusted controller <=128 MiB process
address space / 2 minutes. Goals: separate owned cgroups/PID/filesystem views,
shared private loopback, constrained loader-search paths and observed cleanup.
No host elevation/firewall changes, acquired-code execution/permissions changes,
model loading, inference, downloads or deletion were authorized or performed.

## Evidence

- [First executed controller and unexercised fixture-role source, pinned before correction](https://github.com/santiagopedro1/mininet-ai/blob/bcb6b85/docs/prototypes/ollama_containment_service_fixtures_prototype.py),
  SHA-256 `26be51a005886c9e3995036da3d2a6573c62594b0895f4c30a97d42230dca3fe`.
- [Complete negative attempt receipt](ollama-containment-service-fixtures.observations.json).
- Retained scratch:
  `.prototype-gh99-artifacts/service-fixtures-29dc6602b5844fb59c72c66763a7a5ef/`.
  It contains the private trusted-source copy, planned Provider evidence directory
  and final local receipt. No acquired files were changed, created as executables,
  loaded or run. The acquired package path occurred in **unexecuted** sandbox args.

Executed once:

```sh
env -i PATH=/usr/bin:/bin LANG=C /usr/bin/python3 -I -S \
  docs/prototypes/ollama_containment_service_fixtures_prototype.py
```

## Actual negative result: control-plane connection failed before service creation

The controller invoked `systemd-run --user` once for a generated Provider-fixture
name. It returned 1 immediately:

```text
Failed to connect to user scope bus via local transport:
$DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined
```

The exact full stderr and attempted command are in the receipt. The allowlisted
controller subprocess environment contained only PATH and LANG, inadvertently
removing the user-bus locator needed by systemd CLI tools. This is a harness
control-plane environment error, **not** evidence that private networking or
namespace sharing is unsupported on this host.

**Neither service was created.** The `units.provider` entry is the attempted name
and planned evidence directory, not an observed unit/InvocationID or proof of a
running Provider. `launched` remained empty because the command failed before
connection. No existing/new service was queried, adopted, stopped or signaled;
no worker command, journal read, fixture process or namespace-setup code ran.
No private socket/listener/toy traffic, loopback ioctl, capability change, namespace
join, cgroup inventory or cleanup/cessation observation was reached.

Raw `success=false`, `exercise_completed=false`, empty check map. Controller elapsed
approximately **0.0057 seconds** with observed effective RLIMIT_AS soft/hard
134,217,728 bytes. Its own status reported VmHWM 19,876 KiB and VmPeak 31,544 KiB.
Unit limits occur only in the attempted argv and were **not** applied or witnessed.
No successful filesystem/network/ownership result can be claimed from source alone.

The controller deliberately adds a unit to its cleanup list only after a successful
transient-unit creation command, avoiding adoption/signaling of a pre-existing
name on launch failure. No fallback, automatic retry, reset or deletion occurred.
An ambiguous future creation timeout would need explicit unknown-state handling;
this observed failure is an explicit inability to connect before any creation RPC.

## Verdict and next authorization gate

**Negative attempt, runtime capability unvalidated.** The loader constraint from
static preflight remains open: unavailable unowned `/build` search path, controlled
CWD, effective dependency/environment policy and actual mapped-file ownership still
need observations. This attempt does not authorize or weaken real Ollama admission.

Proposed correction, **not executed**: explicitly supply derived non-secret user
bus locators only to trusted controller systemd CLI subprocesses:

```text
XDG_RUNTIME_DIR=/run/user/1000
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
```

Before use, check that the selected runtime directory/socket belong to the current
UID and are not unsafe redirects. Read only their filesystem metadata, not
credentials or inherited environment values. These locators are for the already
authorized per-user control plane, not an inference endpoint. Do not transfer
them into the sandbox: keep its cleared environment and hidden `/run` unchanged.
No host networking, alternate/shared daemon, `--machine` transport, broader bus
endpoint or elevated privilege fallback is proposed.

Await separate explicit human approval for the corrected attempt under the same
two-unit/controller budgets. Preserve this source/report unchanged at publication;
a correction must produce a new report, not overwrite the failure. Any subsequent
setup/namespace/path/ownership/evidence/stop failure stays negative/partial, with
no automatic retries, permission changes or real model execution.

Static validation cross-checks source/staging-receipt hashes, trusted source-copy
and local receipt equality, the sole failed control-plane command, no created-unit
identities/worker/stop commands, and retained no-runtime flags. No service attempt
or inspection rerun occurs during those checks; no production test suite runs.
#99 remains open and claimed; architecture/Provider acceptance is unchanged.

## Separately authorized corrected attempt: bus works, namespace setup fails

The human explicitly approved a corrected attempt using only the current user's
validated public bus locator for controller commands, under the same two-service
and controller caps. Runtime-directory metadata was checked without credential or
inherited-environment inspection: `/run/user/1000` is an owned non-symlink directory
mode0700; its `bus` is an owned non-symlink socket mode0666, protected by that
private parent directory. These variables remain controller-only; sandbox source
still clears its environment and hides `/run`.

- [Corrected executed source](ollama_containment_service_fixtures_prototype.py),
  SHA-256 `a0065e77b60ca3b31cba8afa9119aaf77334e4850b95c4d6c995fcf71364845d`.
- [Public corrected-attempt receipt](ollama-containment-service-fixtures.corrected.public.observations.json).
- [Exact public-export transformation](ollama_containment_fixture_public_export_prototype.py).
- Raw receipt SHA-256
  `4e9ebd8bbd61917e5b65e6fa2163989561a964fa444a4f3d49b31922f1416bf6`,
  retained unchanged and untracked locally, including in the private scratch
  directory `.prototype-gh99-artifacts/service-fixtures-0e2fb0dc20434db88048d65d9e78c45c/`.
  The publication redacts only an unnecessary machine/journal filename from one
  journal-error path; command outcomes/error meaning and source/raw hashes survive.

Exactly **one** Provider-fixture service was created. The worker was never launched;
the unused worker role was not repurposed for an unapproved diagnostic or retry.
Unit `prototype-gh99-provider-b335b7f89c954115a351a35f6cd28868.service`, invocation
`bd68bd7604bf4d7c817d8bbdeecdfc75`, initially reported active/running, MainPID30911,
MemoryMax134217728, TasksMax16, RuntimeMax50s, TimeoutStop3s, no restart,
KillMode control-group, SendSIGKILL yes and NoNewPrivileges yes.

The sandbox's trusted Python code then failed before readiness while applying
`SIOCSIFFLAGS` to its loopback interface:

```text
PermissionError: [Errno 1] Operation not permitted
```

The direct service stderr contains the traceback. The controller subsequently
reported a readiness timeout; this is a secondary symptom, not a Provider request
deadline or model failure. No listener, toy request, worker namespace join or
native Provider request was attempted. Initial namespace/interface guards were
on the executed code path before the ioctl, but their local `setup` object was
not published. Actual capability masks, user/network namespace ownership and
successful environment/filesystem probes **were not captured**; do not invent
those missing witnesses from requested flags or the controller's config fields.

The service exited with status1. The exact owned unit's explicit stop returned0;
manager then reported failed/exit-code, MainPID0 and no ControlGroup, retaining
the same invocation ID and unit settings. No live process/cgroup census was
available by cleanup time (`missing_cleanup_inventory` is explicit), so the raw
stop record has **no successful independent quiescence witness**. This is partial
cleanup evidence, not a blanket complete containment result.

Manager retained MemoryPeak11,444,224 bytes; that is its cgroup-accounting value,
not a direct RSS or complete ownership proof. Controller elapsed8.118s, own
VmHWM19,872KiB/VmPeak31,544KiB and effective RLIMIT_AS128MiB. The journal CLI
returned0 but stderr reported **Cannot allocate memory** opening a journal file,
and stdout said no entries. Do not treat that as successful absence-of-events
evidence, proven host OOM or reason to increase budgets. Direct service stderr
and manager facts remain available; full journal coverage is missing.

**Verdict remains negative/partial.** No guard removal, host-network fallback,
elevation, extra service, acquired-code execution/permissions change, model load,
inference, new download, restart/replay, reset or deletion occurred. Failed unit
metadata and scratch evidence remain retained. The controller-only bus correction
is observed working; shared private loopback, separate role ownership and complete
shutdown witnesses are still unvalidated.

### Installed documentation versus unobserved cause

A read-only inspection of installed `/usr/share/man/man1/bwrap.1.gz` documents
that `--disable-userns` sets `user.max_user_namespaces` to1 and then enters a
nested user namespace unable to raise the outer limit. The compressed manual's
hash and documentation evidence class are retained in the public receipt.

This makes an owning-user-namespace mismatch a concrete hypothesis: capabilities
in a nested child need not permit administration of a network namespace owned by
an ancestor. Missing effective CAP_NET_ADMIN is another hypothesis. The failed
run captured neither mask nor network-namespace owner; documentation alone
cannot prove which mechanism caused this particular EPERM. Do not simply remove
`--disable-userns`/assertions, add broad capabilities or weaken the boundary.

### Next proposal: one observational namespace diagnostic, not authorized yet

One new harmless disposable user-service fixture, **<=60s / 128MiB / 16tasks**, with
trusted controller **<=128MiB / 2min**, retaining current namespace guards. Publish
pre-network namespace/capability masks and keep the fixture alive for a correlated
owned-process census. The controller may inspect only that fixture's namespace
handles and the network namespace's owning-user-namespace identity (read-only
namespace ioctl), plus its own baseline for comparison, then stop only that unit
and independently observe exit/cgroup cessation.

**No interface changes or socket/traffic tests**, no namespace migration of a
worker, acquired-code execution/permission changes, model loading, inference,
downloads, host elevation/firewall changes, guard relaxation or deletion. This
diagnostic can establish new observations, not reconstruct missing snapshots of
the failed run. The corrected-attempt grant does not authorize it; no new service
or second attempt has been launched.
