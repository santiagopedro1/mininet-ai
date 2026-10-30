# Missing-flow prompt tuning (2026-10-30)

## Verdict: both candidates rejected; original wording restored

The user authorized revising the diagnosis instructions and evaluating a newly
declared configuration after the [remote missing-flow failure](bounded-decision-remote-native-evaluation.md).
Only scenario instructions and the two choice descriptions were changed.
Input shape, model identity, observation contents, oracle, cadence, deadline,
execution path and empty proposal lists remained unchanged. No computed health
hint, oracle label, deterministic override or confidence threshold was supplied
to production inference.

Two candidates were evaluated on `http://10.10.10.152:9931` using the real
four-agent `ExperimentRuntime`, native `/v1/systemone` client and constructed
Substrate observations. This is iterative tuning against an inspected failure,
not held-out evaluation. Each case starts a fresh runtime, but server cache
state persists. Startup interface probes precede decisions; cold latency is
unmeasured. Remote hardware/backend/full launch and artifact hash verification
remain unavailable/unverified as in the earlier remote evaluation.

1. **Flow-first:** explicitly compare the complete expected inventory, include
   drop/probe rules, reject too few records, and reject matching-count inventories
   missing a required record; check current ports afterward.
2. **Balanced:** require independent PORTS PASS AND FLOWS PASS; explicitly state
   either failing check requires degraded and cannot be excused by the other.
   This was a separately declared candidate after rejecting the first, not a
   replay of the failed configuration.

| Candidate | Healthy | Original missing s2 flow | s1-s2 port fault | Overall |
| --- | --- | --- | --- | --- |
| Flow-first | 10/10 | 10/10 | 9/10 | **29/30; failed** |
| Balanced | 10/10 | 6/6 | 7/10 | **23/26; failed** |

Both candidates corrected the original missing-flow choices but introduced
incorrect healthy diagnoses for s2 with `s2-eth1` explicitly down. The retained
input was inspected to confirm the down state was actually present. The model's
wrong choice was retained as a successful native invocation and failed evaluator
correctness, not rewritten to degraded or disguised as a transport failure.

Every run stopped at its first failed case, without automatic retries. The
declared later cases (s2-s4 fault, missing flows on other switches, missing first
rule, equal-count incorrect inventory, wrong action/cookie, reordered healthy
flows, and unavailable observations) were **not attempted** after that failure.
Their offline coverage is not a native-model pass.

All 56 invocations completed with zero output tokens and no timeouts. Recorded
inference-phase latencies were:

| Candidate | n | Min / max seconds | Mean seconds |
| --- | --- | --- | --- |
| Flow-first | 30 | 0.2432 / 2.0558 | 0.7820 |
| Balanced | 26 | 0.3527 / 2.0112 | 0.7994 |

These are inference phases, not full lifecycle or recovery measurements. The
one-second cadence and five-second operational deadline were not relaxed. All
slots were observed idle before/after each attempted case. Fake cleanup proves
only the in-memory model is empty, not real-network or host cleanup.

## Retained evidence

`evidence/diagnosis-prompt-tuning-20261030.tar.gz` contains separate `flow-first/`
and `balanced/` directories, each with its exact compiled experiment, immutable
plans, reproducible evaluation driver, declared full matrix, native
requests/responses, all labels and failures, validated results, SQLite ledgers,
runtime reports, and timed pre/post slot observations.

- SHA-256: `93e7ff1b99abf55deef2a82964b360e248e37f944d089ab82e83fd1653a42c87`
- All **56 execution records** validate against the strict published schema.
- Source directories: `/tmp/opencode/diagnosis-flow-first-20261030T155458Z` and
  `/tmp/opencode/diagnosis-balanced-20261030T155550Z`.
- The original failed remote archive remains separate and unchanged.

The candidate descriptions and exact requests are preserved in the archive,
not promoted into the Maintained Example. The source YAML has been restored to
its original wording. No accepted prompt improvement or model-support claim is
made.

## Regression coverage and next decision

The continuous-runtime offline test now covers missing first/last flow,
equal-count duplicate inventories, incorrect cookies/actions, and reordered
healthy flows. It checks that scenario instructions/descriptions reach the
native question unchanged and that labels stay evaluator-side. A forced-healthy
scripted transport confirms wrong answers fail evaluation while the actual
native choice remains healthy and runtime operation remains successful.
These tests establish mechanics, not correctness of either failed prompt.

Focused diagnosis/example/cleanup/guard/evidence regressions: **88 passed**.
Ruff lint, repository formatting and Pyright pass. The full regression refresh
finished with **137 failed, 1045 passed, 3 skipped, 173 subtests passed**.
The earlier merged CLI/results failure set remains an independent completion
blocker; no clean full-suite pass is claimed. The 13 additional passing cases
come from expanding the diagnosis runtime test from five to eighteen cases.

#124 stays open; #125's real-network matrix remains blocked. The next step
requires an explicit decision to evaluate another native model or to undertake
a separately reviewed request-design change. More deadline does not repair the
observed wrong choices. Do not silently substitute a model, discard failed
cases or replace model diagnosis with the oracle.
