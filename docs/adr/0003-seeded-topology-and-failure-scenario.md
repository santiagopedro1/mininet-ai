# Seeded ten-switch topology and failure scenario

Use a seeded ring-plus-chords graph to vary topology structure while guaranteeing
alternate paths after a single switch-link failure. Supply deterministic forwarding
policies rather than use learning or automatic failover, so successful connectivity
recovery exercises agent-controlled policy installation.

Decision discussion: [#32 — Choose the seeded ten-switch topology and failure scenario](https://github.com/santiagopedro1/mininet-ai/issues/32), part of
[#29 — Plan the link-failure latency experiment](https://github.com/santiagopedro1/mininet-ai/issues/29).
The user confirmed this planning contract; it does not describe implemented
generation or runtime behavior. It builds on the
[connectivity recovery contract](0001-link-failure-connectivity-recovery.md) and
[timing boundaries](0002-link-failure-latency-boundaries.md).

## Graph distribution

- Use exactly ten stable switch identities and exactly one agent per switch.
- Shuffle the switch IDs into a spanning cycle using the topology random stream.
- Choose an extra-link count uniformly from 1–5. Sample that many distinct
  non-cycle switch pairs uniformly without replacement, using canonical candidate
  ordering before random selection.
- The resulting switch graph is simple and undirected, with 11–15 links and no
  self-links or duplicate links. Every switch belongs to the spanning cycle, so
  removing any single switch-to-switch link leaves the switch graph connected.
- This samples a ring-plus-chords distribution, not every connected ten-switch
  graph uniformly. Unlike merely relabeling a ring, seeds can vary graph structure.

## Seed and replay contract

- Require an explicit seed and record the generator version and configuration.
  The same seed, version, and configuration must reproduce the graph, ports,
  host attachments, and eligible-failure ordering.
- Keep topology generation, host placement, and failure selection in separate
  random streams. Changing trial repetition must not change the topology.
- Define deterministic selection and tie-breaking order. Generation must not
  depend on unordered collection iteration or process-randomized hashes.
- Any change to generator behavior requires a new generator version. The
  reproducibility guarantee does not extend to a different version or configuration.
- Retain the actual graph, port mapping, host attachments, selected fault, and
  healthy/recovery forwarding paths. Recorded scenario data is authoritative for
  replay even if a later generator maps the same seed to a different scenario.

## Hosts, traffic, and network configuration

- Use two hosts with fixed identities, IP addresses, and MAC addresses. Select
  two distinct attachment switches uniformly using the placement random stream;
  attach one host to each. Do not require adjacent attachment switches.
- Designate both directions between these hosts as required traffic. This is a
  focused connectivity experiment, not an all-pairs or performance workload.
- Use controller-free switches in secure mode with OpenFlow13. Exclude learning,
  flooding, `NORMAL`, spanning-tree recovery, and preinstalled automatic failover.
- Use ordinary host ARP rather than static neighbor entries. Forwarding policies
  must support directed ARP as well as host traffic.
- Keep all link characteristics fixed: 100 Mbps bandwidth, 1 ms configured delay,
  and zero configured loss. This isolates graph variation from link-quality
  variation; actual probes still determine success.

## Supplied forwarding and eligible failures

- Construct deterministic minimum-hop destination-rooted forwarding trees for
  the two hosts, using stable switch-ID tie-breaking. Use explicit output rules
  for host traffic and directed ARP, not cyclic-topology broadcast flooding.
- Healthy policies use the complete graph. Recovery policies exclude the failed
  link. Stable policies are loop-free; this does not promise loop-free transient
  mixtures during independent updates.
- An eligible failure is a switch-to-switch link used by at least one designated
  healthy forwarding path, with recovery routes available for both directions
  after removing that link. Exclude host-access links and unused links.
- Compute a stable eligible set and seed its selection order using the failure
  random stream. Selection with or without replacement and trial repetition
  remain lifecycle decisions, not decisions in this contract.
- Graph eligibility is not proof of working baseline connectivity or observed
  disruption. Require the agreed healthy baseline and at least one failed
  designated post-injection probe; never shrink the required traffic set.
- Unexpected partitions remain unsuccessful as specified by the recovery
  contract, even though the intended graph family has alternate paths.

## Agent authority and execution

- Each agent may install only its exact supplied recovery policy on its own
  switch. Validate the complete proposal before execution. Enforce rule content,
  not merely access to a generic flow-install capability.
- Grant no link restoration, controller changes, host-process execution, or writes
  to other switches. No automatic mechanism may substitute for agent recovery.
- The harness installs and validates the complete healthy policy before injection.
  Arm observation paths before injection, as required by the timing contract.
- Agents install their recovery policies independently. Mixed old/new rules may
  transiently disrupt traffic; the three consecutive all-success probe rounds
  with the failed link down determine recovery, not configuration acknowledgements.
- Do not wait for every agent to finish before recognizing shared recovery. Ten
  agents is a placement requirement, not a claim that all ten must contribute.

## Implementation gap and remaining work

The current `examples/link-failure-latency/run.py` generator produces labelled
rings, places hosts on adjacent switches, and fails their direct connecting link.
Its path and graph-reconstruction logic is ring-specific. The substrate supports
general links, but adding chords to YAML alone does not implement this contract;
general-graph generation, reconstruction, routing, and eligibility require later
implementation work. No generator or runtime code changes accompany this decision.

Trial repetition, reset, and failure-selection lifecycle remain for
[#33 — Define repeated failure trials and their lifecycle](https://github.com/santiagopedro1/mininet-ai/issues/33).
Clock, correlation, and observation instrumentation remain for
[#34 — Choose clock, correlation, and instrumentation contracts](https://github.com/santiagopedro1/mininet-ai/issues/34).
Record schemas and summary populations remain for
[#35 — Define raw latency records and trustworthy summaries](https://github.com/santiagopedro1/mininet-ai/issues/35).
