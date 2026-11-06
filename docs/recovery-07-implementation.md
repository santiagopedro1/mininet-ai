# Ticket 07: authorized offline implementation

## Scope and boundary

Implemented in `examples/noms27/recovery_campaign/calibration.py` at the existing
campaign configuration → retained evidence → report boundary. This is an offline
artifact processor, **not another policy runner**. It imports the existing
`Manifest`, allocation, scenario definitions and validity rules; it never invokes
the runtime, providers, VM, sockets or held-out trials. The scientific gates in
tickets 03–07 are not satisfied by this work.

The worktree is `/tmp/opencode/recovery-07-implement`, branch
`feat/recovery-07-offline`, initially clean and descending from integration/base
`315035e`. Paper scope/spec/tickets were read without editing the paper tracker.

## Public workflow

Using the already installed environment (no dependency installation):

```bash
python -m examples.noms27.recovery_campaign.calibration preregister \
  --output registration.json
python -m examples.noms27.recovery_campaign.calibration report \
  --registration registration.json --evidence synthetic-evidence.json \
  --output draft-report.json
```

`preregister` optionally accepts `--manifest` and `--metadata` JSON files. Output
creation is exclusive: neither command overwrites existing evidence/artifacts.
There is no approval, live, execution or scientific-freeze switch.

Preregistration snapshots the manifest, seeds, structural placements, repetitions,
candidate set, full allocation, validity rules, selection/tie rules, measurement
contract and supplied freeze metadata. A content hash binds the snapshot; each
record binds that hash and its candidate configuration hash. Gate-disabled plus
0.0, 0.1, …, 1.0 are fixed. Threshold comparison remains strictly below; enabled
0.0 is not the same as gate-disabled for invalid/missing confidence. Both combined
slots bind the same shared S1 interface/settings metadata, not candidate-specific
prompts. Numeric ties use a preregistered ascending order, independent of JSON
object ordering; disabled wins equal scores.

The default calibration plan is 180 matched blocks, each containing all twelve
candidates and a **separate** ungated comparator: 2,340 synthetic trial records.
Comparator sharing is not assumed or implemented. Calibration allocations are
additional to the evaluation floor.

## Synthetic evidence contract

`synthetic-evidence.json` is an array of `FixtureRecord` objects. Every record has
`trial_id`, `block_id`, nullable `replaces`, `registration_sha256`,
`configuration_sha256`, `evidence_kind="synthetic_calibration_fixture"`,
`placement_id`, `repetition`, `traffic_seed`, `environment_seed`,
`scenario_sha256`, `candidate`, `valid`, `invalid_reasons`, `recovery_failure`, and
`disruption_seconds`. See the fixture constructor in
`tests/examples/test_recovery_calibration.py` for complete executable examples.

These are **declared synthetic outcome assertions**, not raw scientific packet
records or independently verified model outcomes. This slice does not implement
an adapter that promotes retained live runner evidence into calibration evidence;
it deliberately rejects scientific/live/held-out evidence kinds. Existing packet
reconciliation and feasibility reporting are unchanged. A later scientifically
authorized workflow must establish trustworthy provenance, measurement validity,
approved budgets, actual matched conditions and actual identical interfaces.
Hashes bind declarations; they do not prove that collection occurred after
preregistration or that declared scientific settings were used.

Selection requires complete coverage of every declared calibration block, with
all candidates/comparator exactly once, matching placement, scenario hash and
traffic/environment seeds. Held-out/unknown placements and seeds, duplicate trial
IDs, unknown candidates, config/hash mismatches, malformed scalar types, extra
fields, nonfinite/negative disruption and incomplete blocks fail closed.

Every original record stays in `retained_records`, including valid unsuccessful
and invalid attempts. Valid failures contribute to failure counts and disruption.
Only frozen demonstrated-experimental-failure reasons permit invalidation. An
invalid block requires a complete explicitly linked replacement with fresh trial
and block IDs; the entire original block is excluded, not just its unfavorable
member. Replacing a valid block, branching/orphan/cyclic replacement chains,
partial replacements and reclassifying model failures as invalid are rejected.

Eligible candidates have total observed failures no greater than the comparator.
Reports retain class sample counts, failure differences, disruption means and
class-specific/overall differences for **every candidate**, including negative and
ineligible findings. Eligible candidates minimize the mean of the six class means,
not a pooled trial mean. Disabled wins ties/no improvement. An internally
inconsistent disabled fixture that violates eligibility and has no eligible
improvement blocks selection rather than silently waiving the failure constraint.
The report explicitly disclaims population-level noninferiority.

## Draft evaluation and freeze metadata

The draft preserves all four named primary slots and direct S2 plus both combined
slots at higher load. The selected synthetic policy applies only to the fourth
slot. Gate-disabled can make the two combined policies identical without merging
slots or reducing trials: default allocation stays **1,440 + 1,080 = 2,520**,
excluding calibration, feasibility and invalid-block replacements. The existing
manifest enforces six classes, at least one calibration/two evaluation placements,
disjoint structural placements/seeds, and at least 30 repetitions.

Metadata captures model identities/revisions, runtime/decoding/hardware settings,
prompts, schemas, catalogues, the combined S1 interface, budgets and feasibility
references, workload, code/dependency revisions, measurement/clock validation,
reset/containment protocol, randomization/repeated model calls, analysis and
resource assumptions, executor, full manifests and sample counts. Missing sections
and required nested details are listed, not invented. Numerical budget/workload
and analysis-contract conflicts are rejected. This is snapshot/completeness
reporting, not validation of arbitrary model/runtime metadata or execution order.

The draft includes policy/selection hashes, fixed measurement/validity contracts,
the entire held-out plan and null approval records. Regardless of metadata or
fixture coverage: `scientific_selected_policy=null`, `scientific_freeze=false`,
`execution_authorized=false`; budget/resource and Pedro evaluation approvals
remain null. Live implementation/containment/measurement validation, six-class
fault-free/induced-fault feasibility, successful-observation sufficiency agreement,
budget/resource approval, workload/placement scientific freeze, real calibration
and Pedro's final approval remain explicit blockers. No real calibration, scientific
freeze, budget sign-off, execution authorization or tracker acceptance is claimed.
