# Ollama containment capability — THROWAWAY, NEVER MERGE

Investigation: [Validate dedicated Ollama Provider and runner containment](https://github.com/santiagopedro1/mininet-ai/issues/99),
under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).
Date: 2026-10-22. Base: `67ae633531340418a78a8017080a4c052cf72936`.
No PR, merge, production implementation or ADR change is intended.

## Human decisions and authorization

The human agreed to this development machine, proposed Ollama v0.40.8 Linux
amd64 packaged-runner CPU-only profile, exclusively owned disposable deployment.
The human separately authorized read-only eligibility inspection, then **one**
harmless fixture service: at most 60 seconds, 128 MiB, 16 tasks, scratch under
`/tmp/opencode/PROTOTYPE-GH99`. Only this fixture service may be stopped.
No Ollama acquisition/startup, model selection/download/loading, inference,
elevated access or existing-daemon discovery was authorized or performed.

Inputs reviewed: #100 source candidate at `6b423a1423a2a2808803880f7191accc59a94eeb`
and #98 bounded offline evidence at `393cab0`, including its human acceptance
and remaining worker/persistence/real-Provider gaps. Neither grants runtime safety.
The glossary, map Notes and ADRs 0004/0009 remain unchanged. No unsupported GPU
profile is implicitly accepted, and Independent Agent Invocation concurrency is
not redefined.

## Read-only eligibility observations

- Arch Linux x86-64, kernel `7.2.11-arch1-1`, UID 1000, systemd `262.1-1-arch`.
- `/proc/self/mountinfo` reports cgroup2 at `/sys/fs/cgroup`.
- User manager reports `/user.slice/user-1000.slice/user@1000.service`;
  that service reports `Delegate=yes`, `DelegateControllers=cpu memory pids`.
- The current shell is in `app.slice/kitty-8539-0.scope`. Relevant ancestor
  cgroups report domain type; `app.slice` and this scope expose writable
  `cgroup.kill`. These **unrelated existing cgroups were never signaled**.
- `systemctl`, `systemd-run`, `unshare`, `bwrap`, `ip`, `nft`, `iptables`,
  `nsenter`, `setpriv` are installed. Tool presence and apparent writability
  do not prove that a configured sandbox will work.

These facts were printed by the read-only inspection; they are not a deployment
measurement, network policy test or containment acceptance.

## First and only authorized service attempt: negative capability result

Command executed once:

```sh
python3 -I -S docs/prototypes/ollama_containment_capability_prototype.py
```

[Retained executed harness](ollama_containment_capability_prototype.py), SHA-256
`06ecf82625c80dcbd7fc5930cff34816eb615ada0ff30b4cb4854288ea03d90f`.
[Captured observation excerpt](ollama-containment-capability.observations.json)
contains exact relevant outputs and raw scratch artifact digests. Full command
arrays, timings and manager snapshots remain in the retained raw report.

`systemd-run` returned 0 and provided an invocation ID. The service initially
reported active, then failed with main exit status 1. Bubblewrap 0.13.1 emitted:

```text
bwrap: --disable-userns requires --unshare-user
```

The command used `--unshare-all --disable-userns --assert-userns-disabled` but
omitted explicit `--unshare-user`. This failed before the Python sandbox fixture
or child produced readiness evidence. The controller correctly returned a
negative result but its secondary error (`Missing or unexpected fixture cgroup`)
is less informative than the retained Bubblewrap stderr.

The exact newly created service was explicitly stopped once; stop returned 0.
Final manager observation: `ActiveState=failed`, `MainPID=0`, `Result=exit-code`,
`ExecMainCode=1`, `ExecMainStatus=1`, `ControlGroup=` (empty). No broader signal,
reset-failed, cleanup deletion or second service launch was performed. Failed
unit metadata and scratch files remain retained. No process inventory,
populated-to-empty observation, kill escalation or listener test was reached.

## Verdict, limits and next gate

**No sandbox/containment acceptance.** This attempt establishes a harness launch
failure, not absence of host isolation support. `systemd-run` success is not
sandbox readiness or successful cleanup proof. No request ledger or inference
attempt exists in this harmless fixture, and no Provider quiescence is established.

Proposed correction: add explicit `--unshare-user`, without dropping the mandatory
namespace assertions, device/path restrictions or privilege limits. This is a
diagnostic hypothesis grounded in the actual error, **not a verified fix**.
The finite authorization has been consumed; do not rerun without separate human
approval for another equally bounded fixture service. No minimization, repeat
reproduction, alternate sandbox or privilege fallback was authorized.

Before any real Provider work, exact acquired server/runner/library/model bytes,
model choice, effective backend, startup/load helpers, owned listeners, enforced
network isolation and whole-boundary cessation still need independently scoped
authorization and observation. The full ambiguity/cancellation/controller-loss
matrix remains entirely unperformed. Even a later passing harmless fixture cannot
prove native terminal semantics or live Provider containment. Required evidence
gaps retain admission denial; physical lock availability cannot clear them.

## Validation / publication

`git diff --check` passed after the first attempt. No production test suite ran.
The executed harness is preserved unchanged, including its failing launch flags.
These are throwaway evidence assets, never a merge candidate. #99 remains open
and claimed; no resolution or architecture acceptance is asserted.
