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

[First executed harness, pinned before correction](https://github.com/santiagopedro1/mininet-ai/blob/927e35d/docs/prototypes/ollama_containment_capability_prototype.py), SHA-256
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

## First-attempt verdict and subsequent authorization gate

**No sandbox/containment acceptance.** This attempt establishes a harness launch
failure, not absence of host isolation support. `systemd-run` success is not
sandbox readiness or successful cleanup proof. No request ledger or inference
attempt exists in this harmless fixture, and no Provider quiescence is established.

Proposed correction: add explicit `--unshare-user`, without dropping the mandatory
namespace assertions, device/path restrictions or privilege limits. This is a
diagnostic hypothesis grounded in the actual error, **not a verified fix**.
That finite authorization was consumed. The human subsequently explicitly
authorized **one more** equally bounded fixture with this flag correction.
No alternate sandbox, relaxed assertions or privilege fallback was authorized.

## Second authorized service: bounded capability checks pass

Exactly one additional service was launched with the same command, changing
only the Bubblewrap argument list to include explicit `--unshare-user`.
[Current executed harness](ollama_containment_capability_prototype.py) SHA-256:
`496b3e7f0a916d5980434bb57396dcb0be24ce06976d1ec11de7c688cfc2a371`.
[Second observation excerpt](ollama-containment-capability.second.observations.json)
retains the exact service/incarnation, host PID birth identities, namespace
identities, key outputs, command return codes and raw report digest.
The [second full raw report](ollama-containment-capability.second.raw.json) is
also captured byte-for-byte in this branch, rather than relying on scratch retention.

The harness reports **13/13 checks passed**, within approximately 0.235 seconds.
Readiness and evidence came from the actual sandbox Python fixture and its child,
not a simulation. Observed during this one run:

- All five checked namespaces differed from the controller (net/pid/mnt/user/cgroup).
- The owned cgroup was domain/populated, with memory limit 134217728 bytes and
  PID/task limit 16; unit properties recorded 50-second runtime and 3-second stop
  limits, no restart and no-new-privileges.
- Four sampled host processes (two Bubblewrap processes, Python fixture and child)
  belonged to the exact owned service cgroup. They had recorded start ticks before
  stopping and were all absent or exited during post-stop inspection.
- Fixture capabilities were all zero and NoNewPrivs was 1. `/sys`, `/run` and
  `/home` were empty. The enumerated `/dev` had only the selected basic device set,
  no accelerator nodes. `/usr` was exposed read-only. No host home directory
  or daemon control socket was intentionally exposed.
- A TCP connect to TEST-NET `192.0.2.1:9` inside the isolated namespace returned
  errno 101, network unreachable; the namespace route table had no entries.
  This is not a production egress-policy test or proof about every possible path.
- Child evidence records a loopback listener at `127.0.0.1:40661` inside the sandbox.
  No connection to it, socket-inode ownership correlation or post-stop socket
  observation was performed. Loopback usability for real client/server/runner
  traffic is not established (the interface was not brought up).
- Stop returned 0 in approximately 0.0056 seconds; the service cgroup was removed.
  The sampler captured only `populated 1`, **not** `populated 0`. Manager then
  reported `LoadState=not-found`, inactive/dead. Default properties returned for
  this nonexistent unit are not retained properties of the terminated incarnation.

Both Python processes installed SIGTERM-ignore handlers, but Bubblewrap did not.
Killing Bubblewrap/PID-namespace init can terminate descendants. Therefore this
run does **not** demonstrate the 3-second escalation path or independently prove
which signal stopped each process. Journal output records start/stopping/stopped,
not an escalation. No timeout default can be inferred from this short observation.

The controller's redundant final stop returned **5**, `Unit ... not loaded`, after
the primary successful stop and disappearance observations. The harness reports
`passed=true` because that return code is not checked. Preserve this exact command
failure as a limitation; do not infer failed physical cleanup from it, or call all
commands successful. A real gate needs explicit incarnation-aware cleanup handling.
The original raw report is unchanged; no rerun or post-hoc check mutation occurred.

## Remaining gates after the harmless fixture

The two individually authorized service attempts are exhausted. No further
service launch or resource work is authorized by their successful/failed results.
The second result is **bounded host capability evidence**, not human acceptance
of Provider containment, complete escape resistance or the architecture.

Before any real Provider work, exact acquired server/runner/library/model bytes,
model choice, effective backend, startup/load helpers, owned listeners, enforced
network isolation and whole-boundary cessation still need independently scoped
authorization and observation. The full ambiguity/cancellation/controller-loss
matrix remains entirely unperformed. Even a later passing harmless fixture cannot
prove native terminal semantics or live Provider containment. Required evidence
gaps retain admission denial; physical lock availability cannot clear them.

## Validation / publication

`git diff --check` passed after the first attempt. No production test suite ran.
The first executed harness is preserved at `927e35d`, including its failing flags.
The second is preserved with the sole explicit-user-namespace flag correction.
Static source parsing, captured source/report digest and excerpt/raw cross-checks
are performed without creating services. During the second-attempt validation,
the first attempt's raw scratch report was no longer present. Cause is unknown;
do not infer deletion, reboot or cleanup success. Its first-session excerpt/raw
cross-check passed before publication at `927e35d`; that pinned excerpt, executed
harness and recorded hashes survive, but the first full raw report cannot now be
revalidated or reconstructed from a hash. The second full report is published to
avoid relying solely on scratch. The installed executable hash samples
in the second excerpt were collected after execution; they are not a complete
dynamic-library inventory, source-build attestation or TOCTOU guarantee.
These are throwaway evidence assets, never a merge candidate. #99 remains open
and claimed; no resolution or architecture acceptance is asserted.
