# Ticket 06: offline gate/scenario integration

## Merge scope

`feat/recovery-06-integration` starts at ticket 05 integration `b56af8f` and merges
`feat/recovery-04-integration` at `dcb8c9e`. The only textual conflict was in
`examples/noms27/recovery_campaign/runner.py`:

- Retain the frozen scenario instance, matched-scenario validation, higher-load
  offline restriction and scenario-specific S2 port authority from ticket 05.
- Retain the fourth treatment, strict confidence configuration, shared confidence
  validation, JSON-safe evidence and confidence-gated handoff from ticket 04.
- Keep deadline inheritance, shared-failure precedence and containment unchanged.

## Small compatibility seam

`blocks.run_blocks(..., confidence_threshold=...)` now admits the fourth offline
slot when an explicit finite numeric development threshold in [0,1] is supplied.
The CLI spelling is `--development-confidence-threshold`. Only `combined_gated`
enables confidence escalation. Without a threshold, the three-treatment primary
and two-treatment higher-load rehearsal defaults remain unchanged. The manifest
allocation, calibration-only execution and held-out refusal remain unchanged.

Threshold validation happens before creating evidence. The block export records
the development threshold and actual available/reserved treatments. No scientific
configuration or calibration artifact is created or claimed.

Cross-seam tests exercise all six classes at both loads, compare matched scenario,
seed and initial forwarding across the combined slots, require fresh owners,
verify skipped S1 execution and inherited handoff deadlines, and check that
extended repairs retain all three switches while no-path outcomes remain valid
unsuccessful trials. Existing gate, block, S1, S2 and ungated regression tests are
also run against the merged tree.

## Static and CLI checks

- Repository Ruff lint: passed.
- Scoped Ruff formatting: passed (15 files).
- Full Pyright with the existing Python 3.14 environment: zero errors/warnings.
- An additional explicit Pyright pass over the campaign examples and gate/block
  tests: zero errors/warnings after making the event sanitizer's mapping
  construction explicit. Default repository Pyright excludes the examples as
  direct check targets; the inherited whole-value sanitizer call had a mapping
  unpack type error there. Per-value sanitization preserves the same JSON values.
- All four incident development configurations and the block manifest passed
  their network-free `--check` commands.
- The CLI opt-in rehearsal completed six calibration blocks with threshold 0.5;
  it exported mechanism-only evidence under the ignored
  `run-evidence/recovery-06-cli-development/` directory, with held-out execution
  false.
- Whitespace/conflict-index checks: passed.
- Final explicit-mapping sanitizer regression: 33 invalid-confidence cases passed
  (55 unrelated gate cases deselected), covering finite-invalid, missing and
  nonfinite evidence in both combined slots and gate-disabled fourth-slot cases.
- Final explicit-mapping cross-seam regression: all 8 new block integration and
  threshold-rejection cases passed (22 pre-existing block cases deselected).

The first focused regression attempt exceeded the 120-second shell limit; it was
not counted as a completed run. The next attempt exhausted the shared disk quota
while exporting test evidence and even failed pytest summary/cache writing, so
its pass/fail stream is not treated as a valid regression result. Only this
session's temporary test trees were removed (about 365 MiB); no retained worktree
or other session's evidence was deleted. The retry disables pytest's cache and
uses `tmp_path_retention_policy=failed`, removing passed-test temporary evidence
as tests complete. The separately checked CLI rehearsal evidence is retained.

The retry completed successfully: **252 passed in 170.01 seconds**, covering
`test_recovery_campaign_blocks.py`, `test_recovery_campaign_gated.py`,
`test_recovery_campaign_ungated.py`, `test_recovery_campaign_s2.py` and
`test_recovery_campaign.py`. That process started before the behavior-preserving
event-mapping type fix. Against the final mapping construction, the full gate
suite passed separately (**88 passed in 33.28 seconds**) and all 8 new block
integration cases passed as recorded above. A repository-wide test run was not
performed; these are focused offline regression results, not live acceptance.

## Limitations

This is controlled, in-process mechanism evidence only. Live acceptance, six-class
live feasibility, measurement validation, approved budgets/resources, calibrated
and frozen thresholds and held-out authorization remain unresolved. Unknown
terminal S1 work still denies S2 admission under the existing safety boundary.
No main merge, push, GitHub action, network/provider request, VM access or
dependency installation is part of this integration. The parent owns tracker
updates; the paper tracker was not accessed or modified.
