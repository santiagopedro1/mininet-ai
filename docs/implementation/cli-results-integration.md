# CLI results/storage integration repair (2026-10-30)

## Diagnosis

The integration merge `022a786` changed `mininet_ai/cli.py` relative to its
`main` parent while retaining that parent's results/storage/inspection modules
and acceptance tests. The merged CLI accepted older per-file flags and lacked
`--results-dir`, `--storage-config`, inspection publication, interruption
reporting and grouped terminal warnings. Imports resolved to the checkout,
not a stale installed package. Full regression before repair was 137 failed,
1045 passed, 3 skipped; this was not waived as unrelated test noise.

Fast reproductions before repair:

```bash
uv run pytest -q tests/test_results_acceptance.py::test_run_help_explains_inspection_and_independent_output_controls tests/test_storage_options.py::test_help_groups_live_results_and_advanced_storage --tb=short
```

Both failed on missing results guidance/help grouping. Most other failures
rejected `--results-dir` before entering the runtime.

## Repair

- Restore the existing `StorageCommand` and strict explicit storage config,
  current results-root selection, migration diagnostics, results help and
  owner-side storage controls. No new storage contract is introduced.
- Restore derived inspection publication and startup/operation failure context
  without changing exit rules or converting unknown cleanup into success.
- Restore stdout/stderr separation, invocation-attributed failures, interruption
  observation, and default warning grouping while retaining detailed logs.
- Preserve the bounded branch's `plan --output`, saved-plan execution and
  conditional Agno factory construction. Do not restore the old `main` CLI
  wholesale and lose these behaviors.
- Preserve both reasoning-complete and lifecycle-finalized audit records in
  the append-only ledger. Inspection must not classify a final action/state
  commitment failure as a failed reasoning attempt. Its invocation outcomes
  still come from the current-run end report, not inferred success from reasoning.
- Update integration assertions to check each audit phase and retain complete
  structured responses and action results. No audit records or tests are deleted.

## Validation and review

The previously affected CLI/results/storage/inspection/control group passed
226 tests (8 subtests). A further regression checks that an actual reasoning
failure is counted once despite its separate finalized failure record. A new
CLI integration test exercises real native-client readiness through injected
transport and a Fake Substrate: a saved bounded plan executes with results
publication without recompiling source, changing its digest or constructing
Agno. It sends no real inference and deploys no network.

Final full regression: **1178 passed, 3 skipped, 179 subtests passed**
(69.95 seconds); skips are opt-in Mininet/OVS integration checks. Ruff lint,
repository formatting, Pyright, whitespace, and diamond/iperf
validation/planning checks pass. The earlier failure report remains historical
evidence, not the current offline verdict.
Self-review compared restored code against the merge's `main` parent and
checked saved-plan, factory-selection, failure/cleanup and audit-phase handling.
No independent external review is claimed.

The evidence/docs/diagnosis regressions were saved separately as `336a364`.
The pre-existing user edit in `mininet_ai/specification/models.py` is left
unchanged and excluded from the repair commit; working-tree checks include it.

## Remaining #116 gates

This repairs offline integration, not native model correctness. The archived
[prompt-tuning attempts](bounded-decision-prompt-tuning.md) failed local
port-fault diagnosis. #124 needs an explicitly agreed model/request-design
evaluation; #125's privileged real-network matrix remains blocked. No model
service changes, inference requests, network trials or merges are part of this
repair.
