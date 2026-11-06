# Ticket 06: offline feasibility planning and reporting

## Authority and boundary

Offline slice only; scientific approver **Pedro**. Branch
`feat/recovery-06-feasibility` began clean at the committed integration HEAD
`b56af8f2686093dbca7ea63e345e9c9cb3a819ea`. Read `AGENTS.md`, `GLOSSARY.md`,
ADR 0013's campaign amendment, the authoritative ticket/spec/offline scope, and
the retained 03/05 implementation/validation context. Parent owns tracker changes.

The approved seam is declared campaign configuration → `run_campaign` / retained
Run Evidence → report. `feasibility.py` composes the existing manifest, structural
repairability and packet evaluator; it neither replaces the runner nor constructs
a VM, socket, model client or provider. Inputs are read only; CLI output uses
exclusive creation. No approval, freeze, live override or execution switch exists.

## Plan and resource accounting

All six classes × four treatment slots × both loads are planned: **48 cells**.
Structural witnesses, not observed outcomes, identify **28 repairable cells**,
each requiring at least **30 valid induced-fault attempts** (840). The remaining
20 cells separate no-surviving-path and structurally insufficient S1 expectations.
Their default 30 attempts/cell is an **unapproved resource-planning assumption**,
not a new scientific floor. Twelve separate fault-free class/load checks do not
count as induced-fault repair timing; live workload validation remains pending.

The manifest's 1,440 primary + 1,080 higher-load floor is preserved. Optional
replication is additive. Calibration accounts separately for gate-disabled,
11 thresholds (0.0…1.0), and a matched ungated comparator. With six calibration
placements, 30 repetitions and the explicitly assumed primary calibration load:
180 gate-disabled + 1,980 numeric-threshold + 180 comparator = **2,340 trials**.
Comparator reuse removes only the additional 180 trials and requires an explicit
matching/reuse justification; this is not approval of that experimental design.
Choosing both calibration loads doubles calibration allocation. Feasibility,
baseline checks and replacements are additional; no gate-disabled outcome removes
a treatment slot. The default known total excluding replacements is **6,312**
under these assumptions, not an approved final campaign size.

Replacement trial allowance defaults to unknown, not zero. Separate local-model
identity, input/output/reasoning tokens, inference seconds, total serial runtime
and runtime assumptions must be supplied for a complete arithmetic estimate.
Reasoning is separately reported, never added to output tokens. Runtime includes
inference; the two totals must not be added. Planning inputs are explicitly not
measured model resources. CPU, RSS/memory, GPU and energy are unmeasured. Include
model startup/warmup, reset, full observation and cleanup overhead in the declared
runtime assumption; no inference-to-runtime or token-to-time conversion is made.

## Artifact reporting and numerical illustrations

Each input retains its path, content hash, ID, validity/errors, service outcome,
censoring and missing provenance. Failed and invalid originals remain observable.
Duplicate trial IDs/path aliases cannot fill replication counts. Missing files,
malformed/ambiguous JSON, nonfinite/boolean/string numeric inputs, inconsistent
configuration/scenario/schema/outcomes and packet-report mismatches fail closed.
The existing `measure()` recomputes send-time bins, delivery deadlines, sustained
verification and observation from retained packet records; this is reuse of the
existing evaluator, not a claim those checks were previously absent. Clock
anchors use injection submission's retained deadline and `fault_confirmed`'s
`confirmed_ns`, not slightly later journal emission ticks.

Only explicitly synthetic mechanism artifacts can contribute to this offline
report's **mechanism-fixture** timing illustration. Calibration or held-out/live
evidence is not repurposed as feasibility evidence. Calibration-placement
mechanism rehearsals remain synthetic fixtures, not calibration observations.
Actual six-class live feasibility is unsupported by the retained runner and is
not enabled here. There is no scientific-budget output: `scientific_budget=null`,
`numerical_signoff_ready=false`, `execution_authorized=false` unconditionally.

Successful timing uses nearest-rank p95 (`ceil(.95*n)`). Twice the largest
successful local verification-completion p95 across S1-only and locally verified
combined slots provides a conservative S1 repair cap;
twice the largest repairable treatment end-to-end p95 spans classes and loads.
The illustrated deadline is the maximum of that doubled p95 and
`S1 cap + explicit handoff allowance + verification allowance` (at least 1.1 s).
This deliberately conservative verification-inclusive timing and all assumptions need review
before any scientific use. Nonrepairable cells never contribute budget timing.

The spec gives **no successful-observation minimum**. It defaults to null;
supplying one is explicitly an unapproved assumption pending Pedro's agreement,
not an implicit equation with 30 attempts. Per-cell insufficient successes/valid
attempts block coverage. Partial illustrative numbers are labeled partial and
never sign-off ready, even if synthetic coverage is complete. Missing provenance,
validation/workload freeze, live integration, handoff/resource inputs and Pedro's
approval remain blockers. No confidence policy is selected here; the rehearsal's
gate setting/threshold is an unapproved mechanism setting, never derived from
calibration or held-out outcomes.

## CLI

Use an existing local environment; no dependencies need installing:

```bash
python -m examples.noms27.recovery_campaign.feasibility \
  --config examples/noms27/recovery_campaign/offline-feasibility.json \
  --output run-evidence/recovery-06-plan.json
```

An optional `--manifest` uses the existing validated campaign manifest. Input
artifact paths in `evidence_paths` resolve relative to the JSON config. Re-running
with the same output path fails instead of overwriting evidence. Unknown config
keys (including approvals/overrides) are rejected. Replace nulls only with explicit
planning assumptions; neither configuration nor a report is a scientific freeze.

## Outstanding acceptance

Ticket 03 containment/live prerequisite, 04/05 live integration, real UDP offered
load and clock calibration, all-class induced-fault feasibility, production model
identity/usage, termination evidence, approved resources/numerical budgets,
calibration/freeze and Pedro's held-out authorization are untouched. No live or
held-out experiments, provider calls, model-machine access, VM/network setup,
dependency downloads, push, PR or manuscript edits were performed.
