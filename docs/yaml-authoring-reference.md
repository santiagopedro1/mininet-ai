# YAML authoring reference

Use this reference when generating Mininet AI experiment YAML. The public
contract is `mininet-ai/v1alpha4`; unknown fields are rejected.

Compatible `v1alpha3` source documents emit deprecation warnings and normalize
to `v1alpha4` only under current validation rules. This applies to experiments
and embedded/external blueprints and capabilities, including mixed supported
versions. New plans always use `v1alpha4`. Original `v1alpha3` saved plans are
instead validated and reconstructed without rewriting their snapshots or digests
or reloading source files. Do not change archived plan headers. See
[compatibility and migration](compatibility.md#public-specification-v1alpha3-to-v1alpha4).

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

- Use `apiVersion: mininet-ai/v1alpha4` for newly authored documents.
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
   Declarative Ollama agents and the bundled prompt-parsed factory also support
   boolean `model.parameters.think`; `false` disables model thinking for bounded
   action workflows. Omit it to preserve Ollama's default. Python factories may
   supply other provider-specific configuration.

## Experiment

Minimal shape:

```yaml
apiVersion: mininet-ai/v1alpha4
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
apiVersion: mininet-ai/v1alpha4
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
apiVersion: mininet-ai/v1alpha4
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

### Shared model configurations

Instead of repeating inline model settings, multiple blueprints can select the
same named entry from a local YAML file. For example, `models.yaml`:

```yaml
models:
  local-reasoner:
    provider: ollama
    name: qwen3.5:latest
    parameters:
      host: http://ollama.example:11434
      think: false
```

In each blueprint:

```yaml
model:
  file: ./models.yaml
  entry: local-reasoner
```

The file is a plain object with only a root `models` mapping: no `apiVersion`,
`kind`, or `metadata` envelope. Every entry is a complete inline configuration
(`provider`, `name`, optional `parameters`). Configuration names such as
`local-reasoner` are 1–128 characters, begin with an ASCII letter, and continue
with ASCII letters, digits, underscore, dot, or hyphen. They are aliases, not
Provider model IDs: `name` still accepts IDs such as `qwen3.5:latest`.

Inline and reference forms are mutually exclusive. Both `file` and `entry` are
required for a reference. Entry-to-entry references, inheritance, and overrides
are not supported by this reference-only slice. A Python factory can still omit
`model` when it owns all model setup; references do not change that ownership.

Paths are relative to the **document containing the blueprint**: an external
blueprint's directory, or the experiment's directory for an embedded blueprint.
Absolute local paths and parent traversal (`../models.yaml`) are allowed. URLs,
tilde/environment expansion, discovery, and machine-wide registries are not.
Experiment-to-blueprint paths retain their existing experiment-relative meaning.
For Python-created experiments, pass an explicit source location with
`compile_experiment(resolve_experiment(experiment, source="project/experiment.py"))`
(`resolve_experiment` is in `mininet_ai.specification.loader`). Without an explicit
source, compilation uses the current directory's `experiment.py` fallback.

`validate` and `plan` read each resolved model file once per compilation, validate
the **whole file**, including unused entries, and reject duplicate YAML mapping
keys at every depth. Invalid entries cannot hide behind a selected valid entry.
Errors identify the referring blueprint, target file, entry, and field where
applicable, with numeric YAML locations rather than configuration-value dumps.
Later compilations read updated files; an already loaded experiment is not a
process-global configuration cache.

All entries undergo shared structural and credential-field checks. Construction-
specific restrictions apply to the selected complete base and its effective
configuration on the referring blueprint's ownership path. An unused entry has
no construction owner: a shared file may contain settings intended for custom
factories alongside settings intended for declarative agents or bundled factories.

Executable snapshots contain only effective `provider`/`name`/`parameters`, never
`file`/`entry`. Optional plan-level `modelProvenance` records the blueprint source,
resolved file, and configuration name separately from the snapshot digest.
Equivalent inline/reference settings in otherwise identical `v1alpha4`
experiments therefore have equal snapshots and digests. Saved plans reconstruct
and execute without rereading shared files, even after those files move or are
deleted. They still depend on runtime environment and installed Python factory
code. Consuming factories receive the resolved model configuration and remain
responsible for constructing their models; compilation never calls factories.

Keep credentials in the Provider's runtime environment, not shared files, inline
configuration, URLs, or provenance. Literal credential parameters (`api_key`,
`api-key`, `token`, `password`, `secret`, `credentials`) are unsupported, including
for custom factories; unrelated factory-owned parameters remain allowed. This
does not introduce project-specific credential-selector syntax or change Agno's
environment lookup (including `OLLAMA_API_KEY` and omitted-host behavior).
Compilation neither reads credential values nor contacts Providers.

### Runtime construction and ownership

On the active Agno runtime, an entrypoint uses `module:attribute` syntax and
must resolve to an Agno `Agent` or a factory returning one. Use a Python factory
for provider-specific options not supported declaratively. Declarative agents require
`model`. `reasoning.output-schema` is an optional legacy string identifier; the
active Agno path always requests the structured `AgentResponse` contract.

Ollama declarative agents and both bundled factories
(`mininet_ai.agents.agno.ollama_factory:create_prompt_parsed_agent` and
`create_deterministic_prompt_parsed_agent` in that module) support an optional
endpoint and boolean thinking mode:

```yaml
model:
  provider: ollama
  name: qwen3.5:latest
  parameters:
    host: http://ollama.example:11434
    think: false
```

`host` must be a non-empty HTTP/HTTPS URL with a hostname and no embedded
credentials. Invalid hosts fail during `validate` and `plan`, without deploying
a network or contacting Ollama. Explicit configuration overrides `OLLAMA_HOST`;
omitting the host preserves Agno/Ollama's environment and default behavior,
including its cloud endpoint behavior when an API key is configured. Each
blueprint can use a different endpoint; the runtime does not modify process
environment variables. Only `host` and `think` are supported for these Ollama
construction paths. `think` must be a boolean (not a string, number, or null);
omission leaves request parameters unset and explicit `false` is preserved.
Unsupported parameters and invalid values now fail during `validate` and `plan`.
Both bundled factories require an Ollama model; declarative agents require a model.
Custom Python factories may omit the model and retain their own parameter and
model-construction semantics. Compilation never imports or calls their entrypoints
or replaces factory-owned models. Shared structural checks, including explicit
Ollama host validation, still apply.

Keep credentials in the runtime environment, not YAML or URL user-info. There
are no project-defined credential-selector fields. Agno/Ollama performs its
existing `OLLAMA_API_KEY` lookup at runtime; a blank key remains valid for an
unauthenticated local endpoint. A nonempty key can select the cloud endpoint
even when `OLLAMA_HOST` is set; an explicit `host` takes precedence. Unsupported
literal credential fields (for example, `api_key`) are rejected by shared source
checks before plan persistence, including for custom factories. Custom
factory parameters are not recursively scanned or subject to declarative parameter
allowlists; factory authors must keep secrets out of their serialized parameters.

Other ordinary declarative providers still reject non-empty `model.parameters`.
These restrictions are checked during source compilation. Providers with empty
parameters remain delegated to Agno, without a compile-time Provider allowlist.
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
      sharedStateReads: []
      metadata: {}
    usage: {}
```

`mock` requires a valid `AgentResponse` in `response`, including an outcome
(message, proposal, delegation, or shared-state update). Optional `usage` must
be an object with the runtime metrics schema's nonnegative counts/cost, known
fields, and totals covering input plus output (including audio). Existing metric
coercion is preserved; validation does not normalize stored parameter values.
These checks use the same response/metrics schemas as runtime construction.

Compilation is offline: it does not construct Provider models or clients, contact
Providers, or read credential values. A successful plan does not establish remote
model availability, credentials, connectivity, or inference success. Saved plans
retain effective settings, not environment-derived values; execution still depends
on the runtime environment and installed factory code.

Memory rules:

- `learned.scope: run` isolates learned memory to one run; `agent` reuses it for
  the same compiled agent across runs.
- `learned.mode` is `automatic` or `agentic`.
- `shared.scopes` contains unique values from `deployment` and `run`.
- Declaring persistent memory requires an Agno session database at runtime.
- Shared updates commit atomically after all associated actions and required
  postconditions succeed. Rejected or failed actions publish no shared updates;
  arbitrary external changes are not rolled back by a failed state commit.
- The runtime binds omitted `expectedVersion` values to the invocation's shared
  snapshot. Stale decisions are rejected rather than overwriting newer state.
  Direct shared-store callers must supply `expectedVersion` when updating an
  existing key (`0` means the key must be absent).
- Updated keys are decision dependencies automatically. Responses must declare
  other shared snapshot keys a decision relies on in `sharedStateReads`, as
  `{scope: run, key: route}` entries (including dependencies on absent keys).
  These reads are version-checked and reserved against incompatible commits;
  merely receiving a snapshot does not lock unrelated keys.

See [ADR 0008](adr/0008-shared-operational-state-and-action-commitment.md) for
commitment and dependency-protection limits, and
[ADR 0010](adr/0010-agent-sessions-learned-memory-and-run-evidence.md) for learning
identity and evidence boundaries.

### Invocation concurrency

Independent requests and sibling delegations can overlap under the existing
`max-concurrent-invocations` and agent `maxConcurrency` limits. These budgets
count actual agent invocations, including delegates, not parents waiting for
descendants. A one-slot experiment remains serial. A child begins only after its
parent completes successfully and publishes eligible shared state.

Stateless agents with `maxConcurrency > 1` use invocation-isolated Agno instances
and may complete out of order. Access to one persistent Agno session remains
serialized because its local/history/learned memory is shared mutable state.
This serializes Agno access, not entire invocations: network phases can overlap,
and later reasoning can precede earlier action-result persistence. See
[ADR 0009](adr/0009-invocation-budgets-and-session-serialization.md) for the precise
budget and ordering guarantees.

Conflicting actions are protected throughout execution and postcondition checks,
across requests as well as within a request. `reject` rejects incompatible work;
`serialize` waits; `priority` orders pending conflicts without preempting running
actions. Independent effects/targets and shared-state keys can proceed together.
Model-driven observers and passive telemetry can see intermediate network state;
observations are not promised a transactionally consistent network snapshot.

## Capability

```yaml
apiVersion: mininet-ai/v1alpha4
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
- Built-in action effects also determine conflict admission. Missing effects and
  custom providers are conservatively treated as unknown mutations, not as
  read-only work. The built-in `substrate.observation` provider is observation-only
  and still goes through normal proposal validation and execution.
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
