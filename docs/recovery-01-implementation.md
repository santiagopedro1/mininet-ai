# Ticket 01: one S1-only incident, configuration to evidence

## Scope and status

Implements only `four-treatment-recovery/issues/01-run-s1-only-recovery.md`
against the paper repository's four-treatment `spec.md` (2026-11-06 amendment),
and the resolved mechanism/measurement planning decisions. The source baseline
is [recorded separately](recovery-01-baseline.md).

The public boundary is `CampaignConfig` → `run_campaign` → `evidence.json` and
append-only journals in `examples/noms27/recovery_campaign/`. Known-outcome tests
exercise that boundary; a loopback test additionally runs the real UDP worker
processes and the existing native S1 protocol with controlled model/network
responses. These are **mechanism validation**, not Mininet/model campaign trials.

Live validation is **pending the parent integration session's VM smoke run**.
Initial discovery found uid 1000, password-required host sudo, denied host OVS
socket access, and a powered-off VM. Subsequently the user authorized booting
the VM without provisioning; the parent booted it and confirmed guest Python
3.14.7 at `/home/vagrant/.venvs/mininet-ai/bin/python` and guest root access.
The native endpoint `http://10.10.10.152:9931` refused connections at discovery;
the user intends to start it. The parent owns authorized cleanup of stale guest
bridges. This implementation session neither changed those resources nor ran a
live recovery. No physical-failure results or feasibility-derived budgets exist
from these deterministic checks.

## Run one incident

From the repository root, validate without privileges or model access:

```bash
python -m examples.noms27.recovery_campaign \
  --config examples/noms27/recovery_campaign/development.json --check
```

Copy `development.json` to a new file, set a fresh `trial_id`, the running local
native endpoint and `model_name`. Set `expected_identity` to the provider's
repository/revision/artifact/sha256 mapping when available. Null explicitly skips
immutable identity verification, retaining observed identity and that limitation;
health and native-interface verification still run. The native endpoint must
support `/health`, `/props`, and `/v1/systemone`. There is no chat fallback.

The sample's 10-second deadline/cap and 9.6-Mbit/s capacity are **development
values only**, not approved campaign budgets or validated load settings. The
configuration deliberately accepts only `development_unapproved` and rejects
held-out evaluation. Choose development values for a smoke run; scientific
execution still requires induced-fault feasibility and Pedro's sign-off under
the agreed budget-selection rule, not these constants.

For the booted guest, place an isolated source archive and the edited config in
guest `/tmp/recovery-01-source` and `/tmp/recovery-01-config.json`, respectively.
Do not overlay the original checkout. Then, inside the guest:

```bash
cd /tmp/recovery-01-source
sudo /home/vagrant/.venvs/mininet-ai/bin/python \
  -m examples.noms27.recovery_campaign \
  --config /tmp/recovery-01-config.json \
  --evidence /tmp/recovery-01-evidence-001
```

Use a previously nonexistent evidence directory. Source archives need the
`examples/` and `mininet_ai/` trees and installed project dependencies. Git revision
is null without `.git`; retain the archive's originating commit and checksum
alongside the evidence. Per-source-file hashes and dependency versions remain in
the export. Exit 0 means a **valid trial with verified cleanup**, including valid
unsuccessful recovery; exit 2 means experimental invalidity **or unverified cleanup**.
Cleanup failure does not change trial validity or service outcomes. CLI output
includes validity, outcomes, cleanup and containment separately; inspect all four,
not just exit status. Even exit 0 does not authorize another trial when Provider
containment is unresolved.
No automatic cleanup of preexisting bridges/interfaces is attempted. Resolve
resource-name collisions explicitly in the disposable VM before trying again.

## Network and permissions

- Reuses `DiamondLiveHarness` for owned topology setup, primary forwarding,
  permanent neighbors and persistent `s1-s2` link-down. The campaign adapter calls
  teardown on that harness's owned Mininet network, then independently inspects
  its interfaces and bridges, leaving the legacy harness/example unchanged.
- The designated directional service is h1 → s1 → s2 → s4 → h2 initially;
  the surviving path is h1 → s1 → s3 → s4 → h2. Hosts and attachment switches
  survive. This slice is one structurally locally repairable placement, not all
  scenario classes or a held-out placement allocation.
- Setup configures both directions of every owned TCLink to the declared
  capacity and preinstalls active downstream forwarding on s3/s4. Backup resources
  are dormant until s1 redirects. There is no controller, NORMAL rule, failover
  group, injected-port enable action, automatic repair or fault reversal.
- S1 receives only fresh s1 port descriptions and flow dumps. Detection is explicitly
  harness-triggered local diagnostic collection, not autonomous detection.
  Fault labels, evaluator expected repair and global topology observations are
  never model inputs.
- The frozen catalogue permits exactly one literal `bypass` flow on s1, or
  non-executable `no_action`/`unable`. Confidence is recorded and never gates S1-only.
  The adapter retains invalid/missing confidence, omitting invalid values solely
  for the core native choice mapper; no engine contract changes were made.
- Uses the real `NativeSystemOneClient`/`HttpxJsonTransport`. Warm native startup
  verification occurs before timing, uses a fresh current-only context and is
  retained in `model-startup.json`, `reset.model_startup` and the wire journal.
  There is one incident request, zero retries and at most one repair attempt.

## Workload and independent measurement

Two namespace-local Python worker processes send/receive continuously, independent
of policy progress. A packet carries a sequence number and the sender's
`CLOCK_MONOTONIC` timestamp inside a **1,200-byte UDP payload**. Fixed payload
packet rate is `0.10 × bottleneck_mbps × 1e6 / (1200 × 8)`. UDP/IP/Ethernet
overhead is not counted as payload load. No application return traffic is needed;
ARP is static. Baseline, both loads and actual link shaping still need live
feasibility validation; this slice intentionally rejects the later 70% load.

The receiver acknowledges readiness before sending starts. Five seconds of
healthy baseline traffic precede injection. Both endpoint-down states are
confirmed and sampled approximately every 100ms through observation completion.
These are sampled administrative link-state observations, not continuous proof
of a physical failure. Linux network namespaces share the monotonic clock;
independent clock-accuracy calibration is unavailable, not assumed nanosecond
accuracy. Negative arrival intervals invalidate measurement.

The independent evaluator uses retained **actual send times**, deduplicates
arrivals by sequence, and allows delivery at exactly 100ms. Later arrivals are
failures and remain raw evidence. Send-time bins are aligned to injection start,
100ms wide. Every one of ten consecutive full bins must deliver at least 99%
timely; an average over the second is insufficient. Verification completion is
the qualifying interval's end **plus 100ms**, not its first arrival or action exit.
At the sample's 100 pps, each bin has about ten packets, so 99% means all packets;
this resolution limitation must be checked in feasibility before freezing load.
Only full bins beginning at or after completed link-down confirmation can enter
the restoration streak. Healthy traffic during a slow injection cannot qualify as
restoration. All elapsed times, disruption and the deadline remain anchored to
injection **submission**, not confirmation.

The operational service signal projects those records into one post-action
one-second interval plus delivery allowance (aligned to the next full send bin).
It cannot override the independent final measurement. A command exit zero means
Action Execution Success only. Independent recovery may fail, occur late, or
relapse despite a successful action or operational signal.

Observation always covers injection start through the original deadline + five
seconds, even on inability, malformed response, failed action or early success.
Arrival collection drains for another 100ms before classification; this does not
extend the send observation window or recovery deadline. Exports retain:

- qualifying interval start and verification completion; on-time service outcome,
  late restoration, observed disruption, relapse starts and censoring at the common
  observation limit when recovery is unobserved;
- failed packets / fixed offered pps as lost-packet-equivalent disruption seconds;
- raw sends/arrivals, bin loss timeline and longest timely-arrival gap, including
  leading/trailing boundaries of the observation window.

The sender never creates catch-up bursts. It logs missed scheduled slots instead.
Missing slots, duplicate sends, an incomplete worker record/terminal summary,
collector failure, or offered counts outside the frozen per-bin tolerance
(`max(1 packet, 5%)`) are experimental measurement failures, not network packet
loss. Any missed sender slot is invalid, even if bin counts fit that tolerance.
Send sequences must be continuous from zero; the single final sender summary's
next sequence/missed-slot counts must reconcile with retained send records. The
single final receiver summary must reconcile with all arrival records (duplicates
included). Summaries remain in packet evidence. Missing records within the load
tolerance still invalidate the trial; observed-only disruption from an invalid
trial is not a complete campaign estimate, and missing observations are not
invented as packets or concealed as a lower offered load.
Malformed essential packets, unhealthy baseline, bad reset/injection, missing
persistence checks or a restored failed link also invalidate the trial. Raw
records are retained. Model malformation, inability, failed repair and exhausted
budgets remain valid treatment outcomes. Cleanup verification is separate from
service and validity, and must be inspected before another run.

`cleanup_budget_seconds` defaults to 30 and must be positive. Worker stop/drain,
owned-network teardown and independent inspection share one best-effort budget;
each enforceable external check receives only the remaining time. Successful
`ip -j link show` and OVS bridge inventories are filtered to the owned resource
names. Nonzero exits, malformed inventories, timeouts and budget exhaustion remain
unverified, with diagnostics retained, not evidence of absence. No global deletion
or VM recovery is attempted. Synchronous Mininet teardown may exceed the budget;
the cleanup receipt explicitly does **not** promise a hard return deadline or that
budget exhaustion stopped Python teardown. These are scoped checks under trusted
ownership assumptions, not whole-host or arbitrary-descendant containment proof.

## Timing, cancellation and evidence

Injection submission starts the common deadline. Injection, diagnostic collection,
native inference, local execution and verification share it. The S1 cap starts
at the same origin, cannot exceed the common deadline, and never grants extra
time. Admission closes before starting a new phase once its budget is gone.
Expiry requests cancellation, revokes further action admission, terminates a
running action subprocess and records late completions. Partial action effects
are possible; command cancellation is not rollback.

Closing the local native HTTP client **does not prove remote generation stopped**.
After timeout, `containment.next_trial_allowed` is false. Do not run another
trial until the operator independently establishes provider containment; this is
a single-incident runner, not a safe matched-block scheduler. Blocking operations
retain terminal results where available. Outstanding tasks and unknown remote
termination remain explicit. Late terminal evidence can append to journals after
the immutable export snapshot without revising its outcome; artifact sizes/hashes
are explicitly snapshots. Do not infer a terminal response from local cancellation.
`containment.next_trial_allowed` additionally requires a positive cleanup receipt,
completed startup, retained terminal responses for incident Provider calls, and no
outstanding local tasks. Failed requests without terminal response evidence remain
uncontained even when no policy timeout was reported. This is a conservative
single-incident admission signal, not implementation of repeated-trial scheduling.

A late action terminal result known before export updates `actions[].result`, its
completion/late fields and `outcome.action_execution_success`. It does not reopen
verification or revise the already-fixed timeout/policy result. Results arriving
after the export snapshot remain journal-only as above.

`evidence.json` retains independent validity, action success, service recovery,
failure, allocated-budget timeout and policy termination separately, plus:

- synchronized injection, confirmation, detection, inference, action, verification,
  cancellation, observation and cleanup events with trial identity;
- requests/responses, including malformed native output where transport evidence
  exists, per-call latency and input/output tokens separately; reasoning tokens
  are null unless separately supported, never double-counted into output;
- incident token totals (including late terminal responses available at export),
  zero retries, warm-up evidence outside incident timing, telemetry/action JSON
  message records and bytes at the runner-adapter boundary; handoff is exactly zero;
- reset/cleanup checks, frozen config, prompt/criteria/catalogue/hash, source hashes,
  git revision/dirty state where available, dependencies, seed, execution order,
  model identity verification status and network manifest.

Wire-level operational byte counts, reasoning-token usage, clock accuracy, energy,
and unavailable immutable model identity/dependency metadata are explicitly null
with limitations, not invented zeros. JSON event byte counts are **not wire traffic**;
tokens are not inference latency, runtime or energy. `model-wire.jsonl` retains
native requests, responses and available transport-error bodies. Sender/receiver
stderr and raw JSONL files remain alongside the aggregate export. No ping result
is used as primary service evidence.

## Remaining gates and excluded scope

Live Mininet/native smoke validation is pending, followed by healthy-load validation,
induced-fault feasibility (at least 30 attempts per applicable repairable
treatment/class), sufficient successful observations, numerical-budget/resource
approval and frozen evaluation configuration. Passing tests is not scientific
acceptance. Higher-load, other placements/topologies, S2, combined policies,
calibration, matched-block replacements/bootstrap analysis, and the full campaign
allocation belong to later tickets. Original checkouts and paper tracker were not
modified. The older examples remain intact; their reversible administrative-port
faults and ping criteria are not evidence for this campaign contract.

## Initial integration checks (`57eb2a4`)

- Targeted configuration/evidence tests: **33 passed**, including real loopback
  UDP worker processes with controlled network/model responses.
- Final full suite: **1,347 passed, 3 skipped, 179 subtests passed**.
- Repository-wide Ruff lint: all checks passed; added-file formatting check clean.
- Full Pyright: zero errors/warnings; explicit campaign/test Pyright also clean.
- Repository-wide formatter check identifies a preexisting formatting difference
  in `tests/examples/test_noms27.py:471`; that preserved baseline file was not
  rewritten. The added campaign and tests are formatted.
- No live Mininet/native recovery result is asserted by these checks.

## Ticket 01 review corrections

The isolated `fix/recovery-01-review` follow-up addresses all four documented
standards findings and three specification findings. Regression tests were run red
then green through configuration → runner/CLI → exported evidence, including:

- unverified/unknown/exceptional cleanup leaves validity unchanged but returns a
  nonzero CLI result and blocks another trial;
- successful absence inspection versus inspection failure, malformed inventory,
  remaining resources, a shared configured cleanup budget, bounded external checks,
  and synchronous teardown overrunning that best-effort budget;
- missing Provider terminal evidence and incomplete startup blocking containment;
- slow injection with healthy pre-fault traffic, with and without later restoration;
- missing send records even inside per-bin load tolerance, summary reconciliation,
  retained raw worker evidence, and known late action success/failure updating only
  execution evidence rather than deadline/policy outcomes.

The token decoder is shared between usable and unusable native output, preserving
snake/camel-case aliases, explicit unavailable counts, and rejection of boolean or
negative counts. Bin accounting now uses named sent/timely counts.

Follow-up checks: **67 targeted tests passed**; full suite **1,381 passed, 3 skipped,
179 subtests passed** (existing specification-deprecation warnings retained).
Repository-wide Ruff lint and full Pyright passed; explicitly scoped campaign/test
Pyright passed with zero errors or warnings. Added-file formatting and whitespace
checks are clean. Live VM/model
validation and independent re-review remain parent-session gates; these changes do
not approve campaign budgets or implement later tickets.
