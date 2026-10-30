# Bounded-decision final review (2026-10-29)

> **2026-10-30 refresh:** All committed integration work is on `main` through
> merged PR #128 (`a0f5224`); PR #126 is closed without merging. The later
> archived real-OVS healthy trial failed (4 correct diagnoses, 2 timeouts,
> cleanup unknown). Independent cleanup verification was added afterward and
> requires live validation. The findings/checks below remain the historical
> review record, not a new support pass. See the
> [acceptance handoff](bounded-decision-acceptance-handoff.md).

Baseline: `git diff main...dfe26ff`, merge-base `67ae633`. Standards and spec
reviews ran independently. #116's current-only diagnosis and per-scenario
deadline amendments supersede the original recovery/history/pinned/800 ms scope.

## Resolution status

The findings below are the original review record, not the current open-code
checklist. Follow-up fixes now provide:

- original-version checks for every explicit blueprint execution selection and
  experiment decision field, including empty historical fields;
- field-specific proposal validation and one shared HTTP response decoder;
- `ovs.port-state` with explicit up/down/unknown values and current timestamps;
- compiled, inspectable `localExpectations`, filtered to the invocation's one
  switch; expected port and exact flow completeness are checked by the
  evaluator, never used as a production inference gate;
- a real `MininetOVSRuntime` / four-agent `ExperimentRuntime` live runner, with
  healthy, both isolated link faults, missing-flow, and unavailable-input trials;
- normalized invocation JSONL, compiled plan, SQLite ledger, independent work
  and cleanup status, and evaluator-side labels for every trial;
- scenario-derived startup inference budgets, distinct startup timeout errors,
  retained invalid native responses and bounded raw HTTP error diagnostics;
- nested evidence-schema requirements and execution-kind-aware validation,
  with valid/invalid fixtures and tests of actual serialized runtime records;
- clean Ruff lint, repository formatting, and Pyright, without disabling checks.

Independent follow-up review at `a2e2e44` confirmed the scoped standards and
spec findings resolved, with no additional actionable regression confirmed.
Targeted reviewer validation passed 43 tests. Final full regression: 1049
passed, 3 opt-in checks skipped, 179 subtests passed; lint, formatting and
Pyright pass. An initial follow-up standards concern about nonempty v1alpha3
normalization was corrected: the reproduced defect was admission of explicitly
empty new experiment fields, now rejected before normalization.
The six normalized execution records in the retained native-failure archive
also validate against the strict published evidence schemas.

The current-only continuous-runtime regression substitutes only native transport
and the Substrate. It covers healthy, down, missing/wrong flow, and unavailable
inputs; checks per-target isolation, no history, no proposals, coordination,
deadlines, retained evidence, and cleanup. The three initially failing groups
were reproduced before their fixes (17 failures); follow-up evidence/startup
and historical-field failures were also reproduced before fixes.

**Remaining gates:** the new four-agent native check with constructed healthy
inputs failed its declared workload (two correct choices, four timeouts on the
one-slot service). No inference was retried; an independent later `/slots`
observation showed idle after local stop had initially left remote work active.
See the live-model report. After service/deployment review, rerun the new
deployed-runtime matrix on a root-capable host. `sudo -n true` requires a
password in this session; no privileged network trial was sent. Existing 16/16 measurements
remain historical direct-client evidence, not evidence for the replacement
runner. #125 remains open until this gate is resolved; PR #126 was later closed.

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

Original review summary: Standards had one documented violation and two
heuristic findings; Spec had three findings. Their code fixes are implemented;
the replacement live evaluation still needs privileged execution. No
merge-readiness or live-support claim.
