# Mininet AI

Mininet AI is a declarative experiment compiler and multi-layer runtime for agentic networking research. Users choose where agents attach to an emulated network, what resources they can observe and change, where they execute, and how they coordinate.

The project is being built as an experiment framework rather than a collection of predefined agents. Experiments should be reproducible, inspectable, and portable across networking substrates.

## AI runtime direction

The alpha releases and planned v1 are **Agno-centric**. Agno will own agent and
model execution, sessions, conversation history, agent-local state, learned
memory, summaries, and AI usage metrics. Mininet AI will own experiment
specifications, topology and substrate lifecycle, event delivery, scheduling,
capability authorization, network action execution, cross-agent operational
state, and the reproducibility ledger.

Agno agents produce Mininet `ActionProposal` values; they do not mutate the
network directly. Every proposal continues through the Mininet capability
engine, which validates identity, scope, target, effects, input, and output.
Agno usage measurements are normalized into the experiment ledger alongside
the network events and action outcomes they caused.

The framework-neutral agent and model provider layer introduced in Phase 3 is
deprecated and will be removed before v1. Framework neutrality will be
reconsidered when a concrete second production agent runtime is required. Agno
objects will remain confined to the agent-runtime implementation so Mininet's
experiment, proposal, capability, event, and ledger contracts do not expose
them. See [ADR 0001](docs/adr/0001-use-agno-as-v1-agent-runtime.md) for the
decision and its consequences.

## Current capabilities

Phase 1 introduces the `mininet-ai/v1alpha1` public contract and a compiler that operates without root access or a running Mininet network:

- Strict schemas for experiments, agent blueprints, placements, capabilities, topology resources, coordination, policies, and resource limits.
- YAML documents and Python-created specifications.
- Inline definitions or external YAML references resolved relative to the experiment file.
- Target selectors by resource kind, explicit name, and labels.
- Singleton, per-target, and per-group agent expansion.
- Global, management, control, data, host, observer, and registered custom attachment layers.
- Compile-time substrate, observation, runtime, and capability compatibility checks.
- Least-privilege calculation from capability effects.
- Centralized, hierarchical, distributed, and independent coordination plans.
- Deterministic deployment plans containing the normalized experiment snapshot and a SHA-256 digest.
- A fake substrate that validates experiments without changing the host system.

Logical placement is intentionally separate from physical execution. For example, an agent may be attached to the data plane of a switch while its model executes in an external process. The attachment controls its network scope and available capabilities.

The Phase 3 foundation introduced the independent
`mininet-ai/agent-runtime/v1alpha1` SDK contract. Its scoped invocation context,
structured action proposals, normalized invocation results, capability
contracts, and execution catalog remain part of the Mininet domain. Its generic
agent and model provider protocols are now deprecated; the Phase 4 Agno
migration will replace them rather than extend them.

The current implementation still has explicit agent, capability, and model
provider registries. Only capability extensibility remains a v1 direction;
generic agent and model registration is transitional and deprecated. Plugin
discovery remains opt-in and transactional while the migration is in progress:
compiling or validating an experiment does not import plugins, and failed
discovery leaves existing registrations unchanged.

`CapabilityEngine` is the single execution seam for agent proposals. Before a
provider can run, it verifies the compiled agent identity and scope, assigned
capability, target kind, attachment layer, and effects; validates input against
JSON Schema; and then validates and normalizes provider output. Authorization
violations are rejected without invoking plugin code, while provider and output
failures use typed action results.

Built-in capability adapters connect authorized proposals to substrate actions
or observations, local executables, and HTTP services. External adapters use a
versioned JSON request containing the scoped context and proposal, and require a
`CapabilityOutcome` response. Process adapters never invoke a shell, use a
minimal explicit environment, bound output, enforce deadlines, and terminate
the complete process group. HTTP adapters reject embedded URL credentials,
disable redirects, and bound response bodies.

The built-in OpenAI-compatible and Ollama model adapters are deprecated
transitional implementations. Agno will replace their model invocation,
structured-output, retry, and usage-accounting responsibilities. Credentials
will continue to stay outside experiment documents.

The current declarative and Python agent providers are also deprecated.
Declarative blueprints will construct Agno agents, and Python entrypoints will
return configured Agno agents or factories. User code will still be imported
only at runtime, never during validation or compilation, and will initially run
as trusted code in the orchestrator process; process and namespace isolation
belongs to Phase 6.

The native Agno module is now available alongside that transitional path. It
constructs declarative agents with Agno's canonical `provider:model` resolver,
loads Python-authored Agno agents or factories, requires structured
`AgentResponse` output, and validates the compiled Mininet scope before each
run. It deliberately supplies no network mutation tools: returned proposals
still require capability authorization. Routing the one-shot and continuous
runtimes through this module is the next migration step.

Audit decorators currently record agent invocations, complete model prompts and
normalized responses, token usage, action proposals, results, and typed failures
as versioned JSON events. During the Agno migration, model measurements will be
sourced from Agno run metrics and normalized into the same experiment record.
The JSON Lines sink serializes concurrent appenders,
limits individual event size, and creates owner-only files. Audit records can
contain prompts and observations and must therefore be treated as sensitive
experiment artifacts. Writes are synchronous: failure to record a start event
prevents the wrapped operation from running instead of silently losing audit
coverage.

`OneShotAgentRuntime` currently connects those seams for manual invocations. It verifies
that the supplied deployment plan matches a running substrate, collects only
declared observations, invokes the selected agent and model providers, and
passes every proposal through capability authorization. Ollama,
OpenAI-compatible, deterministic mock, and substrate-backed providers are
built in during the transition; installed provider plugins are loaded only when
explicitly enabled. The Agno migration will replace the agent and model portion
without changing capability authorization.

The Phase 4 runtime-event contract provides one immutable, versioned envelope
for manual intents, interval ticks, normalized observations, agent lifecycle
changes, runtime failures, and plugin-defined event names. Each event carries
run and event identity, source ordering, occurrence and observation timestamps,
correlation and causation identifiers, and a JSON payload. Standard event
payloads have typed models. `InMemoryRuntimeEventBus` provides bounded,
thread-safe FIFO delivery: a full or closed bus rejects publication explicitly,
and closure wakes blocked consumers after already queued events are drained.

`SQLiteRunLedger` persists the reproducibility manifest and append-only history
for experiment runs. A manifest captures the normalized specification, complete
deployment plan, plugin versions and source digests, and runtime configuration.
Records receive durable per-run sequence numbers and can be read forward from a
cursor after restart. Runtime-event and audit-sink adapters preserve their
original versioned records, including invocation, model, capability, and timing
data. Ledger files must be owner-only regular files; unsupported database
versions and unsafe paths are rejected instead of being rewritten.

## Installation

Mininet AI currently requires Python 3.14 or newer and uses [uv](https://docs.astral.sh/uv/) for environment management:

```bash
uv sync
```

Phase 1 does not require Mininet, root privileges, or a model provider.

## Usage

Validate the acceptance experiment:

```bash
uv run mininet-ai validate examples/phase1/experiment.yaml
```

Inspect its deployment plan as a table or JSON:

```bash
uv run mininet-ai plan examples/phase1/experiment.yaml
uv run mininet-ai plan examples/phase1/experiment.yaml --format json
```

Print one of the public JSON Schemas:

```bash
uv run mininet-ai schema experiment
uv run mininet-ai schema agent-blueprint
uv run mininet-ai schema capability
uv run mininet-ai schema deployment-plan
uv run mininet-ai schema runtime-event
```

Schemas are emitted as JSON Schema Draft 2020-12 documents. The deployment-plan
schema has the stable versioned identifier
`urn:mininet-ai:schema:v1alpha1:deployment-plan` and can be saved for external
validation or tooling:

```bash
uv run mininet-ai schema deployment-plan > deployment-plan.schema.json
```

The `v1alpha1` identifier denotes a specific machine-readable contract, even
while the project is in alpha. Compatible additions may retain it; changes that
invalidate existing documents or alter their compiled representation require a
new contract version. See [Compatibility and versioning](docs/compatibility.md)
for the complete rules and review checklist.

The example compiles one reusable blueprint into a singleton global agent, one controller-domain agent, two switch-local agents, and two host agents.

Phase 3 has a separate rootless acceptance example containing user-authored
telemetry and action plugins, a deterministic declarative agent, and an
out-of-scope action check:

```bash
uv run mininet-ai validate examples/phase3/experiment.yaml
uv run python -m examples.phase3
uv run pytest -q tests/acceptance/test_phase3.py
```

See [the Phase 3 example](examples/phase3/README.md) for its extension layout.

## Specification overview

An experiment declares its topology, reusable agent blueprints, capabilities, and concrete placements:

```yaml
apiVersion: mininet-ai/v1alpha1
kind: Experiment
metadata:
  name: distributed-routing

substrate:
  driver: fake
  topology:
    addressing:
      ipv4: {subnet: 10.0.0.0/24, strategy: sequential}
      mac: {prefix: "02:00:00", strategy: sequential}
    resources:
      - {name: network, kind: network}
      - name: c0
        kind: controller
        parent: network
        type: builtin
        port: 6653
      - name: s1
        kind: switch
        parent: network
        labels: {role: edge}
        failMode: secure
        controllers: [c0]
        protocols: [OpenFlow13]
        ports:
          - {name: s1-eth1, number: 1}
      - name: h1
        kind: host
        parent: network
        interfaces:
          - {name: h1-eth0, ipv4: auto, mac: auto}
    links:
      - name: h1-s1
        endpoints:
          - {node: h1, adapter: h1-eth0}
          - {node: s1, adapter: s1-eth1}
        bandwidth: 100
        delay: 2ms
        loss: 0

blueprints:
  - ./agent-blueprints/local-router.yaml

agents:
  - name: switch-router
    blueprint: local-router
    placement:
      layer: data
      targets:
        kind: switch
        matchLabels: {role: edge}
      cardinality: per-target
      runtime: device-sidecar
    observe: [ovs.port-counters, topology.neighbors]

coordination:
  mode: independent
```

IP addresses and MAC addresses are assigned to host interfaces. Links connect
concrete host interfaces and switch ports, and their bandwidth is expressed in
Mbps. An endpoint may omit `adapter`; the compiler will then allocate a stable
interface or port name and number before producing the deployment plan.

See [the complete Phase 1 example](examples/phase1/experiment.yaml) for external blueprints, typed capabilities, links, multiple layers, and safety policies.

Phase 4 continuous-runtime declarations are also part of the compiled contract.
An agent deployment can select manual, interval, and normalized event triggers,
bind aggregation and detector policies to its declared observations, and set
bounded queue, concurrency, overflow, and restart behavior:

```yaml
agents:
  - name: switch-router
    # placement, observe, and capabilities omitted
    triggers:
      - {type: manual, name: operator}
      - {type: interval, name: periodic-health, every: 5s}
      - type: event
        name: queue-alert
        event: queue.threshold-exceeded
        cooldown: 10s
    observationPolicies:
      - observation: tc.queue-occupancy
        every: 1s
        window: 10s
        aggregation: mean
        detectors:
          - type: threshold
            name: queue-high
            event: queue.threshold-exceeded
            path: queue.depth
            operator: gte
            value: 80
    execution:
      queueCapacity: 16
      maxConcurrency: 1
      overflow: coalesce
      restart: {policy: on-failure, maxAttempts: 3, backoff: 2s}
```

Blueprint memory is typed as local structured state, bounded conversation
history, and optional shared deployment/run scopes. Agno will implement local
session state and conversation history; learned memory across experiment runs
will be explicit and opt-in. Mininet AI will implement shared deployment/run
state because it participates in cross-agent coordination. Capability
definitions may declare typed postcondition observations and rollback timeouts.
At this stage the compiler validates and normalizes those declarations;
subsequent Phase 4 commits provide their runtime behavior.

## Development

Run the test suite:

```bash
uv run python -m unittest discover -v
```

Focused compiler suites under `tests/compiler` cover coordination expansion,
reference validation, resource graph cycles, instance limits, observer safety,
and external topology loading. The golden-plan test separately detects changes
to the complete compiled contract.

The code is organized by responsibility:

```text
mininet_ai/
├── agents/          # Agent orchestration; migrating to the Agno runtime
├── audit/           # Versioned runtime events, sinks, and decorators
├── specification/   # Versioned user-facing models and YAML loading
├── compiler/        # Specification to deterministic deployment plan
├── capabilities/    # Proposal authorization, validation, and execution
├── models/          # Deprecated transitional model adapters
├── plugins/         # Capability plugins and transitional provider registries
├── runtime/         # Continuous runtime events and bounded delivery
├── substrates/      # Substrate contracts and the Phase 1 fake driver
├── sdk/             # Mininet invocation, proposal, and capability contracts
├── transports/      # Bounded I/O shared by external adapters
└── cli.py            # compile-time and live-runtime commands
```

### Phase 2 development VM

Phase 2 uses a disposable Ubuntu VM because Mininet and Open vSwitch require
Linux networking privileges. Create or reprovision it from the host:

```bash
vagrant up --provision
```

Provisioning installs a pinned `uv`, Mininet, and Open vSwitch, then creates a
VM-local environment at `/home/vagrant/.venvs/mininet-ai`. The environment is
kept outside `/vagrant` so it never conflicts with the host's `.venv`, and it
includes Ubuntu's system packages so Python can import Mininet. Run project
commands inside the VM through the checked-in wrapper:

```bash
vagrant ssh
cd /vagrant
scripts/vm-run.sh python -m unittest discover -v
scripts/vm-run.sh mininet-ai validate examples/phase1/experiment.yaml
```

From the host, run the complete environment check with:

```bash
scripts/test-phase2-vm.sh
```

The check runs the project suite inside the VM, verifies Mininet/OVS access,
executes a `pingall` smoke test, and restores the clean networking baseline.
It invokes `mn -c`, so use it only with the disposable Phase 2 VM.

### Substrate contracts

Every substrate implements the versioned
`mininet-ai/substrate/v1alpha1` planning contract. A driver publishes a
manifest containing its supported resource kinds, attachment layers, runtimes,
and observations, and provides validators for options, compiled resources, and
agent bindings. The compiler resolves drivers through the public substrate
registry instead of importing a concrete implementation.

The fake driver is the reference implementation. New drivers should use
`ManifestSubstrateDriver` for the shared validation behavior, register a factory
with `register_substrate_driver`, and run the reusable
`tests.substrates.contract.SubstrateDriverContract` test mixin.

Stateful execution uses the separate
`mininet-ai/substrate-runtime/v1alpha1` lifecycle contract. Its five operations
are `deploy`, `inspect`, `observe`, `execute`, and `teardown`; deployment must
roll back on failure, and teardown must be idempotent and limited to resources
owned by the run. Runtime adapters register independently with
`register_substrate_runtime`. `FakeSubstrateRuntime` is the in-memory reference
adapter, and `tests.substrates.runtime_contract.SubstrateRuntimeContract`
provides reusable conformance tests. The Mininet/OVS implementation uses this
same interface without introducing privileged work into compilation.

`MininetOVSDriver` is the rootless, compiler-facing adapter for Phase 2. Select
it with `substrate.driver: mininet-ovs`. It validates the planned OVS bridges,
Linux interface names, OpenFlow port numbers, controller configuration, and
traffic-control parameters. `MininetOVSRuntime` then creates the accepted plan
with explicit controller assignments, OVS modes and protocols, interface
addresses and MTUs, and TC link shaping. Deployment rolls back on failure and
normal teardown is idempotent. It writes an atomic ownership record under
`/run/mininet-ai`, holds an exclusive process-lifetime lock, and rejects a new
deployment while a live or orphaned run exists. A fresh runtime can inspect an
orphan and recover it by calling `teardown` with the recorded run ID; recovery
targets only the processes, bridges, interfaces, and temporary files named by
that deployment plan. A bounded stopped-run record makes repeated teardown and
`stop` requests idempotent until the next deployment. See the
[Phase 2 acceptance experiment](examples/phase2/experiment.yaml).

The live runtime refreshes resource operational state during inspection and
normalizes all observations advertised by the driver: topology resources and
neighbors, controller events, OpenFlow flows, OVS port counters, traffic-control
queue state, host interfaces and processes, and active host reachability.
Observation targets are checked against the requested telemetry scope, and
command or parser failures return typed runtime errors. The runtime also
supports typed `link.enable`, `link.disable`, and `link.configure` mutations;
`openflow.flow.install` and `openflow.flow.remove`; and managed
`host.process.start` and `host.process.stop` operations. Action parameters and
target kinds are validated before mutation, link-state changes roll back a
partially updated endpoint, and processes started by a run are stopped during
teardown. Every operation returns a normalized succeeded, rejected, or failed
result and refreshes the live resource snapshot after success.

### Runtime CLI

Preview a deployment without requiring root or changing networking state:

```bash
mininet-ai run examples/phase2/experiment.yaml --dry-run
```

Live Mininet/OVS runs are foreground-owned so the process holding Mininet's
Python objects also owns cleanup. Start a run in one VM terminal and copy the
reported run ID:

```bash
sudo scripts/vm-run.sh mininet-ai run examples/phase2/experiment.yaml
```

Inspect or stop it from another VM terminal:

```bash
sudo scripts/vm-run.sh mininet-ai status <run-id>
sudo scripts/vm-run.sh mininet-ai topology <run-id>
sudo scripts/vm-run.sh mininet-ai stop <run-id>
```

Invoke one compiled agent from another terminal while its matching experiment
is running:

```bash
sudo scripts/vm-run.sh mininet-ai invoke experiment.yaml <run-id> \
  switch-router@s1 --intent "Inspect forwarding and repair it safely"
```

The command refuses a plan whose digest differs from the deployed run. It
prints a normalized `AgentInvocationResult` and appends prompts, token usage,
proposals, and action results to `.mininet-ai/audit.jsonl` by default. Use
`--format json`, `--audit-log PATH`, or `--model-endpoint URL` when needed.
OpenAI-compatible credentials are read from `OPENAI_API_KEY`; select another
environment variable with `--model-api-key-env`. Add `--discover-plugins` to
explicitly load installed provider entry points. Capabilities implemented by
the active substrate use provider `substrate.action` or
`substrate.observation`.

`status` and `topology` accept `--format json`. `stop` signals only the owner
whose PID, boot identity, and process start time match the protected run-state
record; the foreground owner then performs normal teardown. `Ctrl+C` in the
owner terminal follows the same path. If the owner has already crashed, `stop`
uses the recorded ownership data to recover only that run's resources.

### Golden deployment plans

The acceptance experiment has a canonical deployment plan under `tests/golden`.
Tests compare the complete compiled plan—including resolved resources, agent
instances, coordination, policies, normalized specification, and digest—against
this fixture. The source path is made repository-relative so the result is
stable across machines.

When an intentional compiler or schema change affects the plan, regenerate it
explicitly and review the resulting Git diff before committing:

```bash
uv run python -m tests.update_golden_plans
git diff -- tests/golden
```

The classification and migration requirements for such changes are defined in
[Compatibility and versioning](docs/compatibility.md).

### Mininet cleanup acceptance

Phase 2 development should run inside a disposable VM. Before starting an
experiment, capture its clean networking state:

```bash
sudo scripts/check-mininet-cleanup.sh snapshot
```

After both a normal teardown and a deliberately interrupted experiment, verify
that the machine returned to that baseline:

```bash
sudo scripts/check-mininet-cleanup.sh check
```

The live integration suite deliberately crashes a runtime owner and verifies
that a fresh runtime's `teardown(run_id)` restores the baseline. If targeted
recovery itself fails and leaves resources behind, restore the disposable VM
with the emergency cleanup path:

```bash
sudo scripts/check-mininet-cleanup.sh recover
```

`recover` invokes `mn -c`, which may remove every Mininet/OVS topology on the
machine; it is a test-environment fallback, not the runtime recovery mechanism.
Use it only in the isolated Phase 2 VM. The comparison covers OVS
bridges and ports, namespaces, veth and Mininet-style interfaces, Linux
bridges, qdiscs, Mininet/controller processes, runtime registry files, and
Mininet temporary files. Use `snapshot --force` only when intentionally
accepting a new clean baseline.

## Roadmap

### Phase 1: Specification and compiler

Define versioned experiment, agent, placement, capability, coordination, policy, and topology schemas. Validate and compile them into deterministic deployment plans using a fake substrate.

### Phase 2: Mininet and OVS substrate

Add deterministic network creation and teardown, resource discovery, normalized telemetry, and core OVS, OpenFlow, traffic-control, link, and host-process actions behind a substrate interface.

### Phase 3: User-defined agents and capabilities

Establish scoped invocation, capability, structured-action, policy, and plugin contracts for user-authored agents. The generic agent and model provider implementations created in this phase are transitional and will be replaced by Agno during Phase 4.

### Phase 4: Continuous runtime

Adopt Agno as the v1 agent runtime, using its model integrations, sessions, local state, memory, summaries, and usage metrics. Add event-driven agent lifecycles, triggers, scoped observations, Mininet-owned shared operational state, supervision, failure recovery, authorized action execution, and a persistent experiment run ledger.

### Phase 5: Coordination architectures

Support centralized, hierarchical, and peer-to-peer agent graphs, translating the canonical Mininet coordination plan into Agno teams or workflows where appropriate. Add message channels, intent routing, and Mininet-owned conflict arbitration for concurrent network actions.

### Phase 6: Placement and isolation

Turn logical placements into isolated processes, namespaces, containers, controller-side runtimes, host runtimes, and device-local sidecars with explicit privilege boundaries.

### Phase 7: Programmable targets and research harness

Add P4 and SmartNIC adapters, fault injection, workloads, replay, benchmark definitions, experiment comparisons, and reproducible evaluation reports.
