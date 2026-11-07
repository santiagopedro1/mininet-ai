# Ticket 02: model-selected route interface (development)

## Decision and seam

Pedro chose to **keep System Two responsible for finding the route**, not to
enumerate candidate end-to-end repairs in the harness. The explicit
`s2_decision_interface: route-v2` selects a separate decision interface; the old
`actions-v1` mode remains available for retained historical mechanism tests and
development evidence. This is a changed S2 interface/configuration, not a
retroactive success of the earlier direct-S2 baseline.

The S2 model receives the same alarm and the shared initial local diagnostic
snapshot, projected into measured port state and active-service flows, plus
network-wide operational port/flow telemetry. The adapter summarizes *individual
observed live links* from the declared topology's peer/port map and current port
liveness, requiring both switch endpoints to be up. It does **not** enumerate
end-to-end routes, reveal an evaluator fault label or inject the expected path.
Raw initial and network-wide OVS dumps remain in synchronized event evidence.

The model emits only `{"status":"repair","path":[...]}` or explicit inability
with an empty path. A local compiler rejects repeats, unknown nodes, missing
adjacencies, down ports and non-reciprocal switch links; it then translates **the
model-selected route** into one fixed directional IPv4 forwarding rule per
switch on the route. It never chooses or substitutes a route. The existing typed
action validator checks all compiled ports again; the live adapter reobserves
port states at action admission. No fault reversal, arbitrary commands, offered
traffic changes, retries outside the shared deadline or evaluator data are added.
Installation is sequential, not atomic; independent packet verification, action
and service outcomes, full observation and cleanup/containment remain unchanged.
Requests/responses and the chosen route plus compiled actions are retained.

## Evidence and limitations

The earlier controlled live run with **scripted** three-switch actions proved the
mechanism can restore packet service; it did not establish model acceptance.
Replays `replay-008` and `replay-009` showed route selection fails with raw peer
maps, including a compact initial snapshot. With observed *individual live-link
facts* and no route candidates supplied, five unchanged non-incident prototype
replays (`010`–`014`) selected the observed surviving route, each in 0.53–1.19
seconds. Three additional replays (`015`–`017`) used the **actual production
route-v2 request/schema/validation code** and all selected a valid route in
0.87–1.01 seconds. Every request and response is retained separately under
`/home/pedro/projects/mininet-ai/live-acceptance/recovery-02-acceptance/`.
These are prompt/interface development observations, **not** live recovery,
effectiveness estimates or approved numerical budgets.

Network-free declaration:

```sh
python -m examples.noms27.recovery_campaign \
  --config examples/noms27/recovery_campaign/development-s2-route.json --check
```

## Live mechanism acceptance

The fresh direct-S2 run `development-live-s2-route-001`, from commit `b81e4aa`,
was **valid and independently recovered** on the controlled broader diamond.
The designated Ollama `qwen3.5:latest` model selected
`h1→s1→s3→s4→h2`; the compiler installed three typed rules on s1/s3/s4.
The observed sustained packet recovery began at 1.3 s and verification
completed at 2.4 s under the unapproved 10 s deadline. All 148 recorded fault
checks retained the persistent outage; no downstream active backup was
preinstalled. The 15-second observation had 1,500 sends and 123 failed timely
deliveries (1.23 s disruption); no relapse, late restoration or censoring.
Cleanup and provider containment were positive. Raw evidence and checksum-
receipted source/evidence archives are under
`/home/pedro/projects/mininet-ai/live-acceptance/`, with full trial details and
limitations in the paper tracker ticket 02. Evidence archive SHA-256:
`4a35e2d2718e2da511b39fb6a646d7cf73cac4ff371832e2cca388e529167d9a`.
Source archive SHA-256:
`f203dd64ac835a386e753043f69a5e8b8dd9dc01721305a8375733c2bc5e9a6e`.

This accepts one **live mechanism incident**, not the extended-diamond six-class
campaign, treatment effectiveness, numerical budgets or a frozen policy.
Development deadline, workload, decoding and resource settings remain
unapproved; later feasibility, calibration and held-out approval gates still
apply. A future run requires a new ID and evidence directory and separate
validity/outcome/cleanup/containment inspection.
