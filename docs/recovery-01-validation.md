# Ticket 01 validation and live blocker

## Outcome

The S1-only campaign slice is implemented on local branch
`feat/recovery-01-integration`, reviewed and mechanism-tested. **Ticket 01 is not
resolved:** a valid live incident on the corrected revision remains blocked by
availability of `http://10.10.10.152:9931`. The user chose to finish with that
blocker rather than restart the endpoint, then confirmed that the model computer
is occupied by another person. **Do not contact or use it until newly authorized.**
No merge readiness, physical-failure
effectiveness, approved numerical budget, or campaign completion is claimed.

Runbook: [implementation](recovery-01-implementation.md). Monitor regression:
[diagnosis](recovery-01-monitor-diagnosis.md). Original prerequisite user work:
[baseline provenance](recovery-01-baseline.md).

## Code and checks

| Revision | Purpose |
| --- | --- |
| `b2c9d58` | Isolated snapshot of existing dirty execution-source prerequisites |
| `57eb2a4` | Integrate the S1-only configuration-to-evidence path |
| `5dede608` | Integrate initial review corrections |
| `3deca6bf` | Integrate the fault-monitor lifecycle correction; final code tested |

- Final implementer suite: **1,385 passed, 3 skipped, 179 subtests passed**.
- Focused configuration/evidence suite: **71 passed**, independently repeated by
  the spec reviewer. Scripted model/network responses are mechanism validation.
- Repository Ruff lint and full/scoped Pyright passed. The integration session
  independently repeated repository lint and full Pyright successfully.
- Scoped formatting and whitespace checks passed. Repository-wide formatting has
  a preserved baseline difference at `tests/examples/test_noms27.py:471`; unrelated
  user work was not reformatted.
- Parent independently repeated the full suite: **1,385 passed, 3 skipped,
  124 pre-existing deprecation warnings, 179 subtests passed**, 133.09 seconds.

### Standards review

Four documented violations and two heuristic smells were corrected: cleanup-aware
CLI exit and next-trial admission, failed-inspection handling, shared best-effort
cleanup budget, shared token decoding, and named packet-bin accounting. Re-review
cleared all six and found no new substantive violations in the monitor correction.

### Specification review

Three initial findings were corrected: pre-effective-fault traffic cannot qualify
as restoration, raw sequence/worker summaries reconcile missing packet records,
and known late action results update execution evidence without rewriting timeout
outcomes. Re-review cleared all three and the subsequent monitor correction.

**Final review: zero outstanding Standards findings; zero outstanding Spec findings.**
Live validation remains a separate, failed gate rather than a review waiver.

## Preserved VM attempts

Both attempts declared a persistent `s1-s2` administrative link outage in the
diamond placement, native S1 model alias `h2oai/h2o-lightning-4b`, and explicitly
unapproved development settings: 10-second deadline/cap, five-second baseline,
15-second post-injection observation, 1,200-byte UDP payload at 100 packets/s
(10% payload load of a configured 9.6-Mbit/s bottleneck). Immutable model identity
verification was skipped and recorded, not asserted. Only the first attempt
reached traffic and injection; the second stopped at startup.

| Trial | Revision | Result |
| --- | --- | --- |
| `development-live-s1-001` | `5dede608` | **Invalid**, exit 2: `fault_monitor_failed:`. One successful bypass action and service restoration were observed; cleanup verified. |
| `development-live-s1-002` | `3deca6bf` | **Invalid**, exit 2: startup connection refused; no traffic, fault injection or incident inference began. Cleanup verified. |

The first run's raw-log audit independently recomputed 1,500 post-injection sends,
133 delivery-failed packets, 1.33 lost-packet-equivalent seconds and verification
completion at 2.5 seconds. **These observations do not override trial invalidity.**
The monitor regression reproduced the same invalidity string, but the historical
exception's exact cause cannot be established without its missing traceback.

Full raw evidence and receipts are retained locally, ignored by Git, under
`run-evidence/recovery-01-development/` in the integration worktree. This includes
both unchanged `smoke-001/evidence.json` and `smoke-002/evidence.json`, packet/model
journals, worker logs, source archives and their SHA-256 receipts, model metadata,
the independent development packet-audit script/output, review reports, and the
authorized stale-bridge cleanup record. Git revision is unavailable inside the
guest source archive; the receipt links it to the exact originating commit/hash.

The VM was booted without provisioning and returned to its original powered-off
state after verified cleanup. Only the inspected pre-existing `s1`–`s10` guest
bridges were removed, with explicit user approval. Host networking and original
checkout sources were not overwritten. No push or merge into `main` occurred.

## Human handoff

1. Wait until the model computer is available and Pedro authorizes its use; only
   then start/verify the native endpoint (`/health` and `/props`).
2. Boot the existing VM without provisioning. Deploy the integration revision in
   an isolated guest directory; do not overwrite `/vagrant`.
3. Follow the runbook with a fresh trial ID and evidence directory, retaining both
   invalid originals. Inspect validity, service outcomes, cleanup and containment;
   command exit alone is not recovery evidence.
4. Require a valid corrected live incident before resolving ticket 01. Later
   tickets still own campaign-wide measurement/feasibility and budget sign-off,
   additional treatments/scenarios, calibration and held-out execution.

## Completion receipt

- Parent command: existing execution `.venv/bin/python -m pytest -q` from this
  integration worktree. The first invocation hit the 120-second tool limit without
  a reported test failure; the rerun with a 360-second limit passed in 133.09s.
- Parent checks also passed: `ruff check .`, full Pyright with the explicit existing
  environment, campaign/test formatting, `git diff --check`, and CLI `--check`
  configuration validation without network/model access.
- VM power-off was confirmed through Vagrant status. Both clean implementer
  worktrees were removed; the integration worktree and all run evidence remain.
- The paper ticket is **open / `ready-for-human`**, with the live criterion
  unchecked. No further model-machine access occurred after the user explained
  its unavailability. No dependent ticket was started or unblocked.
