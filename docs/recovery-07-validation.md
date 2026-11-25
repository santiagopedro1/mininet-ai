# Ticket 07: offline validation

## Method and scope

Loaded the TDD skill and used the specification's already confirmed campaign
configuration → retained evidence → report seam. Known-outcome fixtures were added
before implementation. Initial red observations included the absent module/report,
missing replacement support, an ineligible disabled fixture hiding an eligible
improvement, absent metadata details, accepted malformed numerical metadata,
boolean seed coercion, boolean threshold snapshot tampering, and JSON object order
changing numeric tie selection. Each was corrected through the public report or
preregistration boundary. Early red results are in the session transcript; the
numeric key-order red log is also retained under
`run-evidence/recovery-07-development/key-order-red.log`.

The worked unequal-size fixture adds a second calibration placement for
`diamond_link`: class means **16, 8, 8, 8, 8, 8** yield **9⅓ seconds**, while a
pooled trial average would be **10 2/7** against a 10-second comparator. A separate
fixture makes the best disruption candidate fail one extra recovery and verifies
its rejection; another preserves equal total failures with a +1 adverse difference
in one class and −1 in another. Ties and worse numeric thresholds select disabled
without reducing the allocation. None of these are scientific observations.

The tests additionally cover missing/duplicate/unmatched records, unknown
candidates, held-out seed/placement contamination, registration/config/scenario
identity, retained unsuccessful outcomes, complete invalid-block replacement,
invalid-reason abuse, replacing valid blocks, orphan/cyclic/partial replacements,
disjoint structural splits and replication floors, strict scalar types, negative
disruption, missing metadata, forbidden approval fields/switches, exclusive output
creation, duplicate JSON keys and nonfinite numbers.

## Checks and retained evidence

Existing tools were invoked from
`/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/`, without installing anything
or editing that checkout. Commands were run from this implementation worktree:

```bash
python -m pytest tests/examples/test_recovery_calibration.py \
  tests/examples/test_recovery_feasibility.py \
  tests/examples/test_recovery_campaign.py \
  tests/examples/test_recovery_campaign_blocks.py \
  tests/examples/test_recovery_campaign_gated.py \
  tests/examples/test_recovery_campaign_s2.py \
  tests/examples/test_recovery_campaign_ungated.py \
  -q -k 'not real_udp_workers' -p no:cacheprovider \
  -o tmp_path_retention_policy=failed \
  --basetemp=run-evidence/recovery-07-regression-v2
ruff check .
ruff format --check examples/noms27/recovery_campaign/calibration.py \
  tests/examples/test_recovery_calibration.py
pyright --pythonpath /home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python
pyright --pythonpath /home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python \
  examples/noms27/recovery_campaign/calibration.py \
  tests/examples/test_recovery_calibration.py
git diff --check
```

The final broad regression passed **347 tests, 1 deselected in 166.75 s**
(`final-regression-v2.log`): 55 new selection tests plus 292 inherited campaign and
feasibility tests. The real-loopback UDP test was the sole deselection.
The final scoped selection suite also passed **55 tests in 3.37 s**
(`selection-final-v2.log`). Repository Ruff and repository/scoped Pyright passed
(`ruff-v2.log`, `pyright-repository-v2.log`, `pyright-scoped-v2.log`); scoped
formatting passed (`format-v2.log`), as did staged/unstaged whitespace checks. Final logs
are under `run-evidence/recovery-07-development/`. The CLI boundary test retains
registration, 2,340 original synthetic rows and a disabled selection/draft report
under
`run-evidence/recovery-07-selection-final-v2/test_cli_preregisters_then_rep0/`. Source hashes and
the complete originals are included in that report; overwriting is tested and
rejected. These ignored artifacts remain in this worktree for parent inspection.

The initial broad run encountered filesystem **disk quota exceeded (errno 122)**;
its exit 120/truncated `final-regression.log` is not a passing regression result.
Its 1,067 original files were archived in
`run-evidence/recovery-07-failed-regression.tar.xz`; each archived file's SHA-256
was checked against its original before removing the loose duplicate. Archive
SHA-256: `30bbbacf69650a7d65086c9fbcad50d7fefd5b2c7d517ec118e559ba5c72ee20`.
An attempted gzip archive also exceeded quota and was discarded only while all
originals remained intact; the verified xz archive is retained. Redundant earlier
successful test temporary directories were removed; final demonstrations and all
quota-failed-run originals remain. The rerun uses pytest's automatic cleanup of
passing temporary directories to bound quota use, not altered runtime code.

Merged `feat/recovery-07-integration` into this branch before handoff: already up
to date at `315035e`; ancestry was checked again. Only local implementation/docs
commits were created. This branch remains for the parent's separate merger and
independent review, not a declaration of readiness to merge into `main`.

The explicitly excluded `test_real_udp_workers_and_native_protocol_emit_mechanism_evidence`
opens real loopback sockets. No repository-wide pytest, real UDP, live network,
VM, provider, held-out experiment or scientific measurement result is claimed.
Inherited controlled runtime/MockTransport tests are mechanism regressions only.

## Pending acceptance and limitations

The new normalized synthetic records are outcome assertions, not an independent
packet measurement validator or a live runner-evidence ingestion implementation.
Snapshot hashes cannot certify chronological preregistration or truthful scientific
provenance. Arbitrary supplied model/runtime metadata is preserved, not independently
verified; randomized execution orders are not run or validated by this processor.
All live prerequisite and scientific gates remain pending, including budget/resource
and Pedro frozen-configuration approvals. No artifact from this slice can authorize
execution. Tickets 03–07 are not accepted or closed. Parent integration and separate
independent review remain required; no main merge or remote write is performed.
