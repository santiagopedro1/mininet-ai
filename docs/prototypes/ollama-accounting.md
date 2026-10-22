# Native Ollama request accounting — THROWAWAY, NEVER MERGE

Decision under live human review:
[Validate integrated native Ollama request accounting offline](https://github.com/santiagopedro1/mininet-ai/issues/98),
under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).
This branch is disposable evidence, not a production change or an implementation PR.

## Question and agreed seams

Can the locked Agno/native-Ollama client paths durably account for logical calls
and individual transport attempts, preserve ambiguous outcomes without replay,
and refuse dependent work at actual prototype admission/dispatch?

The human agreed, before exercises, to observe admission/dispatch, real locked
client transport, and reconciliation; reject unaccounted routes explicitly;
and report gaps rather than replace integration with a standalone state model.
**No human verdict accepting this evidence has yet been given.**

Current bounded result: **114 groups / 548 observation checks pass**. Ruff and
targeted Pyright pass. This is evidence for the covered client-accounting seams,
not a complete worker/storage acceptance verdict. See the explicit persistence
and composition gaps below before interpreting fixture readiness.

Two final runs produced identical group/check maps and package-source provenance.
Harness SHA-256: `c70216bfed4493cefa5a653ad8bea85cbc68f22fded74069dc6707d2aec6c656`.
The captured JSON is the second run (`offline-onhulxpg`); the first was
`offline-v2g8lz7w`. Both scratch directories are retained. Source fact review found
and prompted the dispatch, SDK-error and graph-validation regression exercises;
its final read-only pass found no remaining concrete P1 within the trusted fixture.
That review did not execute tests or grant human acceptance.

Repository base: `67ae633531340418a78a8017080a4c052cf72936`. Only this disposable
harness, findings and generated observations are changed. No production test
suite was run: no production behavior is changed, and these explicitly offline
exercises are not evidence of live integration/deployment validation.

## Run and assets

```sh
.venv/bin/python -P mininet_ai/agents/agno/ollama_accounting_prototype.py
.venv/bin/ruff check mininet_ai/agents/agno/ollama_accounting_prototype.py
.venv/bin/pyright --pythonpath .venv/bin/python mininet_ai/agents/agno/ollama_accounting_prototype.py
git diff --check
```

The first command re-executes with an allowlisted environment, a fresh scratch
HOME and no inherited credentials, proxies or Ollama host. It makes no package
downloads and uses the installed workspace dependencies. It creates a unique
`/tmp/opencode/PROTOTYPE-GH98/offline-*` directory and prints its report path.
Nothing is deleted. Expected injected failures appear in Agno's stderr, including
its generic “Failed to connect to Ollama” advice; these are fake-transport errors,
not attempts to find or contact a daemon. Optional tokenizer warnings do not
trigger installation. No real model is required.

- [Harness](../../mininet_ai/agents/agno/ollama_accounting_prototype.py)
- [Captured observations](ollama-accounting.observations.json)
- Scratch: one `PROTOTYPE-wipe-me.sqlite3` per exercise group; legacy memory,
  LearningMachine, session tables and evidence use that same selected file when
  applicable. Observations inventory files/sidecars at capture, not hypothetical
  deployment files. The evidence connection requests `synchronous=FULL`; groups
  using Agno SQLite also observe its `-wal`/`-shm` sidecars (not additional logical
  databases). Ledger-only groups start with SQLite's default rollback journal.
  This does not claim every Agno connection uses the evidence connection's PRAGMAs.
  Scratch files are not source artifacts or databases to adopt in production.

The report includes Python version, exact source/lock SHA-256, all installed
Python-source hashes for Agno, Ollama, HTTPX and Pydantic, matching installed
RECORD hashes, and the lock's wheel URLs/hashes. Archive bytes were not downloaded
or reverified here. Installed versions must equal the frozen lock. Expected key
versions: Agno **3.0.11**, Ollama Python **0.6.2**, HTTPX **0.28.1**.

## What actually executes

The fixture uses real `Agent.run` / `Agent.arun`, inherited native Agno
`Ollama.invoke` / `ainvoke`, real SDK `chat` / async `chat`, and real HTTPX request
serialization. Only the HTTPX transport/origin is scripted. A small native model
subclass overrides client acquisition, not native invocation, response parsing,
or tool-loop code. SDK subclasses wrap logical-call entry/return, retaining the
real SDK request and response methods.

Agno's actual `Model.__deepcopy__` clears cached clients. The explicit shared
scratch ledger/script copy behavior and lazy client acquisition keep memory and
learning model copies on the accounted transport. Successful scratch memory
effects and separate tool-continuation attempts are checked, not inferred from
configuration. Agentic memory/learning invokes nested extraction model calls.

### Bounded covered surface

- Synchronous and asynchronous foreground.
- Legacy automatic memory, agentic `update_user_memory`, explicit memory
  extraction/update, and built-in summarization optimization, sync and async.
- LearningMachine's built-in **UserMemory store only**, automatic and agentic,
  sync and async, with copied models and real scratch learning writes.
- One allowlisted synchronous pre-hook and tool: each makes an accounted SDK
  call and retains a separate local effect. Foreground tool continuation is an
  additional logical call, not a retry.
- Built-in background memory/learning submission: an owned executor creates
  child operations before submission and drains its actual futures. An explicit
  adapter at locked `_managers.create_task` similarly owns/drains async tasks.
  Children retain the parent's invocation/session/trial identities.
- Prototype admission, already-admitted dispatch, and transport handoff are
  real executing gates. Late commands are sent through these gates and refused;
  fake-origin observations confirm they did not arrive.

This is a trusted fixture policy, **not** arbitrary Python enforcement, extension
certification, a sandbox, or an inventory proof for arbitrary configured Agents.
The socket tripwire is an additional offline safeguard, not an egress firewall.

## Evidence order and meanings

Prototype names are deliberately not a proposed final evidence schema.

| Record | What its successful COMMIT establishes | What it cannot establish |
| --- | --- | --- |
| `run-open`, `trial-open`, `admission` | Selected synthetic identities; admitted local operation | Real service ownership, process containment or a sent request |
| `dispatch`, `logical-start` | Actual prototype operation / native chat-call entry | Remote admission or terminal completion |
| `intent` | Per-attempt local identity committed **before any handoff** | Whether a crash immediately afterward preceded or followed remote acceptance |
| `send-handoff` | Local permission to enter the fake transport was committed | A byte sent, server acceptance, or a remotely deduplicated request |
| `response` | Complete fake response bytes/status committed with SHA-256 | A terminal unless the strict wire-level predicate also holds |
| `terminal` | Exact attempt's correlated, ordered complete `done=true` native response, with accepted reason and valid assistant message | Completion of sibling calls, healthy trial outcome, local quiescence, or real server/runner cessation |
| `logical-end`, `operation-end` | The observed local call/owned operation returned through the fixture boundary | Host/device containment or permission to resume after context loss |

Every attempt carries run, trial, compiled agent, session, invocation, operation,
logical-call and attempt identities, explicit endpoint and synthetic server
incarnation. A logical call is one SDK native chat operation; model tool loops
can produce additional logical calls. Attempt identity is not an Agno run ID or
a remotely queryable Ollama resource. Native Ollama supplies no inspected
request-status lookup capable of resolving a lost acknowledgement.

Reconciliation reads SQLite rows, validates prerequisite order and parent/context
correlation, checks complete wire content/digest and terminal linkage, and keeps
missing attempts, logical endings and operation endings explicit. Live readiness
also requires no owned local activity, no frozen failure and no evidence-failure
latch. Another independent trial cannot open while the previous trial lacks that
readiness. Healthy rollover is separately exercised.

Dispatch atomically consumes a matching, one-shot durable admission: unadmitted,
identity-mismatched, concurrent duplicate and completed-operation dispatches are
rejected without replay. Already-admitted dispatch rechecks retained integrity.
Reconciliation validates lifecycle cardinalities/order and authoritative run and
trial context, including omissions that leave no sequence gap. The request budget
is shared across model/client copies for the trial, not reset per client.

The SDK wrapper latches failures through post-transport SDK validation, including
errors Agno's approved hook and learning store swallow. A complete remote-request
terminal can coexist with an incomplete local logical call; that does not grant
dependent work. Historical context loss and actual durable failure observations
are reported separately, never conflated into an invented persisted cause.

Failures close admission/dispatch. A terminal for an already-running attempt may
still be retained as late evidence; it cannot revise the frozen failed trial or
clear another attempt's unknown. New SDK attempts after ambiguity are refused.
Already committed learning/local effects are retained rather than rolled back.

Required write rejection arms a local failure latch at the failing transaction.
If that write failed, its event/cause was **not** durably recorded. Later valid
observations do not invent it. After process death, only retained history is
available; missing completions stay gaps and required live-context loss forbids
resume/new dispatch. Historical completion is not actionable continuation.

## Exercise inventory

The observation JSON is the authoritative group/check inventory and retains
each group's events, reconciled gaps, synthetic-origin observations and files.

- Strict final response; `done=false`, coerced boolean, wrong model,
  error envelope, absent reason/message; partial JSON and EOF.
- Timeout, connection failure, fake acceptance with lost acknowledgement;
  cancellation at scripted async send/read boundaries.
- Response received but lost before durable commit; nine **actual child
  process exits** after logical start, intent, handoff, fake origin admission,
  response reception, response COMMIT, terminal COMMIT, logical completion COMMIT
  and operation completion COMMIT. Parent reads retained
  SQLite without replay; process exit never grants remote quiescence.
- Deferred-foreign-key **real SQLite COMMIT rejection** at fourteen required
  event points, including evidence of failure itself. Rejected rows remain absent,
  pre-send failures prevent transport entry, and late work is refused.
- Concurrent outstanding requests; later success cannot clear an earlier unknown.
  A foreground terminal cannot clear held automatic-memory work. Real automatic
  memory runs concurrently with foreground lost acknowledgement; late terminal
  and committed memory survive while the failed run stays closed.
- Attempted replay after ambiguity is refused before a second send. Baseline
  Agent/model retries are zero and guidance retries explicitly disabled;
  nonzero retry/guidance/fallback configurations are rejected at dispatch.
- Contradictory/missing/duplicate/orphan terminal evidence, wrong session,
  invocation, endpoint/incarnation/logical identity, body/hash corruption and
  sequence gaps cannot establish readiness or start a dependent trial.

## Explicitly rejected or not demonstrated

**No public compatibility is removed by this prototype.** The existing public
API remains unchanged; the candidate acceptance boundary rejects unsupported
routes explicitly. Any future compatibility change must be specified separately.

Rejected fixture configurations include streaming, alternate/unresolved models
or endpoints, raw client parameters/clients, fallback and guidance/model/Agent
retries, parser/output/followup/reasoning models, arbitrary tools/hooks/callable
instructions, and unaccounted memory-model fallback. Native generate/raw HTTP
calls without a registered logical chat call are refused before fake-origin entry.

LearningMachine profile/session-context/entity/learned-knowledge/decision-log and
custom stores are not covered by this runtime verdict. They are not silently
disabled: non-UserMemory graphs and alternate store models are ineligible.
Entity supersession judges, custom retrieval/optimization, summary/compression
models, guardrails/evals, manual capture hooks and detached extension work need
their own supported-route evidence or explicit rejection in a production design.

Agno's separate reasoning capability probe uses top-level HTTPX `/api/show`,
bypassing injected model clients; reasoning routes are rejected, not miscounted
as native chat. Arbitrary code can evade a cooperative allowlist/tripwire and
remains outside this fixture's claim.

### Retry boundary

Pinned SDK request code has no observed retry loop; failing fixtures observe one
transport entry rather than inferring zero retries from Agent settings. The
HTTPX transport here is entirely fake (no inner connection retries, mounts or
proxies); redirects are explicitly disabled. Model/Agent/guidance/fallback retries
are disallowed and tested as rejected configurations, not claimed to have been
exhaustively executed. Additional model/tool/learning calls have their own logical
identities. Server-side loading/runner retries, intermediaries, proxy replay and
real effective configuration cannot be validated by this harness.

## Limits and handoffs — acceptance remains blocked

1. **No real Provider evidence:** all responses, admission and server-incarnation
   identities are synthetic. No existing daemon was inspected or contacted; no
   service provisioned, inference performed, credentials inspected or model
   downloaded. Real server/runner/backend ownership, offload prevention, response
   semantics, network behavior and bounded cessation remain unvalidated.
2. **Not production-worker composition:** this is actual client/Agno execution
   inside a prototype dispatch harness, not the production coordinator or complete
   long-lived worker IPC, full learning gate, deployment containment, persistent
   cross-controller quarantine or compatibility implementation. Prior accepted
   worker/crash evidence is an input, not proof those mechanisms compose here.
3. **Bounded persistence/crash evidence:** deferred constraints test COMMIT
   rejection, not disk-full, I/O/fsync failure, physical corruption or power loss.
   Exits are at enumerated boundaries, not arbitrary instruction-level crashes.
   No exactly-once, rollback, transparent replacement or restart/resume guarantee.
   Agno storage failure adapters are not newly validated by these ledger probes.
   In particular, built-in memory tools can convert required-store failures into
   error strings/logs. Their absence from the transport ledger is **not** successful
   persistence. `normal_ready` means this fixture's request-accounting/local-work
   boundary only; it cannot authorize production worker success without the strict
   persistence adapters and complete learning gate, whose composition is still
   unvalidated here. Happy-path scratch writes do not clear that gap.
4. **Scripted cancellation phases:** async cancellation occurs in real client
   tasks at fake transport send/read gates, not actual socket syscalls or server
   admission. Fake connect failure is conservatively unknown, not proof of remote
   non-admission. A close/timeout/worker exit/cache operation never clears it.
5. **Unchanged containment gate:** any unresolved attempt or required evidence
   means fail the run and retain the need for verified full owned Provider
   containment before independent new work. This harness supplies no such witness.

Return these limits to
[Choose a verifiable Provider quiescence acceptance route](https://github.com/santiagopedro1/mininet-ai/issues/59)
and [Choose a database-free or single-database storage architecture](https://github.com/santiagopedro1/mininet-ai/issues/41).
[Validate dedicated Ollama Provider and runner containment](https://github.com/santiagopedro1/mininet-ai/issues/99)
still requires a live human deployment choice and **separate explicit runtime
authorization**. Closing this offline ticket cannot grant those guarantees.

## Inputs and pinned source observations

- [Provider completion/cancellation source baseline](https://github.com/santiagopedro1/mininet-ai/issues/55#issuecomment-6200541147).
- [Selected native Ollama route](https://github.com/santiagopedro1/mininet-ai/issues/59#issuecomment-6342644612).
- [Pinned server/runner source proposal](https://github.com/santiagopedro1/mininet-ai/issues/100#issuecomment-6343685544): v0.40.8 is a source candidate, not a deployment selected or executed here.
- [Accepted worker protocol](https://github.com/santiagopedro1/mininet-ai/issues/54#issuecomment-6200618039).
- [Accepted same-file crash boundary](https://github.com/santiagopedro1/mininet-ai/issues/60#issuecomment-6224621796).

Installed Agno paths (relative to its pinned distribution):
`models/ollama/chat.py` (native calls/client acquisition, non-strict done parser),
`models/base.py` (tool loops, retry/guidance, deepcopy clearing clients),
`agent/_managers.py` (automatic memory/learning futures/tasks),
`memory/manager.py` (copied extraction/update, optimization, OpenAI fallback),
`learn/stores/user_memory.py` (automatic and agentic copied extraction),
`learn/machine.py` (other store graphs and detached capture hooks),
`reasoning/ollama.py` (separate HTTPX probe),
`agent/_run.py` and `utils/agent.py` (error/status and background-error handling).
Ollama `_client.py` supplies SDK request/response paths and constructor defaults.
Exact installed source digests are retained in the report.
