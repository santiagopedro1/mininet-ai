# Confidence-gated combined recovery: offline validation

## Scope and provenance

- Worktree: `/tmp/opencode/recovery-04-implement`.
- Branch: `feat/recovery-04-confidence-gate`, created from integration tip
  `1b0ea1a`; `git merge-base --is-ancestor 1b0ea1a HEAD` passed.
- Implementation commit: `69c5229`. Merging the latest local integration branch
  returned `Already up to date`; integration ancestry was checked again.
- Only ticket 04 implementation, tests and documentation are changed. The parent
  session owns the paper tracker; it was not modified here.
- No installed `implement-spec` workflow was available. Work followed the ticket
  acceptance criteria, the approved runner/evidence seam and the TDD skill.
- Existing tools in `/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/` were
  reused without dependency installation or modification.

## Red–green coverage

Tests enter through declared `CampaignConfig`, run an incident, and compare the
returned evidence with the exported JSON. Controlled native HTTP transport and
model outputs test mechanisms only, not held-out treatment effectiveness.

1. Low-confidence correct repair first failed because the fourth slot and gate
   fields were unsupported; it now skips local execution and hands off with the
   original budget, retained choice, gate cause, service/token/latency outcomes.
2. Missing/invalid confidence first lacked recorded validation status; nonfinite
   values also crashed strict JSON export. Both slots and gate-disabled fourth-slot
   cases now retain the values and only confidence-escalate when enabled.
3. Fourth-slot CLI validation first failed for the absent development configuration;
   it now declares both stages' authority plus gate semantics without network access.
4. Gate evidence initially omitted the confidence and selected choice; those are
   now explicitly retained with presence, validation and skipped-execution fields.
5. A large JSON integer exposed overflow in native confidence validation. Checking
   bounds before finiteness now preserves it as invalid confidence rather than a
   whole-decision error. Native missing-confidence presence also had a failing
   assertion before its adapter/runner evidence fix.

Additional cases cover strict inequality/equality, threshold 0 and 1, invalid
configuration, identical native S1 requests/responses and provenance across slots,
shared inability/unusable decisions, confident failed actions/verification,
gate-disabled defaults, S2 attempt exhaustion and unsuccessful service outcomes,
contained verification timeout and refused uncontained inference/action handoff.
The shared strict HTTP parser still rejects non-JSON NaN/Infinity identically in
both slots; controlled runtime nonfinite confidence is retained and gate-tested.

## Checks

- Repository Ruff lint: passed.
- Scoped Ruff formatting: passed (10 files).
- Full Pyright with explicit installed Python: zero errors/warnings.
- All four network-free development configuration checks: passed.
- New confidence-gate boundary suite: **82 passed** in 29.08 s.
- Whitespace: passed.
- Final serial full regression: **1,530 passed, 3 skipped, 179 subtests passed**
  in 176.74 s, with the existing 124 deprecation warnings.

Logs and controlled evidence remain in `run-evidence/recovery-04-development/`
(ignored by Git). An intermediate focused run had **210 passed, 1 failed** because
the old configuration-rejection test still classified the newly supported fourth
slot as unsupported; that assertion now rejects deterministic restoration instead.
The first full run exceeded the shell's 120-second timeout and is not counted as
a completed result. The next full run (`full-tests-final.log`) had **1,529 passed,
1 failed, 3 skipped, 179 subtests passed** in 186.47 s. The existing loopback UDP
worker test observed 592 packets against its minimum of 790. Its exact isolated
rerun passed in 14.31 s (`udp-recheck.log`), and a serial full-suite rerun with no
concurrent checks passed (`full-tests-serial.log`). No assertion or worker behavior
was changed to hide that timing-sensitive failure; host scheduling is a plausible
cause, not proven. Both completed runs retain the existing 124 warnings.

The successful final run has no shell timeout and uses the short pytest root
`/tmp/opencode/r04-serial-tests`, avoiding the known Unix-socket path limit.
Targeted large-numeric and presence red/green logs are also retained.

## Independent review and resolution

The parent session reported independent review of integration `caf4899`:

- **Standards:** zero hard violations and one maintainability smell, **Duplicated
  Code** in confidence validation across native mapping, adapter evidence and
  runner policy.
- **Spec:** zero actionable deviations.

The implementer resolved that finding with one campaign-local
`confidence.valid_confidence` predicate, shared by all three callers. Bounds are
checked before finiteness to avoid huge-integer conversion overflow. Booleans,
numeric strings, containers, out-of-range values and nonfinite values remain
invalid; missing-confidence and strict native-JSON failure behavior are unchanged.
Escalation enablement, threshold comparison and containment remain in the runner.
No core runtime or policy semantics were changed.

Because this is a behavior-preserving review refactor, characterization tests
were green before extraction rather than manufacturing a failing behavior. The
existing approved configuration → runner → exported-evidence tests now also
assert native adapter evidence and gate validation status against literal expected
statuses, adding false, numeric strings, containers, upper-bound violations and
negative infinity to the native-response matrix. No predicate-internal tests or
mocking of the shared helper were introduced.

Post-review checks were run serially:

- Expanded gate boundary suite before extraction: **88 passed** in 32.96 s.
- Expanded gate boundary suite after extraction: **88 passed** in 32.91 s.
- Repository Ruff lint: passed; scoped format: passed (11 files).
- Full Pyright with explicit installed Python: zero errors/warnings.
- Full serial regression: **1,536 passed, 3 skipped, 179 subtests passed** in
  180.58 s, with the existing 124 warnings. No UDP-worker failure occurred.
- Whitespace: passed.

Logs are `review-before-tests.log`, `review-after-tests.log`, `review-ruff.log`,
`review-format.log`, `review-pyright.log` and `review-full-tests-serial.log` under
`run-evidence/recovery-04-development/`. Full regression used
`/tmp/opencode/r04-review-tests`; no tests ran concurrently. The integration branch
was merged before the follow-up and checked again at handoff. This records the
implementer's resolution and validation, not an independent re-review of the fix.

## Handoff and limitations

Initial validation above was implementer self-review; the subsequent independent
review and implementer resolution are distinguished in the preceding section.
No subagents, live experiments, VM/model-machine access, real provider
calls, dependency downloads, pushes, GitHub writes or main merges were performed.
Existing network-free/controlled transport checks do not validate live faults.

Acceptance remains blocked by ticket 03's safety boundary: unknown terminal
inference/action work requests escalation but denies S2 admission, even with time
remaining. Human review must accept that containment boundary or supply additional
support; this slice does not mark the trigger criterion complete. All prerequisite
live acceptance, measurement validation, six-class feasibility, approved budgets
and resources, calibration/freezing and held-out authorization remain outstanding.
The development threshold is not calibrated and no effectiveness claim is made.
