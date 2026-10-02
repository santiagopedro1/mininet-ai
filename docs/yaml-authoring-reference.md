# YAML authoring reference

Use this reference when generating Mininet AI experiment YAML. The public
contract is `mininet-ai/v1alpha3`; unknown fields are rejected.

The authoritative definitions are the Pydantic models in
`mininet_ai/specification/models.py`. Before returning generated files, always
run `validate`, then `plan`. A valid schema can still fail compilation when
references, placement, substrate support, or scopes are inconsistent.

```bash
uv run mininet-ai validate path/to/experiment.yaml
uv run mininet-ai plan path/to/experiment.yaml
```

Machine-readable schemas are available for the three authorable, versioned
input document types:

```bash
uv run mininet-ai schema experiment
uv run mininet-ai schema agent-blueprint
uv run mininet-ai schema capability
```

The CLI also emits schemas for runtime output contracts (`deployment-plan`,
`runtime-event`, `coordination-message`, and `coordination-outcome`); those are
not input YAML documents.

## File graph

An experiment is the root document. It may contain its topology, blueprints,
and capabilities inline, or reference separate YAML files:

```text
experiment.yaml
├── topology.yaml                         # optional external Topology object
├── agent-blueprints/<name>.yaml          # AgentBlueprint document
└── capabilities/<name>.yaml              # Capability document
```

All relative paths are resolved from the directory containing
`experiment.yaml`, not from the process working directory. The four YAML shapes
accepted by the loader are:

1. `Experiment`
2. `AgentBlueprint`
3. `Capability`
4. an external `Topology` object

Only the first three have `apiVersion`, `kind`, and `metadata`.

## Common rules

- Use `apiVersion: mininet-ai/v1alpha3` exactly.
- Names start with a letter, contain only letters, digits, `_`, `.`, or `-`, and
  are at most 128 characters.
- Metadata supports `name`, optional `description`, and optional string
  `labels`.
- Durations are non-negative numbers followed by `us`, `ms`, or `s`, such as
  `250ms`, `5s`, or `1.5s`. Fields representing active timeouts or intervals
  generally require a value greater than zero.
- Preserve the field spelling shown here. The contract intentionally mixes
  camel case (`apiVersion`, `matchLabels`) and kebab case (`input-schema`,
  `max-instances`).
- References use metadata names. An agent's `blueprint` names a loaded
  blueprint; entries in `capabilities` name loaded capability definitions.
- Keep credentials outside YAML. Agno providers read their standard environment
   variables; Ollama endpoints can also be set through `model.parameters.host`.
   Python factories may supply other provider-specific configuration.

## Experiment

Minimal shape:

```yaml
apiVersion: mininet-ai/v1alpha3
kind: Experiment
metadata:
  name: example

substrate:
  driver: fake                 # fake or mininet-ovs
  topology:
    resources:
      - {name: network, kind: network}
    links: []
  options: {}

blueprints:
  - ./agent-blueprints/operator.yaml

capabilityDefinitions:
  - ./capabilities/inspect.yaml

agents:
  - name: operator
    blueprint: operator
    placement:
      layer: global
      targets: {kind: network, names: [network]}
      cardinality: singleton
      runtime: orchestrator
    observe: [topology.resources]
    capabilities: [topology.inspect]

coordination: {mode: independent}
policies:
  conflicting-actions: reject
  require-postcondition-check: false
resourceLimits:
  max-instances: 256
  max-concurrent-invocations: 32
  max-queued-events: 4096
```

`blueprints` and `capabilityDefinitions` accept either file paths or complete
inline objects. `substrate.topology` accepts either a file path or an inline
Topology object.

### Topology

An external topology file contains the value that would otherwise appear below
`substrate.topology`; it has no document header:

```yaml
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
    failMode: secure
    datapath: kernel
    controllers: [c0]
    protocols: [OpenFlow13]
    ports:
      - {name: s1-eth1, number: 1, mtu: 1500}
  - name: h1
    kind: host
    parent: network
    interfaces:
      - {name: h1-eth0, ipv4: auto, mac: auto, mtu: 1500}

links:
  - name: h1-s1
    labels: {purpose: access}
    attributes: {}
    endpoints:
      - {node: h1, adapter: h1-eth0}
      - {node: s1, adapter: s1-eth1}
    bandwidth: 100             # Mbps
    delay: 1ms
    jitter: 0ms
    loss: 0                    # percent, 0..100
    maxQueueSize: 1000
```

Declarable resource kinds and their additional fields:

| `kind` | Fields |
| --- | --- |
| `network`, `region`, `flow` | Common resource fields only |
| `controller` | required `type: builtin\|remote`; optional `address`, `protocol: tcp\|ssl`, `port` |
| `controller-domain` | non-empty `controllers` |
| `switch` | optional `dpid`; `failMode: secure\|standalone`, `datapath: kernel\|userspace`, `controllers`, `protocols`, `ports` |
| `host` | `interfaces`, optional `defaultRoute` |

Every resource also accepts optional `parent`, string `labels`, and free-form
`attributes`. A remote controller requires `address`. Host interface `ipv4` is
an address with prefix, `auto`, or `none`; `mac` is `auto` or a unicast MAC.
Port numbers may be positive integers or `auto`. A link has exactly two
different node endpoints; omit `adapter` to request deterministic allocation.

Switch `dpid` is an optional non-zero string of 1–16 hexadecimal digits, for
example `dpid: "abc"`. It is normalized to 16 lowercase digits. Without an
override, canonical `sN` switches retain their numeric DPID; other names receive
a deterministic generated DPID. Fixed IDs must be unique, and generated IDs
skip collisions. Deployment plans always include the resolved switch `dpid`.
Noncanonical names such as `edge-sw` are supported, subject to the Mininet/OVS
15-byte limit for switch and interface names (including generated suffixes).

### Agent deployments

Each item in `agents` deploys a loaded blueprint:

```yaml
- name: switch-operator
  blueprint: operator
  placement:
    layer: data
    targets:
      kind: switch
      names: [s1]
      matchLabels: {role: edge}
    cardinality: per-target
    runtime: device-sidecar
  observe: [openflow.flows, ovs.port-counters]
  capabilities: [openflow.flow.install]
  priority: 0
  triggers:
    - {type: manual, name: operator}
    - {type: interval, name: periodic, every: 5s, initialDelay: 0s}
    - type: event
      name: congestion
      event: queue.threshold-exceeded
      source: telemetry
      subject: s1
      cooldown: 10s
  execution:
    queueCapacity: 64
    maxConcurrency: 1
    overflow: reject          # reject, drop-oldest, or coalesce
    actionTimeout: 30s
    restart:
      policy: on-failure      # never or on-failure
      maxAttempts: 3
      backoff: 1s
```

Placement fields:

- `layer`: `global`, `management`, `control`, `data`, `host`, `observer`, or
  `custom`.
- `custom-layer`: required only for `layer: custom`.
- `targets.kind`: any resource kind; `names` and `matchLabels` narrow matches.
- `cardinality`: `singleton`, `per-target`, or `per-group`.
- `groupBy`: required only for `per-group`.
- `runtime`: must be supported by the selected substrate, layer, and target.

For both built-in substrates, common layer/runtime pairs are:

| Layer | Typical target | Runtimes |
| --- | --- | --- |
| `global` | `network` | `orchestrator`, `process`, `container` |
| `management` | network, region, controller/domain | `orchestrator`, `process`, `container` |
| `control` | controller/domain, switch | `controller-sidecar`, `process`, `container` |
| `data` | switch, port, link, flow | `device-sidecar`, `process`, `container` |
| `host` | host | `host-namespace`, `process`, `container` |
| `observer` | any | `orchestrator`, `process`, `container` |

The compiler verifies the exact combination against the driver's manifest.
Only the `fake` driver supports declaring custom layers in YAML:

```yaml
substrate:
  driver: fake
  options:
    custom-layers:
      - name: research-plane
        targets: [switch]
        runtimes: [process]
        observations: [topology.neighbors]
```

Each custom layer requires non-empty `targets` and `runtimes`; `observations`
may be omitted. A placement then uses `layer: custom` and
`custom-layer: research-plane`.

### Observation policies

An observation policy samples one entry already declared in the agent's
`observe` list:

```yaml
observationPolicies:
  - observation: tc.queue-occupancy
    every: 1s
    window: 10s
    aggregation: mean         # latest, minimum, maximum, mean, or sum
    detectors:
      - type: threshold
        name: queue-high
        event: queue.threshold-exceeded
        path: queue.depth
        operator: gte         # gt, gte, lt, lte, eq, or ne
        value: 80
        cooldown: 10s
      - type: anomaly
        name: queue-anomaly
        event: queue.anomaly
        path: queue.depth
        method: zscore
        sensitivity: 3
        minSamples: 10
        cooldown: 10s
```

`window` must be at least `every`. Detector names must be unique within the
policy. Trigger names must be unique across all trigger types within an agent.

### Coordination

Use exactly the fields belonging to the selected mode:

```yaml
# Independent agents
coordination: {mode: independent}

# One singleton deployment receives external intents
coordination:
  mode: centralized
  coordinator: coordinator-deployment

# Directed deployment relationships
coordination:
  mode: hierarchical
  relationships:
    - {source: parent-deployment, targets: [child-deployment]}

# Peer graph
coordination:
  mode: distributed
  peers: topology-neighbors   # or all
```

Coordination names refer to agent deployment names, not expanded instance IDs.

## AgentBlueprint

Declarative Agno agent:

```yaml
apiVersion: mininet-ai/v1alpha3
kind: AgentBlueprint
metadata:
  name: operator
  description: Inspects and safely repairs one scoped network target.

implementation:
  type: declarative

model:
  provider: ollama
  name: qwen3.5:latest

reasoning:
  instructions: |-
    Inspect the supplied context. Return a structured AgentResponse and propose
    only capabilities assigned to this agent.
  output-schema: AgentResponse
  timeout: 120s

memory:
  local: {maxEntries: 1000}
  conversation: {maxMessages: 50, summaries: false}
  learned: {scope: run, mode: automatic}
  shared: {scopes: [deployment, run], maxEntries: 1000}

loop:
  phases: [observe, reason, propose]
```

Python-authored Agno agent or factory:

```yaml
apiVersion: mininet-ai/v1alpha3
kind: AgentBlueprint
metadata: {name: operator}
implementation:
  type: python
  entrypoint: my_package.agents:create_operator
model:
  provider: ollama
  name: qwen3.5:latest
reasoning:
  timeout: 120s
```

On the active Agno runtime, an entrypoint uses `module:attribute` syntax and
must resolve to an Agno `Agent` or a factory returning one. Use a Python factory
for provider-specific options not supported declaratively. Declarative agents require
`model`. `reasoning.output-schema` is an optional legacy string identifier; the
active Agno path always requests the structured `AgentResponse` contract.

Ollama declarative agents and the bundled
`mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent` factory support
an optional endpoint:

```yaml
model:
  provider: ollama
  name: qwen3.5:latest
  parameters:
    host: http://ollama.example:11434
```

`host` must be a non-empty HTTP/HTTPS URL with a hostname and no embedded
credentials. Invalid hosts fail during `validate` and `plan`, without deploying
a network or contacting Ollama. Explicit configuration overrides `OLLAMA_HOST`;
omitting the host preserves Agno/Ollama's environment and default behavior,
including its cloud endpoint behavior when an API key is configured. Each
blueprint can use a different endpoint; the runtime does not modify process
environment variables. Only `host` is supported for these Ollama construction
paths; other parameters are rejected at agent construction. Custom Python
factories remain responsible for consuming their own configuration. Keep
credentials in environment variables, not YAML or URL user-info.

Other ordinary declarative providers still reject non-empty `model.parameters`.
The deterministic `mock` provider accepts its existing response and usage
parameters and is useful for offline generated experiments (see
[Getting Started](../examples/getting-started/README.md) for a complete
mock-provider experiment):

```yaml
model:
  provider: mock
  name: deterministic
  parameters:
    response:
      message: No action required.
      proposals: []
      delegations: []
      sharedStateUpdates: []
      metadata: {}
    usage: {}
```

Memory rules:

- `learned.scope: run` isolates learned memory to one run; `agent` reuses it for
  the same compiled agent across runs.
- `learned.mode` is `automatic` or `agentic`.
- `shared.scopes` contains unique values from `deployment` and `run`.
- Declaring persistent memory requires an Agno session database at runtime.

## Capability

```yaml
apiVersion: mininet-ai/v1alpha3
kind: Capability
metadata:
  name: openflow.flow.install
  description: Install one scoped OpenFlow rule.

targets: [switch]
layers: [control, data]
effects: [network.forwarding.write]
provider: substrate.action

input-schema:
  type: object
  required: [match, actions]
  properties:
    match: {type: object}
    actions: {type: array, items: {type: string}}
  additionalProperties: false

output-schema:
  type: object

reversible: true
rollback: {timeout: 30s}
postconditions:
  - observation: openflow.flows
    path: flows.0.state
    operator: eq
    expected: installed
    timeout: 5s
    interval: 250ms
```

- `targets` is a non-empty list of resource kinds.
- `layers` is a non-empty list and must match the assigned agent placement.
- `effects` contributes to the compiled least-privilege set.
- `input-schema` and `output-schema` are JSON Schema objects.
- `provider` names a registered capability provider. The runtime registers
  `substrate.action` and `substrate.observation`; plugins may register others.
- A `rollback` block requires `reversible: true`.
- Postcondition `operator` is `eq`, `ne`, `gt`, `gte`, `lt`, or `lte`.
- Postcondition `path` is relative to the observation target and may traverse
  object fields and list indexes.
- Postconditions execute only when the experiment policy
  `require-postcondition-check` is enabled.

See [Autonomous Network Operation](../examples/autonomous-operation/README.md)
for a complete example with rollback and postconditions.

## Built-in observations

Observation availability depends on substrate and placement layer. The
`mininet-ovs` driver advertises:

| Layer | Observations |
| --- | --- |
| `global` | `topology.resources`, `topology.neighbors`, `host.reachability` |
| `management` | `topology.resources`, `topology.neighbors`, `controller.events` |
| `control` | `topology.resources`, `topology.neighbors`, `controller.events`, `openflow.flows` |
| `data` | `topology.neighbors`, `openflow.flows`, `ovs.port-counters`, `tc.queue-occupancy` |
| `host` | `topology.neighbors`, `host.interfaces`, `host.processes`, `host.reachability` |
| `observer` | union of all observations above |

The `fake` driver is similar, but global and host layers omit
`host.reachability`, and the data layer additionally supports
`packets.samples`. Let compiler validation decide rather than copying an
observation across incompatible layers.

## Generation checklist

Before considering generated YAML complete:

1. Account for every intended topology node, interface/port, and link.
2. Ensure every parent, controller, endpoint, selector name, blueprint,
   capability, observation, and coordination deployment reference resolves.
3. Ensure placement layer, target kind, runtime, and observations form a
   substrate-supported combination.
4. Ensure each capability permits the agent's layer and selected target kind.
5. Ensure trigger names and detector names are unique in their scopes.
6. Keep secrets and machine-specific model credentials out of YAML.
7. Run `validate` and fix every issue.
8. Run `plan` and inspect expanded agent IDs, resources, privileges,
   coordination edges, and the normalized specification.

The maintained complete example is
`examples/iperf-throughput/experiment.yaml` with adjacent blueprint and
capability files.

## See also

- [Getting Started](../examples/getting-started/README.md) — the simplest
  possible experiment; one agent, one capability, no external services.
- [Autonomous Network Operation](../examples/autonomous-operation/README.md) —
  event triggers, detectors, rollback, and shared state.
- [Hierarchical Routing Coordination](../examples/hierarchical-routing/README.md) —
  multi-layer placement and hierarchical coordination.
- [Examples index](../examples/README.md) — feature matrix and progression
  guide.
