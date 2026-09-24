# Mininet AI

Mininet AI is a declarative experiment compiler and multi-layer runtime for agentic networking research. Users choose where agents attach to an emulated network, what resources they can observe and change, where they execute, and how they coordinate.

The project is being built as an experiment framework rather than a collection of predefined agents. Experiments should be reproducible, inspectable, and portable across networking substrates.

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

The Phase 3 foundation adds the independent
`mininet-ai/agent-runtime/v1alpha1` SDK contract. It defines scoped invocation
context, model requests and responses, structured action proposals, normalized
invocation results, and provider protocols. An execution catalog resolves each
compiled agent to its normalized blueprint, capability definitions, and policy
without changing the deployment-plan format.

Agent, capability, and model adapters use explicit provider registries. Optional
packages expose versioned descriptors through the `mininet_ai.agents`,
`mininet_ai.capabilities`, or `mininet_ai.models` Python entry-point groups.
Discovery is opt-in and transactional: compiling or validating an experiment
does not import plugins, and a failed discovery leaves existing registrations
unchanged.

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

Built-in model adapters normalize OpenAI-compatible chat completions and
Ollama chat responses into the same `ModelResponse` contract. Calls are always
non-streaming and use the shared bounded HTTP transport. OpenAI-compatible
parameters are sent as top-level request fields; Ollama parameters are sent as
`options`. When a response schema is requested, the adapter sends the backend's
structured-output setting, parses strict JSON, and validates the result locally
before returning it. Credentials belong in adapter configuration or plugin
code, not experiment documents or endpoint URLs.

Declarative agent adapters turn a compiled blueprint and scoped `AgentContext`
into one structured model request, then require a valid `AgentResponse` before
any proposal reaches the capability engine. Python agents use an explicit
`module:callable` entrypoint with the same context and response contracts.
Entrypoints are imported only when their provider is constructed, never during
validation or compilation. They currently run as trusted code in the
orchestrator process; process and namespace isolation belong to Phase 6.

Audit decorators record agent invocations, complete model prompts and normalized
responses, token usage, action proposals, results, and typed failures as
versioned JSON events. The JSON Lines sink serializes concurrent appenders,
limits individual event size, and creates owner-only files. Audit records can
contain prompts and observations and must therefore be treated as sensitive
experiment artifacts. Writes are synchronous: failure to record a start event
prevents the wrapped operation from running instead of silently losing audit
coverage.

`OneShotAgentRuntime` connects those seams for manual invocations. It verifies
that the supplied deployment plan matches a running substrate, collects only
declared observations, invokes the selected agent and model providers, and
passes every proposal through capability authorization. Ollama,
OpenAI-compatible, deterministic mock, and substrate-backed providers are
built in; installed provider plugins are loaded only when explicitly enabled.

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
├── agents/          # Declarative and Python agent adapters
├── audit/           # Versioned runtime events, sinks, and decorators
├── specification/   # Versioned user-facing models and YAML loading
├── compiler/        # Specification to deterministic deployment plan
├── capabilities/    # Proposal authorization, validation, and execution
├── models/          # Normalized model-backend adapters
├── plugins/         # Explicit provider registries and entry-point discovery
├── substrates/      # Substrate contracts and the Phase 1 fake driver
├── sdk/             # Agent, model, and capability runtime contracts
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

Provide a stable SDK, capability registry, plugin loading, structured action proposals, policy enforcement, and adapters for user-authored declarative and code-based agents.

### Phase 4: Continuous runtime

Introduce event-driven agent lifecycles, triggers, scoped observations, memory, supervision, failure recovery, action execution, and a persistent experiment run ledger.

### Phase 5: Coordination architectures

Support centralized, hierarchical, and peer-to-peer agent graphs, message channels, intent routing, and conflict arbitration for concurrent actions.

### Phase 6: Placement and isolation

Turn logical placements into isolated processes, namespaces, containers, controller-side runtimes, host runtimes, and device-local sidecars with explicit privilege boundaries.

### Phase 7: Programmable targets and research harness

Add P4 and SmartNIC adapters, fault injection, workloads, replay, benchmark definitions, experiment comparisons, and reproducible evaluation reports.
