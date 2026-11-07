# Ticket 02: live direct-S2 decision diagnosis (development only)

The controlled live mechanism run `development-live-s2-controlled-001` used the
normal Mininet diamond, persistent s1–s2 outage, independent packet monitor and
cleanup, but **scripted** the S2 decision to install three permitted rules on
s1, s3 and s4. It was valid and recovered service on time (0.13 seconds
lost-packet-equivalent disruption), with verified cleanup and positive containment.
Its `evidence_kind` is `mechanism_validation`; the model did **not** choose the
repair. Source archive SHA-256: `7e24bf793408f3d044bcd432b9b1c9367ee5ed56dead4abdbdfe3ce6333f089b`.
Evidence tar SHA-256: `d1271fba4c3e931506dbc258d4cbd7742ec10cf8b642e475ec4855993a4b0913`.
Both archives and extracted evidence are under
`/home/pedro/projects/mininet-ai/live-acceptance/` and `recovery-02-acceptance/`.

The replay tool `scripts/recovery-02-replay.py` reuses the first real S2 request
from `recovery-02-broader-evidence-003` and sends one non-incident inference at
a time to the recorded endpoint. It never executes a repair. Each numbered
`replay-*` directory under `recovery-02-acceptance/` contains the exact request,
raw response and summary. Single calls are exploratory, **not** matched
effectiveness evidence or proof of a model's deterministic behavior.

| Replay | Request condition | Result |
| --- | --- | --- |
| 001 baseline | Original recorded request, think off | `unable` plus nonempty actions (invalid decision); 1.47 s |
| 002 neutral | No single-switch example; think off | Repair on s1's down port and s2's down port (invalid); 2.16 s |
| 003 neutral-thinking | Enable reasoning | `unable`; explanation incorrectly maps s1 port 3 to s2; 11.23 s |
| 004 port-map | Reverse observed peer map to `port_to_peer`; think off | `unable`; 0.82 s |
| 005 port-map-thinking | Reasoning on with explicit port mapping | Correctly identifies s3 path, but exhausts 1,024 output tokens without JSON; 12.65 s |
| 006 port-map-thinking-long | Raise output cap to 2,048 | Again exhausts cap without JSON; 24.81 s |
| 007 algorithm | Generic path/flow procedure, port map, think off | Invalid repair (down s1 port and nonexistent s2 port); 2.33 s |

No variant has yielded a valid model-chosen complete three-switch repair. The
scripted check narrows the blocker to model decisions under the current S2
interface, not an unrepairable Mininet placement. The original recorded prompt
included a single-switch JSON example; removing it alone did not solve the
problem. Neither reasoning configuration fits the unapproved 10-second
development deadline, and simply raising its output cap did not produce JSON.

**Do not change the live S2 policy based on these replays without a distinct
validated candidate.** Do not promote a scripted mechanism check to S2
acceptance, extend the numerical deadline without feasibility/approval, or retry
until a favorable sample appears. A different model or an algorithmically supplied
repair candidate would change the evaluated decision-maker and requires Pedro's
scientific decision. Ticket 02 and dependent live tickets remain open.
