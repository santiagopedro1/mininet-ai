# Ticket 05 validation

## Scope

Branch `feat/recovery-05-matched-blocks`, worktree
`/tmp/opencode/recovery-05-implement`, initial/integration base `1b0ea1a`.
The approved configuration-to-evidence seam was used for red → green development.
Initial tests failed on the absent manifest/block runner; later slices caught
missing CLI output, inconsistent serialized receipts and a controlled-model
failure that affected S1 but accidentally left S2 successful. The final mechanism
checks include all available treatment paths and both loads.

No VM, model computer, remote model call, live experiment, held-out effectiveness
run, dependency download, push, PR or paper-tracker update was performed.
Existing tools were reused from
`/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/`.

## Baseline

Parent-reported check on untouched integration `1b0ea1a`:

```bash
python -m pytest -q --basetemp=/tmp/opencode/r05-base-tests
```

**1,448 passed, 3 skipped, 179 subtests passed**, 124 existing warnings, 148.05 s.
This is a baseline comparison, not validation of ticket 05 changes.

## Implementation checks

Logs and controlled run artifacts are retained in the ignored directory
`run-evidence/recovery-05-development/`. All commands run at the worktree root
with the existing virtual environment's executables; no dependency installation
was needed.

```bash
python -m pytest tests/examples/test_recovery_campaign_blocks.py -q \
  --basetemp=/tmp/opencode/r05-final-block-tests
ruff check .
ruff format --check examples/noms27/recovery_campaign/{blocks,scenarios,controlled,runner,s2,__main__}.py \
  tests/examples/test_recovery_campaign_blocks.py
pyright --pythonpath /home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python
pyright --pythonpath /home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python \
  examples/noms27/recovery_campaign tests/examples/test_recovery_campaign_blocks.py
git diff --check
```

- Focused block suite: **18 passed** in 56.44 s (`block-tests-final.log`), including
  duplicate held-out incident and repeated traffic seed rejection. The earlier
  17-test run is also retained (`block-tests.log`).
- Repository Ruff, scoped formatting and whitespace: passed.
- Full-repository Pyright and explicit campaign/test Pyright: zero errors/warnings.
- Network-free checks of all three existing development configs: passed
  (`legacy-config-checks.json`).

The initial full run (`full-tests-initial.log`) found that accepting 70% load
globally unintentionally loosened the original incident configuration. It had
**1 failed, 1,459 passed, 3 skipped**, with 179 subtests passed. The fix confines
70% load to the offline matched rehearsal; original live/development paths retain
their prior rejection. The first corrected full run passed **1,465 tests**, with
3 skipped and 179 subtests passed, in 205.09 s (`full-tests-pre-distinct.log`).
The final full rerun after the duplicate-placement/seed guard uses:

```bash
python -m pytest -q --basetemp=/tmp/opencode/r05-verified-tests
```

Final full-suite result: **1,466 passed, 3 skipped, 179 subtests passed**, with
the same 124 existing warnings, in 205.84 s (`full-tests.log`). This is 18 added
boundary checks relative to the parent-verified baseline.

The separate final controlled CLI runs used the exported manifest, without
constructing a VM/network or contacting a provider:

```bash
python -m examples.noms27.recovery_campaign.blocks --check \
  --write-manifest run-evidence/recovery-05-development/manifest.json
python -m examples.noms27.recovery_campaign.blocks \
  --manifest run-evidence/recovery-05-development/manifest.json --controlled \
  --evidence run-evidence/recovery-05-development/controlled-primary-verified \
  --repetitions 2 --invalid-first essential_measurement
python -m examples.noms27.recovery_campaign.blocks \
  --manifest run-evidence/recovery-05-development/manifest.json --controlled \
  --evidence run-evidence/recovery-05-development/controlled-higher-verified \
  --repetitions 1 --load 0.7
```

- Primary rehearsal: completed **13 blocks / 39 incident invocations**: 12 valid
  matched blocks across six classes, one retained invalid original block and its
  fresh-ID replacement. All three available treatments were invoked repeatedly.
- Higher-load rehearsal: completed **6 blocks / 12 incident invocations**, direct
  S2 and combined ungated across all six classes.
- Both export `evidence_kind=mechanism_validation`, `heldout_execution=false`.
  Original and final development rehearsal outputs are retained separately.
- A separate retained artifact audit (`artifact-audit-verified.json`) verifies
  manifest hashes, current Python source hashes, unique trial IDs, all incident
  containment receipts, the 1,440/1,080 planned slots, and retained valid
  unsuccessful/censored serial-tail incidents
  (12 primary-rehearsal incidents and 4 higher-load incidents).

The controlled artifacts were generated from the dirty development worktree.
Their recorded Git revision is the base, with worktree status and per-file source
hashes retained; that revision alone must not be mistaken for the implementation
commit. The final commit hash is reported in the handoff. The small repetitions
are mechanism rehearsals, not real calibration or a reduction of the declared
30-repetition campaign floors.

Tests cover structural witnesses independent of outcomes, split/seed rejection,
all six classes and available treatments, forwarding-only multi-switch repair,
valid no-path failures, matched reset inputs, randomized order, repeated calls,
whole-block fresh-ID replacement, bounded replacements, containment stop,
persistent all-incident-link isolation, partial reversal rejection, manifest
round-trip, future four/three allocation slots, higher-load observation, live
purpose rejection and frozen S2 authority (including observed-but-undeclared
switches/hosts and isolated switch ports). Renaming a calibration incident cannot
convert it into a held-out placement.

## Limits

These are controlled in-process scripts/packets, not live feasibility, actual
model reliability or held-out effectiveness evidence. No scientific gate or
numerical budget is approved. The live substrate remains the original diamond
adapter; six-class live feasibility stays pending. Ticket 03's unresolved
inference/action-timeout containment boundary remains unchanged. Independent
Standards/Spec review and parent integration are not claimed by these checks.
