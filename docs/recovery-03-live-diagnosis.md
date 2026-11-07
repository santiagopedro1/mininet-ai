# Ticket 03: combined ungated live diagnosis (development only)

The first combined live trial `development-live-combined-001`, from integrated
commit `3743141`, was **valid and unsuccessful**. S1 selected `bypass`, installed
the s1 forwarding rule, and failed independent service verification. A full
handoff with 8.59 seconds remaining admitted S2, which returned `unable` after
observing the surviving h1–s1–s3–s4–h2 links. Packet service was not restored;
the 15-second observation had 1,500 failed timely deliveries. All 148 fault
checks retained the outage, and cleanup and provider containment passed.
This is a retained negative result, not ticket acceptance. The evidence tar
is under `recovery-03-acceptance/`, SHA-256
`423948c18f729707d16beff69f97ed58d6c0eee13375b631f1e51835d7035fb7`.

The original S2 wire context contained an 11.8 KB full S1 handoff and consumed
5,996 input tokens. The route-v2-only `route_handoff_view` now presents the
escalation cause, S1 decision, attempted action and execution result, S1 status
and remaining time at handoff. The full original handoff, including snapshots,
decisions, actions and events, remains unchanged in the runner's handoff/event
evidence. `actions-v1` still receives the original handoff. Controlled
configuration-to-evidence tests cover both interfaces and failed-action,
verification-failure and explicit-inability projections.

The non-incident replay tool `scripts/recovery-03-replay.py` reads the exact
retained S2 wire request, replaces only its handoff with the production compact
projection and validates any returned route against the captured observation.
It **never executes forwarding actions**. The replay requests, responses and
summaries are retained separately in `recovery-03-acceptance/`:

| Replay | Change from failed live request | Outcome |
| --- | --- | --- |
| `replay-001-compact-handoff` | Compact handoff only | `unable`, 1,096 input tokens, 0.98 s |
| `replay-002-clarify-handoff` | Same compact handoff; explain that failed S1 verification does not establish absence of a network-wide path | `unable`, 1,132 input tokens, 0.91 s |

Neither replay is live service verification. Input reduction alone did not
recover model route planning after a failed S1 attempt; do not run another
incident or count the compact projection as an accepted repair until an
unchanged production-format replay selects a valid model-chosen route.
Unknown S1 inference/action termination no longer denies S2 admission
unconditionally: the amended campaign ADR 0013 adds bounded, capability-keyed
containment before admission (local unwind, provider `/slots` confirmation,
action exit status, settled-state observation), staying fail-closed until the
2026-11-07 deadline resolution — **Pedro decided containment is required and the
mechanism satisfies it**: `development-live-combined-005` cancelled S1
inference mid-flight at the 0.1 s stage cap (`remote_termination: unknown`),
probe #1 observed the provider still busy, probe #2 confirmed idle, and the
contained handoff admitted S2 with 9.76 seconds remaining; S2 attempt 1 selected
h1–s1–s3–s4–h2 and service verification recovered (policy `verified`, exit 0).
Trials 003 (1.5 s cap, verification-failed path) and 004 (0.5 s cap,
verification-cancellation path) retained the neighbouring timing cases; all
three evidence tars are chased in `recovery-03-acceptance/` (005 SHA-256
`430a25a902980786cb5e79df09a92b971d23354c5d304461511ae840c39a96f1`). Runs at
`430a25a9…`, `939c136a…`, `b2a1cfd8…` are unapproved development values, not
scientific acceptance; the mechanism code lives on `feat/recovery-03-containment`
tips `bb23c8e`/`156f975`. Development budgets, six-class feasibility and
held-out settings remain unapproved.
