# Diamond recovery (exploratory Maintained Example)

**Primary Goal:** both feature demonstration and realistic scenario. This is an
offline authoring/planning example. It does not claim live model, Mininet, or
OpenFlow support. The checked-in deployment uses the `fake` Substrate and its
observation names are bindings for a future live adapter, not implemented path
probes.

## Prerequisites and plan

Python 3.14 and the repository `uv` environment. No controller or model server
is contacted by validation/planning. The model identity and endpoint are
intentionally marked exploratory placeholders; replace them in a new experiment
before any inference trial.

```bash
uv run mininet-ai validate examples/diamond-recovery/experiment.yaml
uv run mininet-ai plan examples/diamond-recovery/experiment.yaml
```

The source and resulting plan retain the paths, observation bindings, choice
criteria and fixed ordered recovery proposal bundle. One grouped singleton
decision-maker is scoped to the four labeled switches, so normal capability
authorization still checks the target against the compiled scope.

## Topology contract

Pinned switch ports: s1 host=1, s2=2, s3=3; s2 s1=1/s4=2; s3 s1=1/s4=2;
s4 host=1/s2=2/s3=3. Expected link mapping is h1-s1(1), s1-s2(2)/s1-s3(3),
s2-s4(2), s3-s4(2), s4-h2(1). Hosts use MACs
`02:00:00:00:00:01`/`02:00:00:00:00:02` and active addresses
`10.0.0.1`/`10.0.0.2`. Diagnostic aliases are primary `.11`/`.12` and recovery
`.21`/`.22`; install explicit peer static neighbors for all six host addresses
before probing. Production setup must verify actual deployed interface/port
mapping, fail-secure mode, table-miss drop, and absence of `NORMAL`/learning
fallback. The current topology model cannot express host aliases, static
neighbors, or table-miss policy; those requirements are recorded here rather
than falsely presented as installed by the fake Substrate.

Initial active and diagnostic flow rules are priority 100 with permanent
timeouts: active and primary probe pairs use the primary path, recovery probes
use the recovery path, all with exact IPv4 src/dst and ingress-port matches in
both directions. Each one-second observation round contains independent primary
and recovery probes in each direction (one-second timeout, sent/received counts
and timestamp), all four inter-switch link states, current policy and previous
action outcomes. Keep three completed rounds, explicit gaps/missing values and
timestamps; freeze each invocation snapshot. Only distinct active-address
end-to-end probes confirm recovery. The scenario has no hidden chat memory or
oracle-inserted diagnosis.

## Trial lifecycle ownership

Setup/readiness, deployed-port verification, policy inspection, one-link fault
injection (s1-s2 or s2-s4), baseline reset, and cleanup reporting are trusted
harness responsibilities. They must be recorded separately and must never
execute a model-proposed recovery bundle or bypass its ordinary capability
authorization. Reset and cleanup are independent outcomes; do not repeat a trial
when either remains unknown. This repository slice provides the topology and
plan, not a live harness implementation, so these steps remain explicitly
unverified.

The offline fixtures in `tests/fixtures/specifications/diamond-recovery/` label
expected decisions for healthy/startup, transient, sustained, stale/missing/
conflicting, failed recovery-probe, partial/unknown and post-recovery cases.
They are oracle inputs, not an alternate deterministic runtime gate. Offline
mechanics and planning are separate from pinned-model/server evaluation and
live-network acceptance; neither live gate has been run or passed here.
