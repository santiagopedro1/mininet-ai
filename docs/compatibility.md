# Compatibility and versioning

Mininet AI versions its machine-readable contracts independently from the
Python package. The package version in `pyproject.toml` describes a software
release; it does not replace the version carried by a document or substrate
driver contract.

## Versioned contracts

The project currently defines ten contract families:

- `mininet-ai/v1alpha3` covers `Experiment`, `AgentBlueprint`, and `Capability`
  documents, plus the `DeploymentPlan` produced by the compiler. The deployment
  plan JSON Schema uses the matching identifier
  `urn:mininet-ai:schema:v1alpha3:deployment-plan`.
- `mininet-ai/substrate/v1alpha1` covers the compile-time interface implemented
  by substrate drivers. It is versioned separately because driver integration
  can evolve without changing experiment documents.
- `mininet-ai/substrate-runtime/v1alpha2` covers the stateful lifecycle
  interface implemented by executable substrate adapters: deploy, inspect,
  observe, execute, and teardown. It is separate from the planning contract so
  compiling an experiment never requires privileged networking access.
- `mininet-ai/runtime-state/v1alpha2` covers the private, on-host ownership
  record used to inspect and recover an interrupted Mininet/OVS run. It is
  versioned so a newer runtime never guesses how to clean up an incompatible
  record.
- `mininet-ai/agent-runtime/v1alpha3` covers the current SDK seam used by agent,
  capability, and model-provider adapters, including scoped invocation context,
  action proposals, normalized model responses, invocation results, versioned
  provider descriptors, registry semantics, and the JSON protocol used by
  external capability processes and services. Its generic agent and model
  provider portions are deprecated by
  [ADR 0001](adr/0001-use-agno-as-v1-agent-runtime.md) and will be replaced by
  an Agno-centric runtime contract before v1. Scoped invocation, delegation,
  action, result, and external capability concepts remain owned by Mininet AI.
  The agent-runtime contract is independent of experiment and substrate
  contract versions.
- `mininet-ai/audit/v1alpha1` covers correlated JSON audit records for agent,
  model, and capability execution. It is versioned separately so storage and
  analysis tools can evolve without changing provider contracts.
- `mininet-ai/runtime-event/v1alpha1` covers normalized continuous-runtime
  events, their ordering and causation, and typed payloads. Its schema identifier
  is `urn:mininet-ai:schema:v1alpha1:runtime-event`.
- `mininet-ai/coordination-message/v1alpha1` covers immutable, correlated
  intent, delegation, and result envelopes accepted for at-most-once delivery
  within one coordination runtime process. Its schema identifier is
  `urn:mininet-ai:schema:v1alpha1:coordination-message`.
- `mininet-ai/coordination-outcome/v1alpha1` covers the ordered messages,
  invocations, arbitration decisions, and issues produced for one coordinated
  request. Continuous invocation records may carry this outcome alongside
  their representative root invocation. Its schema identifier is
  `urn:mininet-ai:schema:v1alpha1:coordination-outcome`.
- `mininet-ai/run-ledger/v1alpha1` covers immutable run manifests and ordered
  ledger records. Its SQLite schema version is validated independently from the
  serialized record contract.

The `alpha` label means that breaking revisions are expected before the
contract is declared stable. It does not mean that the meaning of an existing
version may change silently. A consumer can use the version field or schema ID
to select the exact contract it understands.

### Planned Agno runtime migration

The framework-neutral `AgentProvider` and `ModelProvider` protocols, their
provider registries, and the built-in OpenAI-compatible and Ollama model
adapters are deprecated. They remain documented as current `v1alpha3` behavior
until the Agno execution path reaches parity. Their removal or semantic
replacement will use a new agent-runtime contract version rather than silently
changing `mininet-ai/agent-runtime/v1alpha3`.

The migration does not delegate network authority to Agno. Mininet's scoped
invocation context, structured action proposal, capability authorization,
action result, runtime event, and ledger records remain Mininet-owned
contracts. Agno run and session data will be translated into those records at
the agent-runtime seam.

### Public specification v1alpha1 to v1alpha2 (historical)

`v1alpha2` adds triggers, observation policies, memory, execution controls,
resource limits, postconditions, and rollback declarations. Those fields also
change the deployment-plan shape, normalized snapshot, and digest, so the
public contract and deployment-plan schema identifier advance together.

The compiler no longer accepts `mininet-ai/v1alpha1` documents. Change the
`apiVersion` on an experiment and every referenced blueprint and capability to
`mininet-ai/v1alpha2`, then regenerate and review its deployment plan. Defaults
preserve manual one-shot behavior, but the serialized plan and digest
intentionally change.

### Public specification v1alpha2 to v1alpha3

`v1alpha3` adds an optional switch `dpid` input and a required, resolved `dpid`
on every planned switch. The compiler preserves numeric DPIDs for canonical
`sN` names, generates deterministic non-zero 64-bit IDs for other switch names,
and normalizes explicit IDs to 16 lowercase hexadecimal digits. Fixed IDs must
be unique; generated IDs skip collisions in stable switch-name order. This
enables noncanonical Mininet switch names without relying on Mininet's
name-derived DPID.

Even experiments omitting the new field produce different serialized plans,
normalized snapshots, and digests. The shared public contract and deployment-plan
schema therefore advance together to `mininet-ai/v1alpha3` and
`urn:mininet-ai:schema:v1alpha3:deployment-plan`. This is not a compatible
extension of `v1alpha2`.

To migrate:

1. Stop active runs using the old software before upgrading. Their persisted
   ownership records embed old deployment plans; the new software rejects those
   plans even though the runtime-state envelope version is unchanged. Stopped-run
   records also retain old plans, so the new `status` and `topology` commands
   cannot inspect those snapshots. Capture any needed reports with the old
   software before upgrading; stopping a run does not migrate its saved plan.
2. Change `apiVersion` to `mininet-ai/v1alpha3` on the experiment and every
   agent blueprint and capability, including inline definitions and external
   YAML files. Unversioned topology files need no header change. Python-authored
   documents must use the new version too.
3. Optionally set `dpid: "abc"` on switches whose identity must be pinned.
   Otherwise leave the input field omitted for automatic allocation.
4. Run `validate` and `plan` again; regenerate saved plans and golden fixtures.
   Do not just change a saved plan's version header: recompilation supplies the
   required DPIDs and recomputes its snapshot and digest.
5. Update downstream plan validators to the new schema identifier. Old run
   ledger history remains stored, but interpreting archived plans requires their
   original schema; no automatic migration of stored records is provided.

`Experiment`, `AgentBlueprint`, `Capability`, and `DeploymentPlan` accept only
`mininet-ai/v1alpha3`; `v1alpha1` and `v1alpha2` documents are rejected, not
silently upgraded. The package version and independently versioned substrate,
runtime-state, agent-runtime, audit, event, coordination, and ledger contracts
are unchanged.

### Agent runtime v1alpha1 to v1alpha2

`v1alpha2` adds scoped shared-state input and updates, committed state changes,
and invocation timings. Capability, agent, and model plugins must advertise
`mininet-ai/agent-runtime/v1alpha2`; incompatible `v1alpha1` plugins are
rejected before invocation.

### Agent runtime v1alpha2 to v1alpha3

`v1alpha3` adds optional coordination context to each invocation and structured
delegation proposals to agent responses. Existing agents that do not delegate
need no source changes, but provider plugins must advertise
`mininet-ai/agent-runtime/v1alpha3`; incompatible `v1alpha2` plugins are
rejected before invocation. A delegation is only a proposal: Mininet AI still
validates its destination against the compiled coordination graph before
delivery.

An existing response remains valid after adding the new defaulted field:

```json
{"message": "done", "proposals": []}
```

A coordinating agent may now return an explicit proposal:

```json
{
  "delegations": [
    {
      "id": "delegate-1",
      "targetAgentId": "router@s2",
      "intent": "Inspect the adjacent switch",
      "metadata": {}
    }
  ]
}
```

Coordinated invocations also receive the optional `coordination` object with
the current message identity, requested agent, hop count, and graph-authorized
`allowedDestinations`. Non-coordinated callers continue to omit that object.

### Substrate runtime v1alpha1 to v1alpha2

`v1alpha2` adds postcondition and rollback results plus action-effect latency to
`ActionResult`. Runtime adapters must advertise
`mininet-ai/substrate-runtime/v1alpha2`; incompatible `v1alpha1` adapters are
rejected by the runtime registry.

### Runtime-state v1alpha1 to v1alpha2

`v1alpha2` adds a separate, bounded stopped-run record so repeated `stop` and
later `status` calls remain idempotent across CLI processes. The active-run
record keeps the `v1alpha1` shape; readers continue to accept it and rewrite it
as `v1alpha2` on the next state update. Stopped-run records exist only in
`v1alpha2` and are discarded when the next deployment is claimed.

Old active records therefore need no manual conversion. Operators should use
the normal targeted `stop RUN_ID` recovery before upgrading when practical;
if an old active record remains, the new runtime can inspect and recover it
using its recorded owner, plan, and process groups.

This compatibility applies to the ownership envelope, not to the embedded
deployment plan. When upgrading the public specification version, follow its
migration guide and stop old runs with the old software first.

## Changes allowed within a contract version

### Cross-terminal CLI intent submission

`invoke RUN_ID AGENT_ID --intent TEXT` submits a manual event to
the existing foreground `run` owner through a private local socket. It no longer
constructs a separate one-shot agent runtime. Its JSON output is the accepted
`RuntimeEvent`, not an `AgentInvocationResult`; exit status zero means accepted,
not successfully executed. Execution results remain in the owner's ledger and
log. The SDK's `OneShotAgentRuntime` is unchanged.

The experiment-path positional argument has been removed. Update existing
scripts from `invoke EXPERIMENT RUN_ID AGENT_ID --intent TEXT` to
`invoke RUN_ID AGENT_ID --intent TEXT`. The client no longer reads or compiles
YAML: the live owner uses its original compiled plan to authorize agent IDs and
require a manual trigger. Editing the input files cannot change that plan.

Use `agents RUN_ID` to discover the owner's compiled instance IDs and manual
intent eligibility, or `agents RUN_ID --format json` for `runId`, `planDigest`,
`agents`, and `manualAgents`. Discovery is read-only and requires a live owner;
it does not read archived ledger or substrate ownership records. Both commands
support `--control-dir` and `--timeout`.

The internal socket accepts submit and describe requests. A submit request may
omit `planDigest`; when supplied it is still checked against the owner. Legacy
requests without a request kind are interpreted as submissions. Run identity,
private-directory ownership, socket ownership, and peer-user checks remain in
force. This change does not alter any versioned public document contract.

The old `invoke --audit-log`, `--agno-db`, `--shared-state-db`, and
`--discover-plugins` options remain accepted but are deprecated and ignored with
a warning on stderr: session, state, audit, and plugin configuration belongs to
the owner. Set database and plugin options on `run`.
Use the same OS user on the same machine. Defaults are working-directory
independent: root uses `/run/mininet-ai/control`, non-root uses a validated
`$XDG_RUNTIME_DIR/mininet-ai/control` or `/tmp/mininet-ai-<uid>/control` if unset.
Explicit `--control-dir` is a base override; relative paths follow the command's
working directory, so absolute paths are recommended. The endpoint is now
`<base>/<run-id-hash>/control.sock`. Restart old owners after upgrading: there is
no implicit project-local fallback, and a new client cannot address an old flat
endpoint even using `--control-dir`. Old owners require old compatible clients
and their explicit legacy directory. Existing run directories/endpoints are
never taken over, even if apparently stale; stop the owner or use a new run ID.
The private socket protocol is internal and
does not change the versioned runtime-event or agent-runtime contracts.

### Contract-preserving changes

Run identity reservation is a composition-layer change for the fake and
Mininet/OVS adapters using their existing `run_id_factory` constructors. The
runtime/provider protocols and all serialized contract versions are unchanged.
The reserved identity is used unchanged for deployment, ledger, and sessions.
Registered third-party adapters retain their zero-argument factory/runtime
contracts and can still execute with explicit `--log-file`, `--ledger-db`,
`--shared-state-db`, and `--agno-db`. Automatic per-run defaults are currently
limited to the two built-ins: assigning identities to other adapters would
require a separately reviewed reservation contract, not an implicit protocol
change. Missing file options fail before deployment for such adapters.
Saved output defaults now use `.mininet-ai/<run-id>/logs` and `dbs`; explicit
file options still take precedence, and existing artifacts remain untouched.
`--artifact-root` is independent of live endpoint discovery. A failed start
retains diagnostic output rather than renaming or deleting active SQLite files.
Agno currently routes sessions and learned memory through one `Agent.db`; when
agent-scoped memory is requested, that entire store defaults to the stable
`<artifact-root>/memory/agno.sqlite3`. Run-qualified session IDs isolate sessions;
agent-qualified user IDs retain learned memory. An explicit shared `--agno-db`
also preserves memory; separate per-run shared-state databases remain isolated.
If a legacy `<artifact-root>/agno.sqlite3` exists and agent-scoped memory is
requested, choose `--agno-db` explicitly. Point it at the legacy store to retain
learning; no automatic migration or silent switch to empty memory is performed.

Ollama endpoint configuration uses the existing `model.parameters.host` key,
without adding serialized fields or changing omitted-host plans, snapshots,
digests, or schemas. The public contract stays at `mininet-ai/v1alpha3`.
Explicit host values are validated as HTTP/HTTPS URLs without credentials and
are now honored by declarative agents and the bundled prompt-parsed factory.
The runtime does not mutate environment variables; other Python factories
continue to interpret their own provider-specific options.

A change may keep the current contract version when it does not alter the
accepted meaning or serialized result of an existing valid document. Examples
include:

- documentation, examples, diagnostics, and human-readable CLI formatting;
- internal refactoring, performance work, or additional tests;
- accepting a new optional input field whose default preserves the previous
  compiled plan for documents that omit it;
- accepting additional input syntax that normalizes to an already-supported
  value; and
- fixing validation so that input already prohibited by the published schema
  is rejected consistently.

These changes still require tests. In particular, an additive input must have a
focused test for its default and explicit forms.

## Changes that require a new contract version

Use a new contract version when a change can make
an existing valid document fail, change its meaning, or change its observable
compiled representation. This includes:

- removing or renaming a field, document kind, resource kind, enum value, or
  serialized alias;
- changing a field's type, requiredness, default, constraints, or semantics;
- adding a required field;
- changing placement expansion, resource allocation, ordering, coordination,
  privilege calculation, snapshot normalization, or digest calculation for an
  existing valid experiment; and
- adding, removing, or changing a deployment-plan field or emitted resource
  variant, even when the JSON Schema change would otherwise look additive.

Deployment plans are treated strictly because downstream tools may validate
their complete shape and because their normalized snapshot contributes to the
digest. A deliberate bug fix that changes a plan for previously valid input is
therefore a contract change and needs a new version plus migration guidance.

Changes to the planning driver protocol or the meaning of its manifest require
a new substrate contract version.
Changes to runtime operations, lifecycle semantics, or their serialized models
require a new substrate-runtime contract version. Merely adding an implementation or
changing which optional features a specific adapter advertises does not.
Changes to persisted ownership fields or their recovery meaning require a new
runtime-state version.
Changes to the SDK provider protocols or serialized agent-runtime models require
a new agent-runtime version.

When a contract is promoted to beta or stable, use a new version such as
`v1beta1` or `v1`. Supporting an older version alongside the new one is an
explicit implementation decision; a version bump alone does not promise an
automatic compatibility adapter.

## Review checklist

Every schema or compiler change must be reviewed as a contract change, not just
as an implementation diff:

1. Classify the change as compatible or versioned using the rules above, and
   record the reasoning in the change description.
2. For a versioned change, update the document `apiVersion`, deployment-plan
   schema ID, and relevant tests together. Update the planning or runtime
   substrate version only when that independent contract changes.
3. Add focused tests for schema validation, references, normalization, and
   compilation behavior affected by the change.
4. Run the full test suite and compile the maintained iperf example through the
   CLI.
5. If the deployment plan changes intentionally, regenerate the golden fixture
   and review the complete diff, including resources, agents, coordination,
   normalized snapshot, and digest:

   ```bash
   uv run python -m tests.update_golden_plans
   git diff -- tests/golden
   ```

6. For a new contract version, include migration notes showing the old and new
   forms and identify whether the compiler continues to accept the old one.

An unexplained golden-plan diff is a failed review, not a fixture update. The
fixture should be regenerated only after the contract impact is understood and
accepted.
