# THROWAWAY: Linux parent-death containment counterexamples

Question: can Linux parent-death signaling, with a post-arm parent identity
check, establish the selected worker/descendant containment boundary?

**No.** This narrow candidate fails even for tracked children that never leave
their process group. It is not a verdict that no Linux deployment can satisfy
the contract. Live human review is required before resolving the decision.

## Run

```sh
python mininet_ai/agents/agno/containment_prototype.py
```

Standard library only; direct file invocation avoids package imports. A first
attempt using `python -m` failed before execution because the system interpreter
lacks the package's Pydantic dependency. The direct invocation succeeds without
installing dependencies or calling Agno/Providers.

The script creates scratch traces under `/tmp/opencode`, launches only disposable
fixture processes in fresh sessions, and signals only those fixture groups. It
temporarily makes its own process a Linux subreaper so orphan fixtures can be
reaped. No unrelated processes or existing data are targeted. Scratch traces
are removed after verified fixture exit; captured JSON retains their events.

## Observed boundary

Four groups, ten observation checks. A true check can mean a **demonstrated
failure**, not a passed acceptance requirement.

- Force supervisor death before `PR_SET_PDEATHSIG`: without a parent identity
  recheck, the orphan arms against its new parent and wrongly reaches readiness.
- Repeat with a post-arm expected-parent check: mismatch is rejected and no
  readiness is observed. This covers that forced ordering, not all startup races.
- Launch from a temporary supervisor thread: worker death occurs when that
  creator thread exits, although the supervisor process remains alive. A
  process-level `getppid()` check does not solve creator-thread ownership.
- Kill a supervisor after its worker has a tracked child and grandchild: the
  worker exits, but both descendants survive and ignore SIGTERM. An **external
  surviving harness** kills their fixture process group with SIGKILL and observes
  exit. This is cleanup evidence, not an implemented deployment guardian.

The JSON records host/kernel, Python version, source hash, PIDs, parent PIDs,
monotonic trace times and each observation. `/proc` absence or zombie state is
used as exit evidence; zombie reaping is attempted separately. The three-second
poll bound and short TERM observation interval are experiment controls, not
production timeout defaults, latency guarantees or a scheduling proof.

## Implications and limits

Parent-death signaling plus worker-owned tracking alone is insufficient. The
worker cannot clean descendants after SIGKILL. A surviving containment owner,
appropriate service/cgroup boundary, or another justified mechanism needs its
own deployment-bounded validation. This session neither provisions nor accepts
one. Detached/untracked descendants remain unsupported, not sandboxed.

No real Agno operation, gate, SQLite write, action outcome, sibling admission,
phase cancellation or production coordinator was exercised. No proof of bounded
startup under hostile scheduling, durable orphan recovery, controller/harness
death containment, deliberate unverified-exit denial, or gate retention is
claimed. Fixture PID/group ownership is cooperative and short lived; this is not
a production PID-reuse defense. Trace writes are not a crash-durable protocol.
There are no databases/sidecars in this probe. Previously committed learning and
known outcomes are untouched, not tested here.

Required uncovered guarantees remain architecture blockers. Local fixture exit
never establishes remote Provider termination. No live calls, credentials,
provisioning, production code, migrations, ADR changes or architecture acceptance.

## Proposed human review

Retain these counterexamples as partial negative evidence; reject a bare
parent-death-signal candidate. Choose a concrete surviving containment owner
before expanding into the remaining deployment acceptance matrix. Keep the
current decision open pending that selection/validation, or return an explicit
negative verdict to the architecture decision. Do not treat external harness
cleanup as a supported deployment mechanism.
