# Ticket 06: offline validation

## Scope and integration

Implementation worktree `/tmp/opencode/recovery-06-implement`, branch
`feat/recovery-06-feasibility`, initially clean at integration `b56af8f`.
Loaded the TDD skill and used the already approved configuration → retained
evidence → report seam. Read the authoritative offline scope/spec/ticket, domain
glossary, ADRs 0001/0012/0013 and retained 03/04/05/integration documentation.
The explorer's `/tmp/opencode/recovery-06-notes/measurement-inventory.md` informed
test selection; it is exploration, not validation evidence.

Before final checks, fast-forwarded this branch to integration **`b10274d`**,
including the retained gated implementation and matched-block opt-in seam. No
shared runner/block/measurement implementation was edited by ticket 06.
Existing local Python/Ruff/Pyright executables were reused from
`/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/`; no installs/downloads.

## Red → green evidence

Ignored logs remain in `run-evidence/recovery-06-development/`:

| Slice | Red observation | Green behavior |
| --- | --- | --- |
| `01-*` | Absent reporting module | 48 planned cells, repairability, full allocation and distinct calibration accounting |
| `02-*` | Missing budget output | Known 2 s/4 s verified repairs produce doubled p95s; explicit handoff/verification enlargement |
| `03-*` | Boolean measurement survived Python's numeric equality | Strict JSON/type/packet reconciliation; unusable originals retained |
| `04-*` | Real retained evidence had two injection-start events | Select deadline-bearing submission anchor, not bounded-operation/journal timestamps |
| `05-*` | Finite per-trial assumptions overflowed aggregate totals | Reject nonfinite derived outputs |
| `06-*` | Held-out seed and inconsistent provenance accepted | Declared manifest/seed/provenance identity checks reject contamination |
| `07-*` | S1 cap omitted slower locally successful combined slot | Include successful local repairs across S1-bearing slots |

The worked nearest-rank fixture has 30 valid attempts: 19 repairs verified at
2 s, one at 4 s, ten censored failures. Its successful p95 is **2 s**, not the
maximum; successes are **20**, not the attempt floor 30. An unapproved successful
minimum of 20 permits only that fixture cell; 21 blocks it. Neither permits
scientific sign-off. Separate known cases check maxima across classes/loads,
partial coverage, missing inputs, retained invalids and no silent success-only
attempt accounting. The packet fixtures use existing `measure()` to construct
consistent artifacts; the reporting assertions use worked literal outcomes and
do not claim independent revalidation of every packet algorithm.

Additional input tests cover duplicate IDs/path aliases, missing files, duplicate
JSON keys, NaN/Infinity, bool/string numeric coercion, schema/scenario/outcome
inconsistency, calibration/held-out rejection, additive replication, comparator
reuse requiring justification, separate token/inference/runtime totals, config
path resolution, exclusive report creation and rejection of approval/live flags.

One pre-final run found a fixture's floating-point capacity calculation just
below the runner's 100 packets/s minimum. Only the fixture capacity was corrected;
no workload validation was weakened. All initial failure logs remain retained.

## Final checks

Commands run from the worktree root with the existing environment on PATH:

```bash
python -m pytest tests/examples/test_recovery_feasibility.py \
  tests/examples/test_recovery_campaign_blocks.py \
  tests/examples/test_recovery_campaign_gated.py -q \
  -p no:cacheprovider -o tmp_path_retention_policy=failed \
  --basetemp=/tmp/opencode/r06-final-regression-02

python -m pytest tests/examples/test_recovery_campaign.py -q \
  -k 'send_bins or late_restoration or slow_injection or missing_essential_packet or send_gaps or worker_record_gaps or deadline_cancels or known_late_action or s1_cap_does_not_reset or separate_token_totals or failed_inference_without_provider or incomplete_startup' \
  -p no:cacheprovider -o tmp_path_retention_policy=failed \
  --basetemp=/tmp/opencode/r06-measurement-final

ruff check .
ruff format --check examples/noms27/recovery_campaign/feasibility.py \
  tests/examples/test_recovery_feasibility.py
pyright --pythonpath /home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python
pyright --pythonpath /home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python \
  examples/noms27/recovery_campaign/feasibility.py \
  tests/examples/test_recovery_feasibility.py
git diff --check
```

- **159 passed in 122.96 s**: 41 report-boundary cases, 30 block cases and
  88 gated cases (`regression-tests-final.log`). Block tests cover all six classes
  at both loads, gated/ungated matching, reset ownership, retained invalid whole
  blocks/replacements, ingress-matched repairs, S2 authority and containment.
- **20 passed, 51 deselected**: selected existing measurement/containment/token
  checks (`measurement-tests-final.log`), including deduplication, delivery-deadline
  equality, binning, relapse, late restoration/censoring, send-record gaps, clock
  errors, slow injection, deadline/cancellation and late results.
- Repository Ruff, scoped formatting, repository Pyright and explicit new-module
  Pyright: passed, zero type errors/warnings. Whitespace check passed.
- Default JSON config → CLI plan report: passed (`default-plan-*`).

The tests were selected after inspecting source/inventory for real socket/network
activity. **Did not run**
`test_real_udp_workers_and_native_protocol_emit_mechanism_evidence`: it opens real
loopback sockets/subprocess workers. No repository-wide pytest result is claimed.
Selected MockTransport, controlled graph and accelerated-clock tests do not open
network listeners. Passed temporary test artifacts are automatically removed;
logs and the final demonstration remain. No other session's evidence was deleted.

## Retained controlled demonstration

The reporting suite's parameterized `run_campaign` boundary test was retained as
a small demonstration: four slots × both loads on the calibration-placement
diamond, with fresh controlled graph/model state each time. The gated slot uses
threshold **1.0**, an unapproved mechanism setting, not calibrated policy. Commands:

```bash
python -m pytest tests/examples/test_recovery_feasibility.py -q \
  -k retained_run_campaign -p no:cacheprovider \
  -o tmp_path_retention_policy=all \
  --basetemp=run-evidence/recovery-06-development/controlled-demo-v2
python -m examples.noms27.recovery_campaign.feasibility \
  --config run-evidence/recovery-06-development/controlled-demo-v2-config.json \
  --output run-evidence/recovery-06-development/controlled-demo-v2-report.json
```

The retained JSON config lists the eight `evidence.json` paths relative to its
directory and supplies **unapproved fixture assumptions** of successful minimum 1
and handoff allowance 0.5 s. Eight tests passed and all eight artifact timings
reconciled. The report retains missing metadata, incomplete class/replication
coverage and all scientific blockers. Its illustrative cap/deadline values are
mechanism fixtures, **not real resource or feasibility measurements**;
`scientific_budget=null`, `coverage_complete=false`,
`numerical_signoff_ready=false`, `execution_authorized=false`.

An earlier retained demonstration used the same trial ID in isolated pytest
directories. Combining them correctly failed the duplicate-ID gate
(`controlled-demo-cli.log`); no report was written. The test fixture then gained
unique treatment/load IDs, and the new `-v2` artifacts/config/report were created
without overwriting that original evidence. Neither demonstration is calibration,
held-out, or live evidence.

## Pending acceptance

No scientific approval, numerical budget freeze, workload/placement freeze,
calibration selection, live/held-out run or model/resource measurement occurred.
Actual UDP delivery/offered-load accuracy, clock calibration, real fault/reset
and resource absence, production inference usage/identity and termination remain
unvalidated. CPU/memory/GPU/energy inputs remain unmeasured. Ticket 03
containment/live and 04/05 live prerequisites remain blocked. Parent independent
review, integration and tracker steps remain outstanding; this record does not
close tickets 03–06 or authorize execution.
