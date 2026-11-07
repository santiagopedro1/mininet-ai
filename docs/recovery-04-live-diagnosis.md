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

Interpretation boundaries: exactly one gate-escalation cause is shown live;
threshold **equality proceeds** is offline-covered only; `confidence_missing_or_
invalid` escalation is offline-covered only. Gate-disabled configuration was
exercised offline. These trials validate gate mechanism behavior in live
evidence; they do not establish confidence's effectiveness and select no
campaign threshold. Evidence tars:
`recovery-04-acceptance/recovery-04-evidence-001.tar` SHA-256
`42950d6c9707688565b85d69b63bdc0833984e0f04812aa098ac4391f487bedf` and
`recovery-04-evidence-002.tar` SHA-256
`8fdd4cc2ef71917950deff1f9f376c02e7d5244ae1a145b973c7e261efe3d56d`; configuration
checksums `a0122f98…` / `e0e9f821…`. VM halted after export.
