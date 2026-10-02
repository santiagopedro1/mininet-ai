# Runtime directories and per-run artifacts

## Objective

Eliminate recurring control-socket permission failures caused by project-local
directories, especially VirtualBox shared folders at `/vagrant`. Separately,
organize saved experiment output by run without breaking persistent memory.

Keep the existing user-facing commands:

```bash
mininet-ai run experiment.yaml
mininet-ai agents RUN_ID
mininet-ai invoke RUN_ID AGENT_ID --intent '...'
```

Default agent discovery and invocation must work from different working
directories on the same machine, provided they use the same OS user.

## Current implementation

- `mininet_ai/runtime/control.py` defaults to `.mininet-ai/control`, hashes run
  IDs into socket filenames, and enforces directory/socket ownership and peer UID.
- `run`, `agents`, and `invoke` in `mininet_ai/cli.py` share that default.
- Project-local directories may have broad permissions, different ownership, or
  shared-filesystem behavior that defeats `chmod` and Unix sockets.
- Logs and databases currently use shared paths under `.mininet-ai/`.
- The CLI opens logs and databases before `ExperimentRuntime.start()` deploys
  the network and obtains a run ID. Per-run paths therefore need a lifecycle seam.
- Both built-in substrate runtimes already accept a `run_id_factory` constructor
  argument; the runtime registry currently uses zero-argument factories.

## Scope and sequencing

1. Fix runtime directory resolution and socket lifecycle first.
2. Introduce per-run saved artifacts in separate commits.

Do not weaken permission checks, silently change ownership, or use public socket
directories. Do not modify the user's experiment blueprints or long-term plan.
Improved agent-message logging and VM tooling are separate roadmap items, not
prerequisites for this fix.

## Runtime communication layout

Resolve a runtime base at command execution time, not module import time:

| Execution user | Default base |
| --- | --- |
| Root | `/run/mininet-ai/control` |
| Non-root, valid `XDG_RUNTIME_DIR` | `$XDG_RUNTIME_DIR/mininet-ai/control` |
| Non-root, variable unset | `/tmp/mininet-ai-<effective-uid>/control` |

Use a deterministic hash of the full run ID for a short, safe directory name:

```text
<runtime-base>/<run-id-hash>/control.sock
```

Hashes avoid path traversal, overlong run IDs, and Unix-socket path length issues.
The original run ID remains validated in requests and responses.

### Resolution and security rules

- Introduce one resolver used by `IntentServer` and `IntentClient`.
- Prefer root's `/run` location even if `sudo` preserves `XDG_RUNTIME_DIR`.
- A configured `XDG_RUNTIME_DIR` must be absolute, private, user-owned, and a real
  directory. If invalid, fail with an actionable error rather than silently
  choosing a different location for client and server.
- Use the fixed `/tmp` fallback rather than an arbitrary `TMPDIR`, which could
  point back into a shared project folder. Validate its user-specific directory
  before use; never adopt an existing directory belonging to another user.
- Create application-owned directories with `0700` and sockets with `0600`.
- Reject symlinks at application-owned directory and socket boundaries. Use
  race-resistant filesystem operations when checking and creating those paths.
- Preserve socket owner checks, peer UID checks, run matching, optional digest
  matching, request size bounds, deadlines, and no automatic submission retries.
- Report the resolved path on startup and in relevant connection errors.
- Keep `--control-dir` as an explicit base-directory override. Relative overrides
  remain relative to the command's working directory; recommend absolute paths.
- Clients must not create missing run directories while searching for an owner.
- Check socket path length before bind/connect and report a specific actionable
  error for an overlong explicit override.

### Lifecycle and recovery

- Remove the owner's socket and its now-empty per-run runtime directory on
  orderly shutdown; preserve the shared runtime base and other runs.
- Clean up partial setup failures without deleting anything not created by that
  server. Preserve the existing inode/identity guard for socket removal.
- An existing endpoint must not be unlinked blindly. Stale cleanup needs verified
  ownership and proof that no live listener owns it; otherwise fail safely.
- Handle concurrent runs independently. Hash collisions must fail safely rather
  than take over another endpoint.
- Explain that changing defaults requires restarting existing runs. No implicit
  fallback to `.mininet-ai/control`; old runs remain accessible only using their
  explicit legacy directory and compatible endpoint layout.
- Because the proposed per-run socket layout changes explicit-directory layout
  too, document that a new client cannot address an old owner's flat socket.
  Restart is the supported migration; do not imply `--control-dir` alone bridges
  both protocol/layout generations.

## Saved artifact layout

Use a separate configurable artifact root, defaulting to `.mininet-ai/`:

```text
.mininet-ai/
  <run-id>/
    logs/run.log
    dbs/ledger.sqlite3
    dbs/shared-state.sqlite3
    dbs/agno.sqlite3
    artifacts/
```

- Saved output survives shutdown; sockets never live in this tree by default.
- Resolve and display absolute artifact paths at startup. Artifact-root location
  does not affect how `agents` and `invoke` find the owner.
- Preserve explicit `--log-file`, `--ledger-db`, `--agno-db`, and
  `--shared-state-db` paths. Make their defaults optional in the CLI so the layout
  resolver can distinguish omitted options from deliberate overrides.
- Use safe run-ID path handling, reject traversal and collisions, and never
  overwrite an existing run's output. Built-in run IDs are filesystem-safe;
  provide a deterministic safe mapping for other IDs if needed.
- Keep sensitive-file protection. Moving sockets off `/vagrant` does not make
  shared-folder SQLite storage safe or private. If private permissions or SQLite
  locking cannot be supported, recommend an explicit local artifact root and
  fail clearly rather than relaxing protections.
- Leave existing logs and databases untouched; do not automatically migrate,
  rename, or delete them.

### Lifecycle seam for run IDs

Before implementing artifact defaults, establish how the run ID becomes known
before logs and databases are opened:

- Prefer reserving the built-in substrate's ID and supplying it through its
  existing `run_id_factory`, using a small composition-layer change.
- Keep the reserved ID identical to the deployed `RunInfo.id` and ledger/session
  identities. Add tests for fake and Mininet/OVS construction paths.
- Do not start the complete experiment without its ledger just to learn the ID.
- Do not rename active SQLite files or directories with open WAL connections.
- If runtime/provider contract changes prove necessary, stop and classify them
  under `docs/compatibility.md`; do not silently change a versioned protocol.
- Define failed-start behavior: retain diagnostic output in its reserved run
  directory, mark startup failure, and ensure networking/control cleanup occurs.

### Cross-run memory and run history

- Run-scoped shared state and sessions use per-run databases by default.
- `learned.scope: agent` intentionally reuses learned memory across runs. Preserve
  that through a stable private Agno store when requested, or an explicit shared
  `--agno-db` path; do not silently substitute a fresh database.
- Document the stable store as an exception to per-run placement and record its
  actual path. Validate what separation Agno's storage API supports before
  implementing multiple databases.
- Preserve history by retaining each run ledger. A global run index/list command
  is optional follow-up work, not required for socket discovery.

## Tests and acceptance criteria

### Runtime directories

- Test root selection, valid/unset/invalid `XDG_RUNTIME_DIR`, fallback behavior,
  explicit overrides, and resolution under different working directories.
- Test wrong ownership, broad modes, symlink paths, overlong socket paths,
  existing endpoints, cleanup identity, and partial startup failure.
- Verify run mismatch and peer checks remain enforced after layout changes.
- Verify two owners use separate directories and stopping one leaves the other.
- Cross-process test: start in one working directory, discover/invoke from
  another without YAML or `--control-dir`, then drain and remove the endpoint.
- Run the VM scenario from `/vagrant` using `sudo` for all commands, without
  manual `chmod`, `chown`, or a control-directory flag. Use the local runtime
  filesystem and verify no socket appears on the shared mount.

### Artifacts

- Two runs produce distinct logs and run-scoped databases.
- Overrides are honored; existing legacy artifacts are untouched.
- Test reserved/deployed run-ID agreement and failed-start diagnostics/cleanup.
- Test that agent-scoped learned memory still persists across runs and run-scoped
  state remains isolated.
- `--dry-run` creates no runtime or artifact directories/databases.
- A local artifact-root override works in the VM if shared-folder storage fails.

Run focused control/CLI/storage tests regularly, then `pytest`, changed-file Ruff
checks, and Pyright. Report unrelated pre-existing failures separately. Privileged
VM checks should run only in the disposable VM.

## Documentation and commit-sized delivery

Use conventional commit messages matching the repository and a `feature/...`
branch for new implementation work.

1. `feat(runtime): resolve private control directories outside the project`
   - Runtime-base resolver, override behavior, diagnostics, focused tests.
2. `feat(runtime): isolate control sockets by run`
   - Per-run endpoint layout, secure setup/cleanup, concurrency and socket tests.
3. `chore(docs): document automatic runtime directory discovery`
   - README, compatibility notes, examples, same-user requirement, restart
     migration, and working-directory-independent default commands.
4. `feat(runtime): reserve run identities before opening artifacts`
   - Composition seam, startup failure handling, focused lifecycle tests.
5. `feat(cli): organize default artifacts by run`
   - Artifact-root option, per-run defaults, explicit overrides, persistent-memory
     exception, tests, and documentation of actual effective paths.

Update existing tests that inspect flat socket directories or assume static
default paths. Keep the runtime-directory fix independently releasable from the
artifact migration. Review security and contract implications before merging.
