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

- Require an explicit nonnegative unsigned 64-bit seed and record the generator
  version and configuration. Encode the seed as exactly eight big-endian bytes.
- For version 1, derive each stream key as
  `SHA-256(UTF-8("mininet-ai/link-failure-latency/v1") || 0x00 || seed_bytes || UTF-8(stream_label))`.
  The fixed-width seed and single separator make the encoding unambiguous.
- Generate stream words as the first eight bytes, interpreted unsigned big-endian,
  of `SHA-256(stream_key || counter_u64_be)`. Counters start at zero and increment
  per digest; exhausting the unsigned 64-bit counter is an error, not a wraparound.
- To draw uniformly from `[0, n)`, reject words `x >= floor(2^64 / n) * n`, then
  return `x mod n`. Shuffle by descending Fisher–Yates, for `i = len(items)-1`
  through `1`, drawing `j` uniformly from `[0, i+1)` and swapping positions `i,j`.
- Use the canonical lexicographic ordering of UTF-8 switch IDs (`s1`, `s10`,
  `s2`, …) before every shuffle or candidate enumeration. Shuffle this ordered
  switch list to form the cycle; enumerate normalized non-cycle switch pairs
  lexicographically; draw the chord count uniformly from 1–5; shuffle the complete
  candidate list and take its first `chord_count` pairs.
- Use independent labels `topology`, `placement`,
  `warmup-failure-pass:<zero-based pass>`, and
  `measured-failure-pass:<zero-based pass>`. The topology stream draws
  the cycle shuffle, then chord count, then chord shuffle from its continuing
  counter. The placement stream shuffles canonical switch IDs and attaches `h1`
  and `h2` to the first two switches. For each failure pass, sort normalized
  eligible-link pairs lexicographically, shuffle with that pass's fresh stream,
  and consume its order before deriving the next pass. Warm-up count and failed
  attempts therefore cannot shift measured failure order.
- Assign switch-to-switch ports first, independently at each switch, by ascending
  neighbor switch ID starting at port 1. Assign host-facing ports afterward by
  ascending host ID, continuing the switch's port sequence.
- These rules fully define version 1 scenario generation and failure ordering.
  The same seed, version, and configuration reproduce the graph, ports, host
  attachments, and eligible-failure ordering. Changing any rule requires a new
  generator version. Never rely on unordered collection iteration or
  process-randomized hashes.
- Retain the actual graph, port mapping, host attachments, selected fault, and
  healthy/recovery forwarding paths. Recorded scenario data is authoritative for
  replay even if a later generator maps the same seed to a different scenario.

### Version-1 conformance vector

`../fixtures/link-failure-latency-seed-v1-0.json` is a golden vector for seed 0.
It records the seed bytes, stream keys, first topology words, cycle, chords, full
graph, host placements, per-switch ports, and measured eligible-failure order.
Implementations must match it exactly before claiming generator version 1.

## Hosts, traffic, and network configuration

- Use two hosts with fixed identities, IP addresses, and MAC addresses. Select
  two distinct attachment switches uniformly using the placement random stream;
  attach one host to each. Do not require adjacent attachment switches.
- Keep the existing example addressing: `h1` is `10.0.0.1/24` with MAC
  `02:00:00:00:00:01`; `h2` is `10.0.0.2/24` with MAC `02:00:00:00:00:02`.
  Only their distinct switch attachments vary by seed.
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
  the two hosts. At each switch, among neighbors one hop closer to the destination,
  choose the lexicographically smallest UTF-8 switch ID (or the destination host
  when directly attached). Use explicit output rules for host traffic and directed
  ARP, not cyclic-topology broadcast flooding.
- Healthy policies use the complete graph. Recovery policies exclude the failed
  link. Stable policies are loop-free; this does not promise loop-free transient
  mixtures during independent updates.
- An eligible failure is a switch-to-switch link used by at least one designated
  healthy forwarding path, with recovery routes available for both directions
  after removing that link. Exclude host-access links and unused links.
- Compute the stable eligible set from the two directional healthy forwarding
  paths and alternate-route checks, then use the per-pass streams above for the
  agreed no-replacement selection lifecycle in
  [#33 — Define repeated failure trials and their lifecycle](https://github.com/santiagopedro1/mininet-ai/issues/33).
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

Trial repetition, reset, and failure-selection lifecycle are specified in
[ADR-0004](0004-repeated-failure-trial-lifecycle.md). Clock/correlation and
instrumentation are specified in [ADR-0005](0005-clock-correlation-and-instrumentation.md);
record semantics and summary populations are specified in
[ADR-0006](0006-raw-latency-records-and-summaries.md).
