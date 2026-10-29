# Bounded-decision live-network evaluation

## Verdict: PARTIAL PASS / DIAGNOSIS INTEGRATION UNVERIFIED

The real Mininet/OVS topology and both isolated primary-link fault conditions
were exercised successfully. The harness evidence is retained at
`/tmp/diamond-recovery-evidence/harness.jsonl` and
`/tmp/diamond-recovery-evidence/rounds.jsonl`.

This establishes topology, link-state, probe, reset, and cleanup evidence. It
does not yet establish bounded-model diagnosis support because the harness does
not feed its live observations into the decision runtime. No recovery or model
support claim is made.

The initial Maintained Example scope was amended to current-only local switch
diagnosis. Consequently, the historical diamond recovery matrix is not an
acceptance target for this slice. The live harness remains an opt-in topology
and evidence tool; it does not make a model decision, install recovery rules,
or bypass capability authorization.

## Evidence inventory

| Evidence | Result |
| --- | --- |
| Offline contract and planning | Passed; see [offline acceptance](bounded-decision-offline-acceptance.md) |
| Native live-model evaluation | Partial pass, integration unverified; see [live-model report](bounded-decision-live-model-evaluation.md) |
| Compiled runtime against native service | 15 successful invocations using the fake substrate; real telemetry unverified |
| Real Mininet/OVS topology/fault run | Passed for both isolated links; model diagnosis integration not exercised |
| Diamond recovery run | Not applicable to the amended initial scope; historical fixtures retained |
| Trial reset and cleanup verification | Not measured |
| Remote request termination/isolation | Unknown; no live-network trial was started |

## Blocking prerequisites

Before a live evaluation can be accepted, review the live-model report and
establish a usable native service with retained model/server/build/backend and
hardware provenance. A short smoke response or constructed-fixture matrix is
not evidence of live telemetry integration. If any required provenance or
runtime evidence remains unavailable, the result must stay unverified.

The installed Mininet/OVS binaries were detected, but the harness could not be
started by this agent because the session has no passwordless root access; the
operator nevertheless ran it interactively and retained the evidence listed
above.

The observed matrix was:

| Trial | Fault | Primary probes | Recovery probes | Reset/cleanup |
| --- | --- | --- | --- | --- |
| 1 | `s1-s2` down | Three rounds timed out in both directions | Successful in both directions | Verified |
| 2 | `s2-s4` down | Three rounds timed out in both directions | Successful in both directions | Verified |

When the prerequisite is satisfied, use a new configuration/run and retain:

- readiness, actual switch/port mapping, baseline policy, and probe evidence;
- current-only local inputs for each target switch, including explicit missing
  fields and no history, memory, or other-switch state;
- exact native requests/responses, labelled diagnosis outcomes, warm/cold
  latency distributions, sample counts, and all failures;
- independent reset and cleanup outcomes between trials; and
- local cancellation versus independently verified remote termination.

Do not repeat a trial while reset, cleanup, or remote work remains unknown.
The work outcome and cleanup outcome must remain separate in the retained
evidence.

Configuration changes require a new run. No fallback, candidate substitution,
recovery action, or model-support promotion is authorized by this report.
