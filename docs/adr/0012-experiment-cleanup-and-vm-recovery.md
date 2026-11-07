# Make owner-led teardown sufficient and retire the cleanup script

Normal experiment completion must rely on automatic, run-owned teardown and
verification, not a machine-wide reset. Retire the custom cleanup script and its
baseline workflow rather than build a replacement recovery tool. Focus this
delivery on teardown while the experiment owner is alive.

## Current scope

- Cover normal completion, experiment failure, graceful interruption, and partial
  deployment while the owner remains alive. Do not redesign recovery after an
  owner crash or `SIGKILL`, or expand its guarantees.
- Remove `scripts/check-mininet-cleanup.sh` and its baseline workflow when this
  design is implemented. Update VM tests to check teardown results directly;
  do not replace the script with an `inspect`/`recover` tool or thin launcher.
- Manual `mn -c` remains an external, destructive troubleshooting option. It is
  neither normal completion nor proof of cleanup, and must not be an automatic
  fallback after failed teardown.
- This scope supersedes the earlier recovery-tool proposal, including its
  selectors, acknowledgement mechanism, interface migration, and shared
  teardown/recovery implementation plan. No implementation is authorized yet.

## Teardown contract

- For this delivery, verification covers the declared runtime-managed resource
  set: created topology resources, tracked workers/processes, control endpoints,
  and active registry state. Preserve Run Evidence rather than treating retained
  evidence as residue. This is not a whole-host or arbitrary-descendant absence
  guarantee.
- Use the live owner's creation records and existing identity checks. The
  supported operating assumptions are trusted experiment code and no external
  replacement of resources during the run. Ambiguous identity or failed
  inspection remains unknown, not permission to delete or declare success.
- The substrate verifies its owned resources; the experiment lifecycle coordinates
  verification across other resource owners. Ordinary successful runs must not
  need the recovery script.
- Preserve experiment-work outcome separately from cleanup outcome. Failed
  inspection leaves cleanup unknown and the overall command nonzero; successful
  execution of cleanup commands is not evidence of resource absence.
- Preserve unrelated resources; no global OVS sweep, process-name kill, or broad
  temporary-file deletion is part of owner-led teardown.
- Automatic cleanup requires ownership evidence and identity checks before
  deletion; expected names or stale PIDs alone are insufficient. Record ownership
  durably during creation. Unresolved creation or identity gaps remain unknown
  rather than authorizing deletion of potentially unrelated resources.
- Use a configurable shared cleanup budget, defaulting to 30 seconds, on a
  best-effort basis. Bound external commands where possible. Existing synchronous
  Python shutdown can exceed this budget; there is no hard return deadline or
  universal interruption guarantee. Exhausting a budget is neither successful
  cleanup nor proof that ongoing cleanup work stopped.
- Report local cleanup separately from containment of outstanding Provider
  requests. Completed requests need no remote cancellation; local worker exit or
  connection closure alone does not prove remote termination. Unresolved Provider
  activity blocks another repeated trial without implying local network residue.
  Do not promise remote termination without supporting Provider capabilities.
- Retain ownership records and cleanup progress until verification; preserve the
  final receipt as Run Evidence. Owner-led stop retries must be idempotent, and
  active coordination state is removed only when safe. This does not authorize a
  new crash-recovery subsystem.
- Initial Provider containment tracks requests to confirmed completion within the
  shared deadline; a new remote-cancellation API is not a prerequisite. Timeout,
  client disconnection, or worker termination leaves remote activity unresolved
  unless authoritative Provider evidence establishes termination.
- Acceptance requires fast regression tests and disposable-VM fault-injection
  tests for healthy teardown, partial deployment, graceful interruption, repeated
  stop, stale identities, failed inspection, budget exhaustion where enforceable,
  preservation of unrelated resources, and unresolved Provider requests. Mocks
  alone do not prove real resource absence. Owner-crash recovery testing is not
  an acceptance claim for this delivery.

## Scope revision: supervision deferred, ownership tagging rejected

The user deferred supervised experiment workers and per-run cgroup v2 containment
to future investigation in [issue #131](https://github.com/santiagopedro1/mininet-ai/issues/131).
They also rejected the proposed new run-generation ownership markers on OVS
resources and Linux link aliases for this redesign. Neither proposal is approved
for current implementation, and this redesign does not change ADR-0007's
in-orchestrator trusted-code execution model.

These exclusions require revisiting earlier ambitions: current mechanisms do not
establish a hard end-to-end deadline, descendant-complete process containment, or
safe network attribution from planned names alone after arbitrary replacement.
Do not silently claim these guarantees, label missing evidence as verification,
or assume that deferring the mechanisms authorizes weakening the documented
outcome and repeated-trial contracts. Durable records and existing process identity
checks remain distinct from the rejected resource-tagging proposal.

## Diagnosis motivating the decision

The shell checker is used by VM verification and manual recovery, not ordinary
experiment teardown. Its exact whole-host baseline comparison can fail on changes
unrelated to experiment ownership. Isolated probes demonstrated sensitivity to
qdisc reference counts and unrelated temporary log files; they did not establish
the cause of any particular live run's failure. Real `mn -c` also deletes broad
temporary-log patterns, so simulated post-recovery log additions do not establish
that those additions survive real recovery. Deleting a baseline-listed log can
likewise produce a mismatch.

Separately, the legacy runtime reports cleanup as unknown even after its teardown
procedure succeeds. The experiment lifecycle then reports
`experiment.cleanup.unverified` and a failed overall outcome. Existing outcome
tests confirm this behavior; it does not prove that resources actually leaked.
Replacing unknown with verified without inspection would conceal the evidence gap.

## Implementation and validation

The scoped verification contract must be reflected explicitly in result/evidence
reporting and compatibility documentation, not silently substituted for a broader
claim. It does not satisfy or relax the stronger repeated-trial containment
contract in [ADR-0004](0004-repeated-failure-trial-lifecycle.md); unresolved
descendant or Provider containment must still prevent starting the next trial.

Implement owner-led teardown and scoped verification, update its callers and
tests, and retire the script/baseline workflow. Existing work failures must not
be erased by later successful cleanup, and repeated stops must retain valid
verification receipts rather than manufacture success from missing state.
Concrete mechanisms must be validated through fast regression tests and real
disposable-VM checks before claiming completion. The user has confirmed this
design. This ADR is not an implemented guarantee; implementation authorization
has not yet been given.
