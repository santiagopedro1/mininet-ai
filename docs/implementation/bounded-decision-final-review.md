# Bounded-decision final review (2026-10-29)

Baseline: `git diff main...dfe26ff`, merge-base `67ae633`. Standards and spec
reviews ran independently. #116's current-only diagnosis and per-scenario
deadline amendments supersede the original recovery/history/pinned/800 ms scope.

## Standards

- **Documented contract violation:** `specification/models.py` gates execution
  selection on v1alpha5 only for bounded mode. Historical blueprints also accept
  explicit `execution: {kind: agno}`, contrary to `docs/compatibility.md`'s rule
  that new fields are not admitted under old document versions. Reject explicit
  selection under historical versions and add both-kind regression tests.
- **Possible Primitive Obsession (judgment call):** `DecisionChoice.proposals`
  validates dictionary keys/arguments but not capability/target identifier types.
  Compiler lookups assume hashable string identifiers; malformed values can
  raise raw `TypeError` instead of a specification diagnostic. Validate these
  identifiers at the document boundary.
- **Possible Duplicated Code (judgment call):** HTTP GET/POST repeat bounded-body
  reading, error translation, and strict JSON decoding. Consolidation is optional;
  this heuristic alone is not a merge blocker.

## Spec

- **Missing operational state:** the example binds `portState` to
  `ovs.port-counters`, but the real collector returns counters, not up/down
  state. The amendment requires “current port/link states.” Add a supported
  operational-state input and continuous-runtime link-down regression.
- **Live runtime acceptance gap:** `live_diagnosis.py` manually builds contexts
  and calls the native client sequentially; it does not instantiate the four
  deployed agents or traverse scheduling, coordination, deadlines, and normalized
  Run Evidence. The spec requires “Instantiate one bounded System 1 agent per
  switch.” The 16/16 result is direct-client telemetry evaluation, not acceptance
  of deployed agents. Run the real continuous runtime with live telemetry.
- **Incomplete correctness oracle:** instructions require missing expected flows
  to imply degradation, but no inspectable expected-flow set is supplied and
  live labels check only ports. Define local forwarding expectations and add
  missing-flow labelled trials, retaining current-only local inputs.

Summary: Standards has one documented violation and two heuristic findings;
Spec has three findings, with missing operational state and bypassed live
runtime acceptance the highest-priority gaps. No merge-readiness claim.
