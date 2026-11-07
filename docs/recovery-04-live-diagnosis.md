# Ticket 04: combined gated live diagnosis (development only)

Ticket 03 was accepted by Pedro on 2026-11-07; the confidence-gate commits
(`69c5229`, `caf4899`, `dcb8c9e`) were cherry-picked onto `feat/recovery-03-live`
as `feat/recovery-04-live` (conflict residues resolved against the containment
runner; `_done` — formatting commit `bcd7d34`). Full suite **1,554 passed,
3 skipped, 179 subtests (185.86 s)**; ruff, scoped formatting, full Pyright and
network-free `--check` for the gated configuration all pass with the explicit
worktree interpreter.

Two live development trials on the broader diamond with the accepted route-v2
interface and gate enablement as the only variable. Thresholds are unapproved
development placeholders; source archive SHA-256
`f4c6863a48e65e066c80c1ce124fba8433deb21d943d3133152f7c608f720265` (tip
`bcd7d34`):

| Trial | Threshold | Gate decision (S1 confidence) | Outcome |
| --- | --- | --- | --- |
| `development-live-gated-001` | 0.5 | `proceed` (0.9531, valid) — provisioned threshold below observed confidence | S1 executed bypass, verification failed, contained escalation admitted S2, route h1→s1→s3→s4→h2 installed on s1/s3/s4, service **recovered**; policy `verified`, exit 0 |
| `development-live-gated-002` | 0.99 | `escalate` (0.9518 < threshold) — **local execution skipped** | Handoff cause `confidence_below_threshold` with **9.81 s remaining**; **no S1 action events** (only s2 in the action evidence); S2 attempt 1 recovered the same route; policy `verified`, exit 0 |

## Gate-boundary mechanism checks (2026-11-07, development only)

Script `scripts/recovery-04-controlled-live.py` (commit `9f33b5f`) scripts only
the S1 decision; the runner, Mininet adapter, fault, packet verifier, contained
escalation, model-driven S2 route-v2 stage and cleanup stay real. Guards
restrict use to the broader combined-gated diamond with an enabled gate, the
executable `bypass` choice, and valid confidence only at exact threshold
equality. Source archive `recovery-04-source-001.tar` SHA-256
`7aadcfaf6fc1752c843a7ca181d1cc44213387d9161e3db77de633afe5c9e2ba` (tip
`9f33b5f`), deployed isolated in the guest at `/tmp/recovery-04-source-002`;
configs `recovery-04-config-00{3,4,5}.json` (threshold 0.5, development
values, checksummed). Availability checked without inference before any model
contact: `:9931/health` ok, `/props` identity `h2oai/h2o-lightning-4b`
(Q8_0, same path, `total_slots: 1`), `:11434/api/ps` digest
`56671c2ab938…` — all unchanged from the accepted trials. VM booted without
provisioning; fresh trial IDs and evidence directories; guest network clean
before and after; VM halted gracefully after export. Evidence kind
`mechanism_validation`; the S1 model made no decision; no threshold
selection or effectiveness claim.

| Trial | Scripted S1 | Gate event | Outcome |
| --- | --- | --- | --- |
| `development-live-gated-003` | `bypass`, confidence `0.5` == threshold `0.5` | `proceed`, `local_execution_skipped: false` | S1 bypass executed locally, verification failed, contained escalation admitted S2; S2 attempt 1 installed s1 1→3, s3 1→2, s4 3→1 (h1→s1→s3→s4→h2) and **recovered**; `verified`, exit 0; packets 234 failed of 1,500, disruption 2.34 s, recovery from 2.4 s |
| `development-live-gated-004` | `bypass`, confidence absent | `escalate`, cause `confidence_missing_or_invalid`, `local_execution_skipped: true`, `confidence_present: false` | **No S1 action events** (s2 only); S2 attempt 1 recovered the same route; `verified`, exit 0; packets 113 failed of 1,500, disruption 1.13 s, recovery from 1.2 s |
| `development-live-gated-005` | `bypass`, confidence `"0.9"` (numeric string) | `escalate`, cause `confidence_missing_or_invalid`, `local_execution_skipped: true`, raw value recorded | **No S1 action events** (s2 only); S2 attempt 1 recovered the same route; `verified`, exit 0; packets 114 failed of 1,500, disruption 1.14 s, recovery from 1.2 s |

All three: validity true, healthy baseline, real persistent outage, full 15 s
observation with no censoring, late restoration or relapse, cleanup verified
(0 owned bridges/interfaces, workers stopped), provider containment positive
(`next_trial_allowed: true`). Evidence tars in `recovery-04-acceptance/`:
003 SHA-256 `33b937b95b5a2465ced3c83d90e80a0883e49cf2cf54c9f04477b844ae236b85`,
004 `423db18f156fa15264253e7990ab396f0976d5a53c1dc1470bf466e0c456c185`,
005 `be038032c4e68b10b7b5c9d0b74843cdf96ba747477931e2f8374a7ddce5153e`.

The live gate boundary is now closed: every confidence-gate decision observable
on the combined-gated treatment — `proceed` (strictly above **and equal**), and
escalation for below-threshold, missing, and invalid confidence — has live
mechanism evidence. Equality-proceeds and missing/invalid escalation
previously rested on offline controlled tests alone.

The earlier two model-driven trials below remain the gate's proceed
(strictly-above) and escalate (below-threshold) demonstrations.

Original interpretation boundaries of the two model-driven trials (2026-11-07,
superseded for the boundary coverage by the section above): exactly one
gate-escalation cause was shown live; threshold **equality proceeds** and
`confidence_missing_or_invalid` escalation were offline-covered only; the
gate-disabled configuration remains offline-covered. These trials validate
gate mechanism behavior in live evidence; they do not establish confidence's
effectiveness and select no campaign threshold. Evidence tars:
`recovery-04-acceptance/recovery-04-evidence-001.tar` SHA-256
`42950d6c9707688565b85d69b63bdc0833984e0f04812aa098ac4391f487bedf` and
`recovery-04-evidence-002.tar` SHA-256
`8fdd4cc2ef71917950deff1f9f376c02e7d5244ae1a145b973c7e261efe3d56d`; configuration
checksums `a0122f98…` / `e0e9f821…`. VM halted after export.
