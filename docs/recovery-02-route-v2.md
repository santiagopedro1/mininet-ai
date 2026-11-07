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

For a future authorized live trial, copy the sample config to a fresh ID,
`purpose: live_feasibility`, Ollama model/endpoint and a previously nonexistent
evidence directory. Use the isolated guest source archive procedure in the
ticket-01 runbook. Inspect validity, service outcome, cleanup and containment
independently before any following run. A controlled success or a non-incident
replay cannot check ticket 02's live acceptance box. Development deadline,
workload, decoding and resource settings remain unapproved; later six-class
feasibility, numerical budget approval and held-out configuration freeze are
still required.
