# Integrated stable-Agent learning probe

**Throwaway, offline, not production. Do not merge this branch.**

This follows the [first learning-gate investigation](agno-learning.md) for
[Validate complete learning-gate and strict persistence coverage](https://github.com/santiagopedro1/mininet-ai/issues/58).
It addresses **item 1 only**: mixed supported routes through a stable cached Agent
per session. Immediate global failure admission shutdown (item 2), real Python
extension eligibility (item 3), and
[evidence crash/reconciliation](https://github.com/santiagopedro1/mininet-ai/issues/60)
remain unresolved. Positive local observations do not accept the architecture or
prove remote Provider quiescence.

## What changed

The new `integrated_learning_prototype.py` worker caches by compiled identity
within a fixed run/profile, not by route. Each Agent, manager, adapter, foreground
model, memory model, tracked executor, hooks and tool is constructed once in its
owning worker thread. Later commands change **scripted fixture inputs**, not Agent
flags, managers, model objects or execution owners. The Agent Session always uses
`trial:<compiled-agent>`; the worker's fixed memory scope selects either that
session or the compiled agent as its Learned Memory identity. The history window
is explicitly configured to twenty runs; this is not unbounded history retention.

The older controller now accepts a worker target and constructor options, and can
release the new fixture's blocked learning after gate checks. Its legacy default
worker and original route-specific fixtures remain unchanged. Historical evidence
at commit `17dc399` remains the source for the first investigation; its captured
hashes are not claimed to describe today's modified controller.

### A necessary profile distinction

Pinned Agno 3.0.11's `agent/_managers.py:start_memory_future` requires
`update_memory_on_run` **and not** `enable_agentic_memory` to start stock automatic
learning. Enabling both does not exercise both paths. We therefore use immutable,
explicit worker profiles, rather than changing flags between calls:

| Group | Fixed learning configuration | Scope |
| --- | --- | --- |
| `mixed` | Stock automatic background learning; fixed custom tool and hooks | Run |
| `agentic` | Stock agentic memory tool; fixed custom tool and hooks | Run |
| `automatic-agent-scope` | Same automatic profile | Agent |
| `agentic-agent-scope` | Same agentic profile | Agent |
| `memory-free` | No configured learning, memory-context reads, hooks or tools; two independent cached sessions | Run-qualified sessions |

This is **not** a universal Agent with both stock learning paths active. Each
profile uses its own disposable selected root and per-run worker. Mixed profiles
inside one worker, actual cross-run reuse, and arbitrary user-supplied profile
combinations are not validated by these five groups. The inert fixture memory
manager allocated in the memory-free case is not connected to its Agent or used
for DB reads/writes. Memory-free here is **not DB-free**: sessions and probe events
still persist to the one scratch database.

## Sequence and observed evidence

Each of the four memory-capable profiles completes fifteen separately dispatched
operations in one worker using the same cached Agent and owner:

1. Reasoning with `unique A history`.
2. The configured automatic or agentic learning path.
3. Manager create, task, add, replace, read and last-n search.
4. Fixed synchronous pre/post hooks and a custom manager-backed tool.
5. Scoped deletion, offline optimization and scoped clear.
6. A separate session outcome update, then further reasoning.

The captured report records **five groups, 66 terminal replies and 150 passing
observation checks**. Eleven focused regression tests independently inspect the
CLI/report boundary rather than calling the report checker as their oracle.

- Agent/manager/adapter/model/executor object identities and owner thread stayed
  constant across all fifteen routes in each profile. Recorded Agent learning
  settings and history-window configuration also stayed constant.
- Final model input retained the initial unique history through the intervening
  learning/mutation routes. Session state retained `action_result=installed`
  after the separate outcome operation. No network action or production
  coordinator commitment is simulated as having actually executed.
- Scoped snapshots observed manager-created facts, direct add/replacement,
  read/search results, both hook facts, the actual custom-tool event and tool
  learning, deletion, optimization replacement and empty memory after clear.
  The other memory scope and foreign owner's seed remained unchanged.
- The automatic operation created a real tracked background future. Its fixed
  scripted memory model paused until the controller denied both a controller
  gate acquisition and another thread's acquisition, then explicitly released
  it. The worker retained its gate through the eventual write and executor drain.
  Agentic profiles instead recorded the real `update_user_memory` tool call and
  its stored fact without falsely claiming a stock automatic future ran.
- Memory-capable operations conservatively held the root gate through dispatch,
  tracked completion, snapshots and committed terminal evidence. Successful
  replies matched stored terminal payloads, preceded by supervisor admission,
  write acknowledgement events and a zero-pending drain event. This demonstrates
  the healthy sequence, not fault/crash atomicity or durable restart recovery.
- Memory-free A/B owners met a bounded two-party **reasoning barrier**. A global
  serial dispatcher would fail this probe. Their Agent objects and owner threads
  were distinct; repeated calls retained each identity's object/owner. B saw
  neither A's unique history nor A's outcome state. No learning gate event or
  instrumented memory DB access occurred in that group.
- All five workers exited normally. Each selected root showed one main SQLite
  file and live WAL/SHM sidecars, not fallback databases. Inspection reported
  `integrity_check=ok`. No sidecar was manually deleted.

Automatic learning is still active on later reasoning calls in its profile, so
the final reasoning may learn again after the explicit clear. The report checks
the **clear operation's snapshot**, not a false promise that the final store
stays empty. Optimization is the existing fixed offline strategy, not stock
model-based summarization. Already committed learning is never represented as
rolled back.

## Rerun and provenance

With the repository's installed locked development environment:

```bash
.venv/bin/python -P mininet_ai/agents/agno/integrated_learning_prototype.py --scratch-parent /tmp/opencode/agno-learning-gate --report /tmp/opencode/agno-learning-integrated-rerun.json
.venv/bin/python -m pytest tests/agents/test_learning_gate_prototype.py -q
```

The report destination must not exist. Each invocation creates fresh disposable
`PROTOTYPE-wipe-me-integrated-*` roots; it never opens production or previous run
stores. All responses are scripted. No Provider client, credentials, network call,
provisioning, production implementation, migration or ADR change is required.

`agno-learning.integrated.observations.json` captures runtime/package inventory,
SQLite source ID/compile options, eleven relevant source hashes, command batches,
model inputs, scoped snapshots, object/owner identities, ordered DB events,
filenames, inspection pragmas and worker exits. Captured runtime is Python 3.14.8,
Agno 3.0.11, SQLAlchemy 2.1.0 and SQLite 3.54.0. This is not a deployment binary,
hardware or power-loss durability audit. As before, the 5000-ms inspection timeout
is not the Agno engine's 200-ms timeout or a production default.

The CLI exits nonzero for failed observation checks. Unit/regression tests drove
the missing behavior red before green: route-specific Agent replacement, absent
history/outcome continuity, absent agentic profile, missing held-background
evidence, missing agent scope, missing independent overlap, and absent report
checks/provenance. These tests are throwaway assets on this branch, not a new
production API or production test contract.

Validation: focused tests pass; the original 41-group/180-check probe also passes
with the controller extension. The new source and test pass Ruff and targeted
Pyright with `.venv/bin/python`. The legacy helper already had 99 Pyright errors
with that interpreter before this change; this work does not claim to repair or
typecheck the entire historical prototype. Full suite with live tests explicitly
disabled: **542 passed, 4 skipped, 179 subtests passed**.

## Human-review boundary

These are actual offline Agno/SQLite observations, with a modeled controller and
fixed trusted hooks/tool—not Mininet production integration or real extension
loader validation. The worker's exceptional/unquiesced path must not release its
gate while tracked work remains; that safety branch is not induced here. No new
claim about immediate failure admission shutdown, arbitrary descendants,
schema/path alias ownership, cancellation, retries, duplicate handling in the new
worker, crash recovery, global invocation leases or remote termination follows.

Live review should decide whether these immutable configuration-specific results
suffice for **item 1's bounded synchronous surface**, or require additional mixed
profile/interleaving coverage. They cannot on their own close the complete
learning-gate ticket, clear either remaining item, or accept the architecture.
