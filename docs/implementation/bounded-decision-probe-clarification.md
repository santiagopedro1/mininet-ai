# Mandatory probe-rule clarification (2026-10-30)

> **Subsequent state experiment:** The user approved returning to the original
> prompt and enriching only state with versioned static local roles. That is
> now the example's explicit opt-in; it corrected the original s2 case but missed
> another probe on s1 (19/20 correct). See the
> [rich-state evaluation](bounded-decision-rich-state-evaluation.md). The prompt
> candidates below remain preserved historical failures, not accepted fixes.

## Change and scope

The user confirmed that a missing required diagnostic-probe rule must count as
degraded even when active traffic could still pass, and requested clearer
wording. The Maintained Example retains its original instructions and adds:

> Expected entries include mandatory diagnostic-probe and table-miss drop rules,
> not just active-traffic forwarding rules. A missing expected probe rule is
> degraded even if active traffic could still pass and all ports are up; this
> diagnosis checks required configuration completeness, not just connectivity.

Both healthy/degraded choice descriptions also explicitly include required
probe/drop rules. This clarifies the existing complete-inventory requirement;
it does not redefine degraded as connectivity loss, change the expected rules,
or remove down/unknown/missing ports and unavailable evidence from the policy.

Model identity, input shape, Substrate fixtures, evaluator, choices/proposals,
one-second cadence and five-second operational deadline remain unchanged. No
per-invocation hint, selected label, confidence gate or production oracle is
introduced. The existing user edit to `models.py` is untouched.

## Native evaluation: clarification retained, correctness still fails

Two separately declared H2O prompt configurations were evaluated, after checking
the same remote model and idle slots. Each matrix stopped at its first failed
case with no retries. This is tuning after inspected failures, not held-out
validation or a support claim.

| Candidate | Healthy | Missing s2 probe flow | Verdict |
| --- | --- | --- | --- |
| Broad configuration-completeness rewrite | 0/4 | Not attempted | Rejected: all healthy switches called degraded |
| Original wording plus narrow clarification | 4/4 | 3/4 | Retained for clarity only; native correctness still fails |

The narrow candidate is the source YAML now; the broad rewrite is preserved
only in evidence. Its failed s2 input is identical to the original H2O failure
after removing invocation identity metadata: both ports up, five expected rules,
four actual rules, missing expected `cookie 0x2b67`. Both active-address
forwarding rules remain present. The expected answer is still degraded. H2O
again selected healthy (probability 0.6565195017; diagnostics are not a gate).

Both matrices declared 17 cases, including healthy, the original missing-probe
fault, both local-link faults, other switches' missing probe flows, missing drop
and active-traffic rules, same-count incorrect inventories, wrong actions/cookies,
reordered healthy inventories and unavailable/unknown/missing inputs. The broad
candidate leaves 16 cases unattempted; the narrow candidate leaves 15 unattempted.
In particular, the new wording's native down-port behavior is **not verified**;
offline down-port coverage must not be promoted to that claim.

All 12 retained invocations completed with zero output tokens and no timeouts.
Broad candidate inference phases: 0.1471–0.7148 seconds, mean 0.4904 (n=4).
Narrow candidate: 0.1459–0.7196 seconds, mean 0.5290 (n=8). These are inference
phases, not complete lifecycle or recovery durations. Startup probes precede
scheduled decisions; cold latency is unmeasured. Four idle 8192-context slots
were checked before/after attempted cases. No real network was deployed; Fake
cleanup verifies only in-memory state. Remote hash/backend/launch gaps remain
as recorded in the [original H2O evaluation](bounded-decision-h2o-evaluation.md).

## Evidence and regression coverage

`evidence/diagnosis-h2o-probe-clarification-20261030.tar.gz` retains both candidates'
exact prompts, declared matrix, reproducible driver, effective configuration,
plans, requests/responses, labels, ledgers, reports, and timed slot observations.

- SHA-256: `19f595a319d3205e7063eaba4493e5890a0d162ef6d020375f1008df11d96d7d`.
- All **12 execution records** validate against the strict published schema.
- Source runs: `/tmp/opencode/diagnosis-h2o-probe-clarified-20261030T173724Z` and
  `/tmp/opencode/diagnosis-h2o-probe-narrow-20261030T173827Z`.

The offline runtime regression now explicitly verifies that the missing-probe
case retains both active-address flows, the absent expected probe remains in
the supplied expectations, and both choice descriptions/instructions name probe
requirements. Missing active-flow coverage is added separately. The evaluator
still labels missing rules independently and never rewrites wrong native choices.
Focused diagnosis/example/cleanup/guard/evidence suite: **90 passed**. These
scripted tests prove transport/context/evaluation mechanics, not model quality.
Full regression: **1180 passed, 3 opt-in skips, 179 subtests passed**.

The prompt clarification is implemented, but #124 remains blocked on correctness
and #125's real-network matrix must not proceed on these failed prerequisites.
