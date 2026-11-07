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
Unknown S1 inference/action termination continues to deny S2 admission even
when time remains. **Pedro decided containment is required**: a requested but
safety-denied escalation does not satisfy the S1 timeout criterion. Keep the
criterion open until termination can be confirmed or work safely isolated and
a contained handoff demonstrated with time remaining. The separate model
decision blocker also remains. Development budgets, six-class feasibility and
held-out settings remain unapproved.
