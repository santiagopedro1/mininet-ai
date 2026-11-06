# Ticket 02: offline implementer validation

## Scope and authority

- Worktree: `/tmp/opencode/recovery-02-implementer`.
- Branch: `feat/recovery-02-direct-s2`.
- Confirmed clean base and integration ancestor before implementation:
  `bb980111958997b1c01dbe4c556c0de5fc5c50d2` on
  `feat/recovery-02-integration`.
- Authoritative four-treatment specification and ticket 02 were read from the
  paper repository; ticket 01's still-open live blocker was retained.
- Scope: direct S2 only. No paper tracker, older pilot behavior, combined policies,
  calibration, live incident, VM startup, occupied model computer, remote issue
  tracker, dependency download or push was involved.

## Tests and evidence

The approved seam is declared configuration → campaign → observable exported
events/evidence. Controlled model responses, port/forwarding observations and
known packet timelines are **offline mechanism validation**, not campaign trials.
New checks assert exported records/journals and CLI output, not policy helpers.

Red→green slices included rejected direct-S2 configuration, the missing real S2
adapter/multi-switch mechanism, incomplete Provider responses incorrectly allowing
further requests, and the explicit controlled broader-repair scenario declaration.
Coverage includes no S1 wait/decision; shared initial snapshot and operational
alarm; broader telemetry/forwarding authority; schema/port/traffic restrictions;
fresh execution-admission port checks; three-attempt and common-deadline bounds;
late results; unsuccessful, malformed, inability and partial-action outcomes;
separate warm-up/incident inference accounting; and independent packet outcomes.

The broader fixture independently walks a forwarding graph: one local redirect
cannot repair it, then the next S2 attempt installs two downstream rules. Failure
variants retain partial action records or reject a now-down port. The declared
`controlled_broader_repair` scenario cannot select live execution. It does not
implement or validate an extended-diamond campaign class.

Raw check logs and retained test evidence are ignored by Git under
`run-evidence/recovery-02-development/` in this worktree. Test evidence remains
explicitly `mechanism_validation`; full-suite fixtures are not trial allocation.
The final full-suite temporary root is `/tmp/opencode/r02-tests`, kept short for
the repository's Unix-domain control-socket tests.

## Check results

- Focused S1/S2 boundary suite: **97 passed**; final S2-only rerun: **26 passed**.
- Repository-wide Ruff lint: passed.
- Full Pyright: zero errors/warnings; explicit campaign/test Pyright also passed.
- Scoped Ruff formatting: passed; no unrelated formatting files were rewritten.
- Repository-wide formatter check retains the ticket 01 baseline difference in
  `tests/examples/test_noms27.py:471`; that unrelated file was not changed.
- Network-free direct-S2 CLI configuration check: passed.
- Whitespace check: passed.
- Final full suite: **1,411 passed, 3 skipped, 179 subtests passed** in 137.45 s
  (`full-tests-short-path.log`).

One intermediate full rerun used an excessively long nested `--basetemp` path:
**1 failed, 1,410 passed, 3 skipped, 179 subtests passed**. The only failure was
`tests/test_artifacts.py::test_cli_uses_shared_root_for_artifacts_and_default_control`:
its generated Unix socket exceeded the OS path limit. The same test passed in
**1.09 s** with `/tmp/opencode/r02-probe`, with no source change. The failed log
(`full-tests-final.log`) is retained rather than hidden; the final full suite uses
the short root above. This is a test-path/environment error, not recovery evidence.

The existing execution checkout's installed environment was reused via
`/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/`; no packages were installed
or changed. Pyright was given that environment's Python explicitly. The full
suite retains existing specification-deprecation warnings.

## Handoff and remaining gates

### Standards review follow-up (2026-11-06)

All three Standards findings were addressed together in `cc8d079`:

- `supervision.py` now owns the shared inference/action lifecycle recording,
  including cancellation-unknown results, completion timing and late completion
  accounting. Treatment-specific decision validation, S1 failure-token decoding
  and S2 Provider-terminal interpretation remain in their treatment paths. No
  campaign/runtime API was widened.
- Incident S2 request construction and provenance use the same `MODEL_SETTINGS`.
  Warm-up deliberately retains its separate two-token output budget.
- `MAX_ATTEMPTS` supplies both the permission declaration and loop enforcement.

The existing settings characterization passed before extraction (26 S2 tests);
this was a behavior-preserving refactor, not a claimed failing-feature red→green
cycle. Boundary assertions now compare retained incident wire requests and
provenance against independently specified settings, retain the distinct warm-up
budget, and check cancellation evidence for inference/actions in both treatments.
Tests do not import or assert the new helper structure.

Integration was confirmed clean at `40733c6` before edits. After committing the
fixes, `git merge feat/recovery-02-integration` again reported **already up to
date**, and its tip `40733c60f0ab45877176b8055295f8eb0d99631b` was verified as an
ancestor of `cc8d0799c6b953393741d31a13c60bea602ae5bc` before this report update.

Follow-up checks, using the same installed environment and no network access:

- Focused S1/S2 boundary suite: **101 passed** in 45.74 s.
- Full suite: **1,415 passed, 3 skipped, 179 subtests passed** in 146.12 s;
  existing specification-deprecation warnings remain (124 warnings total).
- Repository-wide Ruff lint: passed.
- Full and scoped campaign/test Pyright: **zero errors/warnings**.
- Scoped Ruff formatting: **10 files already formatted**.
- Network-free direct-S2 CLI configuration check and whitespace check: passed.

Raw test/type-check logs are retained under
`run-evidence/recovery-02-development/review-fixes/`; full-suite temporary
artifacts use `/tmp/opencode/r02-review-tests`. No dependency installation, VM,
model computer, Provider connection, fetch, push, or paper-tracker edit was
performed. These results address offline review findings only and do not change
any live/scientific acceptance gate below.

This is the implementer handoff, **not** independent code-review clearance or a
declaration that the branch is ready for `main`. The parent session owns merger
and subsequent Standards/Spec reviews, paper-tracker updates and acceptance.
Retain the implementer worktree for that process; do not push.

Ticket 01 still blocks live acceptance. Ticket 02's live broader-repair criterion,
all live scenario/placement/load feasibility, approved numerical budgets/resources,
calibration/freezing and scientific authorization remain outstanding. No offline
test can resolve those gates. See the [implementation/runbook](recovery-02-implementation.md).
