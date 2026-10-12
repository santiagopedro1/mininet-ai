# Link-failure connectivity recovery contract

For the link-failure latency experiment, measure sustained host-to-host delivery
over an alternate path, not merely successful configuration changes. Agents
install supplied policies rather than discover routes, isolating response and
policy installation; consecutive successful probe rounds avoid treating a single
successful packet as confirmed recovery.

Decision: [#30 — Define observable network recovery after a link failure](https://github.com/santiagopedro1/mininet-ai/issues/30), part of
[#29 — Plan the link-failure latency experiment](https://github.com/santiagopedro1/mininet-ai/issues/29).

This is an agreed planning contract, not a description of implemented behavior.

## Required traffic and baseline

- Fix the designated host pairs and directions before each Link-Failure Trial.
  Recovery requires all designated traffic, not an arbitrary successful flow or
  every possible host pair. Never shrink the required set after injection.
- Use reachability probes, rather than throughput or application performance,
  as evidence of packet delivery. Probe each designated pair and direction once
  per second.
- Establish a healthy baseline with three consecutive rounds in which every
  designated probe succeeds. An unhealthy baseline invalidates the trial.
- After injection, require at least one failed designated probe to establish
  observed disruption. Otherwise report “no observed disruption,” not recovery.

## Agent-controlled restoration

- Agents install supplied alternate-path forwarding rules on their own switches.
  This experiment measures response and policy installation, not autonomous
  route discovery.
- Agents cannot restore the failed link. Exclude automatic failover and
  controller-driven recovery so those mechanisms do not substitute for the
  agent-controlled behavior under study.
- Select failures with an alternate path available for the designated traffic.
  If a partition nevertheless occurs, retain the trial as unsuccessful and
  record the reason; do not redefine the traffic requirement.

## Recovery evidence

- Connectivity Recovery requires three consecutive rounds in which every
  designated probe succeeds while the failed link remains unavailable.
- Confirm that the failed link remains down throughout the confirmation rounds.
  Any failed probe resets the consecutive-success streak. If the failed link
  comes back up, the trial is invalid, not recovered.
- Successful configuration changes or action postconditions alone do not prove
  Connectivity Recovery. The probes establish connectivity, not performance.
- Successful probes and confirmation that the failed link remains down suffice;
  packet tracing is not required to prove alternate-path traversal.
- Recovery is a shared network outcome. It does not prove that every agent
  contributed or establish individual causal attribution.
- Partial restoration, or fewer than three consecutive all-success rounds when
  the trial deadline arrives, remains unsuccessful. Retain the observations
  rather than declaring recovery or silently dropping the trial.

## Remaining decisions and implementation gap

Exact topology and host placement belong to
[#32 — Choose the seeded ten-switch topology and failure scenario](https://github.com/santiagopedro1/mininet-ai/issues/32).
Deadline duration and lifecycle details belong to
[#33 — Define repeated failure trials and their lifecycle](https://github.com/santiagopedro1/mininet-ai/issues/33).
Exact latency timestamp boundaries belong to
[#31 — Define detection, reasoning, and action-effect timing boundaries](https://github.com/santiagopedro1/mininet-ai/issues/31).
Clock and observation instrumentation belong to
[#34 — Choose clock, correlation, and instrumentation contracts](https://github.com/santiagopedro1/mininet-ai/issues/34).
Machine-readable statuses and aggregation belong to
[#35 — Define raw latency records and trustworthy summaries](https://github.com/santiagopedro1/mininet-ai/issues/35).

The existing `examples/link-failure-latency/run.py` harness uses supplied recovery
policies and currently accepts one successful ping with a failed-link down check.
That is weaker than the agreed three-round confirmation criterion. This decision
does not change the harness or settle probe scheduling, timeout, or clock details.
