# Ownership/network fixture attempt — THROWAWAY GH-99, NEVER MERGE

2026-10-22. The human explicitly authorized two harmless disposable user services,
each <=60 seconds / 128 MiB / 16 tasks, with a trusted controller <=128 MiB process
address space / 2 minutes. Goals: separate owned cgroups/PID/filesystem views,
shared private loopback, constrained loader-search paths and observed cleanup.
No host elevation/firewall changes, acquired-code execution/permissions changes,
model loading, inference, downloads or deletion were authorized or performed.

## Evidence

- [Executed controller and unexercised fixture-role source](ollama_containment_service_fixtures_prototype.py),
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
