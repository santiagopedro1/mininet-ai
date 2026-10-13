# Repeated link-failure trial lifecycle

Repeat a fixed seeded scenario with fresh deployments and agent sessions, counting
unsuccessful attempts rather than retrying until success. Separate bounded response
measurement from bounded cleanup: a trial outcome cannot be revised by late work,
and another trial may start only after the preceding work is contained.

Decision discussion: [#33 — Define repeated failure trials and their lifecycle](https://github.com/santiagopedro1/mininet-ai/issues/33), part of
[#29 — Plan the link-failure latency experiment](https://github.com/santiagopedro1/mininet-ai/issues/29).
The user confirmed this planning contract. It does not implement a fault scheduler
or describe existing runtime behavior. It builds on the
[recovery criterion](0001-link-failure-connectivity-recovery.md),
[timing boundaries](0002-link-failure-latency-boundaries.md), and
[seeded scenario](0003-seeded-topology-and-failure-scenario.md).

## Run configuration and fault selection

- Keep the generated topology and host placement fixed within one run. Replay
  that scenario across attempts; multiple topology seeds are separate runs, not
  implicit variation between repetitions.
- Default to ten measured trial attempts, with a configurable positive count.
  Unsuccessful and invalid attempts count toward the requested number, including
  setup failure, injection failure, no observed disruption, and timeout. Do not
  automatically replace them or retry until a target number of successes.
- Default to one warm-up trial, configurable to zero or another nonnegative count.
  Record warm-up attempts separately and exclude them from measured summaries.
  They exercise the same baseline, injection, response, and cleanup workflow.
- An enabled warm-up must complete baseline, observed disruption, recovery, and
  cleanup successfully. A failed warm-up aborts the run before measured attempts;
  retain its record and do not silently claim warmed measurements or retry it.
- Traverse a seeded shuffle of the stable eligible-link set without replacement.
  After exhausting the set, reshuffle for the next pass. A failed measured attempt
  consumes its assigned selection position.
- Warm-up uses a separate failure-selection stream. Changing its count must not
  change measured topology, host placement, or measured failure order.
- Preserve requested and actually started/completed attempt counts. If the run
  aborts early, report fewer attempts; do not fabricate records for unstarted trials.

## Isolation and setup

- Each attempt, including warm-up, uses a fresh network deployment, fresh agent
  sessions, and fresh trial-local state. Re-enabling the failed link and reinstalling
  baseline rules in a reused network is not sufficient isolation.
- The harness installs the complete healthy forwarding policy and establishes the
  three consecutive all-success baseline rounds for both designated directions.
  Arm the observation paths before injection.
- Fresh sessions do not imply cold model loads. Model-service caches may remain
  warm, but outstanding requests from earlier trials may not carry into the next.
- Setup/baseline failure ends the attempt without injection. Cleanup must still
  establish that any partially deployed resources are gone, including when
  deployment failed before a usable trial handle was returned.

## Stage budgets and injection

All limits below are configurable and recorded; they are separate budgets rather
than an additive definition of measured recovery latency.

| Stage | Default limit | Boundary |
| --- | --- | --- |
| Setup/baseline | 120 seconds | Deployment, healthy-policy installation, baseline confirmation, and observation readiness |
| Injection confirmation | 10 seconds | Injection command submission through confirmed link-down and observed disruption |
| Response | 120 seconds | Confirmed link-down through complete recovery confirmation |
| Stop/drain/cleanup | 30 seconds | Stopping pending work, containing in-flight work, and verifying teardown after the attempt ends |

- Start the injection window at command submission. Start the response clock as
  soon as link-down is confirmed, not after disruption probing completes. These
  windows may overlap; injection-command duration remains separately identifiable.
- Require at least one failed designated probe within the injection window. If
  link-down is not confirmed, report injection failure. If link-down is confirmed
  but no failed probe appears within that window, report no observed disruption,
  not Connectivity Recovery.
- Agents may respond immediately upon their own relevant failure recognition.
  Do not hold their actions until the harness observes disruption. If restoration
  is too fast for a failed probe to be observed, the same no-observed-disruption
  outcome applies; do not manufacture a delay to obtain disruption evidence.

## Response, outcomes, and admission closure

- Each agent has at most one failure-response reasoning invocation and one
  execution pass through its supplied recovery policy per trial. Disable automatic
  reasoning and action retries. A policy pass can contain multiple rule actions.
- Keep missed detection, failed reasoning, absent actions, and failed actions
  visible. One agent's failure does not terminate the attempt while other agents
  may still restore connectivity; wait for recovery, invalidation, or a stage limit.
- Recovery requires prior observed disruption and three consecutive all-success
  probe rounds for the fixed designated traffic while the failed link stays down.
  The entire third round and its link-down confirmation must finish before the
  response deadline. A probe begun before but completed after the deadline is
  late evidence, not timely recovery.
- Partial restoration and incomplete confirmation at the response deadline remain
  unsuccessful. Preserve observations and missing/incomplete phase measurements
  as specified by the timing contract; never replace them with zeros.
- An unhealthy baseline or restoration of the failed link invalidates the attempt.
  Errors preventing reliable baseline, fault-state, or recovery observation also
  invalidate it. A valid unreachable probe is negative evidence; a broken observer
  is missing evidence, not ordinary packet loss.
- Confirmed recovery, timeout, or invalidation closes admission for new invocations
  and actions. Setup/injection failure and no observed disruption likewise end
  the attempt and enter stopping/cleanup. Stop pending work and observations, then
  drain or cancel in-flight work.
- Freeze the trial outcome when it ends. Retain late Provider, action, and probe
  completions separately; cleanup or late recovery cannot revise a timed-out
  outcome. Keep trial outcome and cleanup outcome separate.

## Cleanup, abort, and the next attempt

- Complete verified teardown before another attempt starts. Establish that the
  old network is gone, local workers have stopped, and prior Provider requests
  have finished or are confirmed terminated.
- Client cancellation alone does not prove remote computation stopped. Allowed
  persistent model caches are distinct from forbidden outstanding requests.
- Abort the run if containment and cleanup cannot be established within the
  cleanup budget, including uncertain rollback after failed deployment. Record
  incomplete cleanup and the abort rather than claim isolation or launch another
  attempt. Continue after an unsuccessful measured attempt only when cleanup is
  verified; warm-up failure still aborts measured execution.

## Implementation gap and remaining decisions

The existing `examples/link-failure-latency/run.py` deploys fresh networks and
factories per repetition but has no warm-up or varied eligible-fault sequence.
Its deadline begins before injection, agent workers start after disruption checks,
and cooperative stopping can leave in-flight calls running while the executor
drains. Client cancellation does not establish remote Provider quiescence, and
failed-deployment rollback is not explicitly checked by that harness. These
behaviors do not satisfy this new contract; no runtime changes accompany it.

Clock sources, correlation, instrumentation, and evidence for Provider containment
remain implementation-contract work for
[#34 — Choose clock, correlation, and instrumentation contracts](https://github.com/santiagopedro1/mininet-ai/issues/34).
Machine-readable record/status encoding, warm-up separation, incomplete-run
representation, and summary populations remain for
[#35 — Define raw latency records and trustworthy summaries](https://github.com/santiagopedro1/mininet-ai/issues/35).
