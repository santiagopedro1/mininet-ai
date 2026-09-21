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

The example compiles one reusable blueprint into a singleton global agent, one controller-domain agent, two switch-local agents, and two host agents.

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

The code is organized by responsibility:

```text
mininet_ai/
├── specification/   # Versioned user-facing models and YAML loading
├── compiler/        # Specification to deterministic deployment plan
├── substrates/      # Substrate contracts and the Phase 1 fake driver
└── cli.py            # validate, plan, and schema commands
```

### Substrate driver contract

Every substrate implements the versioned
`mininet-ai/substrate/v1alpha1` planning contract. A driver publishes a
manifest containing its supported resource kinds, attachment layers, runtimes,
and observations, and provides validators for options, compiled resources, and
agent bindings. The compiler resolves drivers through the public substrate
registry instead of importing a concrete implementation.

The fake driver is the reference implementation. New drivers should use
`ManifestSubstrateDriver` for the shared validation behavior, register a factory
with `register_substrate_driver`, and run the reusable
`tests.substrates.contract.SubstrateDriverContract` test mixin. The future
Mininet/OVS driver will implement this same planning contract before adding its
runtime lifecycle operations.

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
