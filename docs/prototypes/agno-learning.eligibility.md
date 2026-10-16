# Worker-local entrypoint, extension and storage ownership probe

**Throwaway, offline, not production. Do not merge this branch.**

Question: [Validate worker-local extension and ownership eligibility](https://github.com/santiagopedro1/mininet-ai/issues/73).
This is item 3 of [Validate complete learning-gate and strict persistence coverage](https://github.com/santiagopedro1/mininet-ai/issues/58),
not architecture acceptance. The accepted item-1 baseline is unchanged. The
reviewed admission-failure fixture is reused; its global timing guarantee is not
re-proved here. **Status: awaiting live human review.**

## Enumerated candidate boundary

These forms are fixed in the probe before execution, not discovered by trying to
make arbitrary Python work. All supported Agents use scripted models, the selected
SQLite file, one stable owner thread per cached session identity, immutable
entrypoint/profile selection, and the bound adapter/executor. The generated modules
are actual scratch `.py` files loaded with `importlib`, once per worker/form.

| Form | Candidate eligibility / rejection |
| --- | --- |
| Synchronous factory | Fresh Agent/model/adapter/executor per distinct session; cached object reused for that session. |
| Module-level Agent | Constructed during worker import; eligible for exactly one session identity. |
| Singleton-returning factory | First owner only; another identity is rejected before reasoning. No cloning, silent sharing or global serialization fallback. |
| Worker-local initialized resources | Controlled client surrogate, lock and live SQLite connection constructed on owner thread; use **and close** on that thread. Does not preserve parent identity/state. |
| Import-thread resource | Module-level SQLite connection works on import owner; requesting it from another owner fails during factory preparation with an affinity diagnostic. |
| Parent-dependent factory | Actual coordinator-only module global contains a live connection/lock; spawned worker imports the default `None` and rejects the factory. No live resource crosses IPC. |
| Running-event-loop assumption | Rejected in the synchronous owner thread: no running loop. No automatic loop creation or async compatibility claim. |
| Hooks / custom tool | Only the fixed synchronous functions in this probe; scoped memory search and writes through the bound manager/adapter. |
| Owned extension background work | Fixed pre-hook submits only to the tracked executor; required memory work remains accounted for until drained. |
| Raw engines, alternate stores, untracked work, async factory | Descriptor contract explicitly rejected **before module import**. This is a modeled allowlist, not introspection, sandboxing, or proof that undeclared Python cannot bypass it. |

Module/factory import and construction are inside the spawned worker, on the
owning thread. The coordinator pre-creates disposable schemas using the existing
fixture, but sends only normalized scalar command descriptors, never live Agents,
clients, connections or locks. One short registry/import lock protects singleton
claims; it is not held through reasoning. Independent memory-free invocation
identities must cross a simultaneous two-party model barrier.

## Observations

See [captured report](agno-learning.eligibility.observations.json) and
[probe source](../../mininet_ai/agents/agno/worker_eligibility_prototype.py).

The final report has **11 groups / 97 checks**, all passing. The groups are loader,
three healthy extension groups, six extension-fault groups, and the alias group.
Repeated runs agree on checks, not PIDs/object IDs/temp paths or thread scheduling.

- Worker import/construction PIDs differ from the coordinator. Repeated factory,
  module-level and local-resource invocations retain Agent, model, manager,
  adapter, executor and owner identities. Fresh factories have separate adapter
  engines. A private history marker reaches A's next invocation, not B's.
- Both module-level and factory-returned singleton aliases are explicitly rejected.
  Two distinct memory-free invocation identities have distinct objects/owners and
  complete the simultaneous barrier: reasoning is not globally serialized.
- The real worker-local connection and lock are exercised twice; a module-global
  connection fails on another owner thread. The first development run caught a
  cleanup affinity violation; cleanup now runs on the owner thread too. This is a
  requirement of this fixture, not evidence all initialized resources are safe.
- Hook, tool and tracked-background searches/writes occur with the root gate held.
  The external coordinator cannot take that gate while owned background work is
  blocked. Background completion and an empty tracked executor precede terminal
  success; the gate is retained until worker shutdown after all owners drain.
- Required memory writes are faulted as raised exceptions and quiet missing-row
  results on each extension surface. **All six Agno runs report completed**, but
  adapter errors and the write-armed failure latch make their operation results
  unsuccessful. Subsequent admission is denied. Failed writes leave no extension
  memory row. Healthy writes are scoped; the three seeded foreign scopes remain
  unchanged. This checks extension participation, not concurrent admission timing.
- Per-operation events record commands, owners, gate state, DB attempts/acks/errors,
  failure latch, foreground return, drain and terminal ordering. These are returned
  in the report, **not a new durable evidence protocol**. An abrupt worker death
  can lose them; evidence failure/reconciliation remains elsewhere.

## Bounded storage alias results

The selected root canonicalizes before DB/lock selection. The fixture pins root,
DB and gate `(device, inode)` identities; the adapter checks them before each
enumerated access. DB/gate symlinks and hardlinks are not supported. Ordinary root
symlinks are allowed only while their target remains pinned.

| Scratch observation | Result |
| --- | --- |
| Relative vs canonical selected root | Same root/DB/gate inode tuple. |
| Root directory symlink | Same canonical root/DB/gate. |
| Competing owner through root symlink | Gate acquisition times out; no conflicting DB operation is attempted. |
| DB hardlink | Rejected by link count before adapter access. |
| DB symlink | Rejected; not opened as a second store. |
| Gate replacement | Rejected by inode change. |
| DB replacement | Rejected by inode change. |
| Root replacement | Rejected by root inode change. |
| Root symlink retarget | Rejected by selected-target change. |

These are real scratch filesystem mutations followed by real ownership checks,
not live SQL writes racing with namespace mutation. Gate contention through the
root alias is between separate file descriptions in one process; the background
groups additionally observe worker-vs-coordinator process contention. A pin is
checked again on each enumerated adapter call, but **check/use filesystem races,
hostile concurrent rename/unlink, bind mounts, inode reuse, network filesystems,
multi-host access and arbitrary external SQLite clients are not validated**.
The bounded contract requires a cooperative, stable local namespace while work is
active. A broader mutation/hostile-filesystem guarantee would require a blocking
follow-up, not acceptance based on these checks. No production ownership manifest,
schema or migration policy is proposed.

Each active loader/extension group observes one physical
`PROTOTYPE-wipe-me.sqlite3`, plus its `-wal`/`-shm` sidecars and the learning lock;
there is no fallback database. The alias group deliberately retains replaced files
and separate scratch roots for inspection; those are mutation fixtures, not
fallback run stores. The parent's `:memory:` connection is solely the never-sent
parent-resource fixture. Files are sampled before adapter close, not monitored
continuously. Per-engine `database_list`, journal mode and synchronous setting are
reported from worker connections; later inspection pragmas are separately named.

## Facts, enforcement and limits

- **Source facts:** the inherited `StrictDb` checks scoped memory ownership,
  required upsert rows and run-row readback; `LatchDb` arms its callback when
  `checked` raises. `TrackedExecutor` drains recorded futures. Their implementation
  is intentionally incomplete outside enumerated routes.
- **Observed:** actual Agno 3.0.11 / SQLAlchemy 2.1.0 / SQLite 3.54.0 on Linux,
  Python 3.14.8, spawned processes, real hook/tool execution, SQLite rows and local
  files/locks. Runtime and SQLite compile options are in the report.
- **Scripted/modeled:** model responses; missing-row fault injection skips the
  inherited backend write; the trusted fixture allowlist and normalized command
  loader are candidate enforcement, not production compatibility validation.
- **Contractual:** eligible extensions must honestly declare memory/resource
  needs, use only bound accounting interfaces, and register every required task.
  No automatic detection of covert raw DB access or untracked descendants is
  claimed. Construction/import side effects must obey the same contract; arbitrary
  modules cannot be safely imported first and checked afterward.
- **Not covered:** async/streaming/Team/Workflow/AgentOS, arbitrary profiles,
  arbitrary strategies/hooks/tools, arbitrary resource clients, alternate stores,
  unregistered executors, malicious Python, remote Provider completion, abrupt
  death/recovery, deployment descendant containment, or whole-operation rollback.
  Background timeout deliberately retains the gate; this session does not claim
  a new containment implementation or exercise that branch.

The bounded evidence can support an explicitly narrowed eligibility decision; it
cannot establish transparent compatibility with arbitrary Python. No new ADR or
public compatibility policy is made here.

## Reproduce and checks

From this throwaway branch, use a fresh report path:

```bash
.venv/bin/python -P mininet_ai/agents/agno/worker_eligibility_prototype.py --scratch-parent /tmp/opencode/gh73-rerun --report /tmp/opencode/gh73-rerun.json
```

No Provider client, network, credentials, actual-data migration or existing store
cleanup is involved. Scratch artifacts remain for inspection.

- Probe: 97/97 checks; repeated final runs must agree on the check map.
- Existing offline suite: 547 passed, 4 skipped, 179 subtests passed.
- Targeted Ruff and Pyright (using `.venv/bin/python`): pass for the new source.
  This is not a whole-repository typecheck claim.
- No new tests: the agreed CLI/report checks are the prototype observation seam.

Human review must choose **suffices for this explicit boundary**, **narrow/follow
up**, or **negative verdict**. Until then the ticket stays open, the parent
investigation stays open, and architecture acceptance is not cleared. Concrete
compatibility and lifecycle decisions remain with
[Define compatibility and migration boundaries](https://github.com/santiagopedro1/mininet-ai/issues/44)
and [Define storage lifecycle and integrity guarantees](https://github.com/santiagopedro1/mininet-ai/issues/43).
