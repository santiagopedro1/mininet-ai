# Combined ungated recovery: offline validation

## Scope

- Worktree: `/tmp/opencode/recovery-03-ungated`.
- Branch: `feat/recovery-03-ungated`, based on direct-S2 integration `f31d1cc`.
- Pedro authorized offline implementation and tests only. The paper tracker was
  claimed for this slice; prerequisite live acceptance remains blocked.
- The original execution checkout and direct-S2 integration were not modified.
  No VM startup, external model/provider request, dependency installation, remote
  issue access or push was performed. Controlled transports are in-process;
  existing loopback worker checks are not live network-fault experiments.

## Coverage

Tests use the approved configuration → runner → exported evidence boundary.
They check local completion without S2, confidence-only non-escalation, inability,
unusable decisions, action errors, failed operational verification, contained
stage timeout, missing/incomplete provider termination, unsafe cancelled work,
deadline admission, S2 attempt limits, handoff evidence and combined token costs.

The real S2 adapter runs against a controlled HTTP transport and forwarding graph
for both direct and combined invocation. The combined fixture applies a local
redirect that fails service verification before S2 supplies downstream repairs.
Port-staleness and execution-failure variants retain unsuccessful outcomes.
Startup checks exercise both real native-S1 and S2 protocol adapters through
controlled transports, separate model/endpoint settings and warm-up accounting.

No scripted model/packet fixture is effectiveness evidence or a live campaign
scenario. Actual inference/action cancellation cannot prove provider termination;
unknown work denies handoff rather than overlapping new recovery work. See
[implementation and limitations](recovery-03-implementation.md).

## Checks

Installed tools from `/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/`
were reused without changing dependencies. Pyright needs that Python explicitly
(`--pythonpath`); invoking the tool alone initially selected the wrong environment
and reported missing installed imports. The corrected full check passes.

- Repository Ruff lint: passed.
- Focused S1/S2/combined boundary suite: **134 passed** in 53.83 s.
- Full short-path regression suite: **1,448 passed, 3 skipped, 179 subtests passed**
  in 148.87 s, with the existing 124 deprecation warnings. A final full rerun also
  covers stage-specific cancellation IDs added during self-review.
- Scoped formatting: passed.
- Full Pyright: zero errors/warnings.
- Network-free combined CLI configuration validation: passed.
- Whitespace: passed.

Logs, configuration-check output and controlled evidence are retained in
`run-evidence/recovery-03-development/` (ignored by Git). One intermediate full
run used an excessively long nested pytest temporary root and failed the existing
Unix control-socket path limit check, not a recovery assertion. Its log is kept
as `full-tests.log`. The final full run uses `/tmp/opencode/r03-final-tests`.
The post-review cancellation-ID rerun uses `/tmp/opencode/r03-checked-tests` and
retains `full-tests-checked.log`; wire-evidence assertions distinguish the S1 and
S2 incident request IDs. Stage-specific IDs prevent delayed cancellation of S1
from cancelling a newly admitted S2 request in the shared transport.

## Handoff

This is implementer validation and self-review, not an independent Standards/Spec
review. No merge or push was performed. The local paper ticket remains open for
human acceptance and prerequisite validation; no scientific gate is waived.
Its escalation-trigger criterion remains unchecked: safe verification-timeout
handoff is covered, but actual inference/action-timeout escalation is refused
when terminal work is unknown. Human review must accept that boundary or require
additional containment support rather than treating it as full compliance.
S1 and direct-S2 live acceptance, six-class live feasibility, approved numerical
budgets/resources, calibration/freezing and held-out execution authorization are
still outstanding. Confidence gating is not implemented by this slice.
