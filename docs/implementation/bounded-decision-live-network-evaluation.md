# Bounded-decision live-network evaluation

## Verdict: UNVERIFIED / BLOCKED

The live-network evaluation was not run. Issue #124's candidate model/server
evaluation remains unverified, so starting network trials would violate the
prerequisite gate and would not establish support. No live recovery, diagnosis
support, cleanup-isolation, or remote-termination claim is made.

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
| Real Mininet/OVS diagnosis run | Not run |
| Diamond recovery run | Not applicable to the amended initial scope; historical fixtures retained |
| Trial reset and cleanup verification | Not measured |
| Remote request termination/isolation | Unknown; no live-network trial was started |

## Blocking prerequisites

Before a live evaluation can be accepted, review the live-model report and
establish a usable native service with retained model/server/build/backend and
hardware provenance. A short smoke response or constructed-fixture matrix is
not evidence of live telemetry integration. If any required provenance or
runtime evidence remains unavailable, the result must stay unverified.

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
