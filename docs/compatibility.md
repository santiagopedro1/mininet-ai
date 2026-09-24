# Compatibility and versioning

Mininet AI versions its machine-readable contracts independently from the
Python package. The package version in `pyproject.toml` describes a software
release; it does not replace the version carried by a document or substrate
driver contract.

## Versioned contracts

The project currently defines five contract families:

- `mininet-ai/v1alpha1` covers `Experiment`, `AgentBlueprint`, and `Capability`
  documents, plus the `DeploymentPlan` produced by the compiler. The deployment
  plan JSON Schema uses the matching identifier
  `urn:mininet-ai:schema:v1alpha1:deployment-plan`.
- `mininet-ai/substrate/v1alpha1` covers the compile-time interface implemented
  by substrate drivers. It is versioned separately because driver integration
  can evolve without changing experiment documents.
- `mininet-ai/substrate-runtime/v1alpha1` covers the stateful lifecycle
  interface implemented by executable substrate adapters: deploy, inspect,
  observe, execute, and teardown. It is separate from the planning contract so
  compiling an experiment never requires privileged networking access.
- `mininet-ai/runtime-state/v1alpha2` covers the private, on-host ownership
  record used to inspect and recover an interrupted Mininet/OVS run. It is
  versioned so a newer runtime never guesses how to clean up an incompatible
  record.
- `mininet-ai/agent-runtime/v1alpha1` covers the SDK seam used by agent,
  capability, and model-provider adapters, including scoped invocation context,
  action proposals, normalized model responses, invocation results, versioned
  provider descriptors, registry semantics, and the JSON protocol used by
  external capability processes and services. It is independent of experiment
  and substrate contract versions.

The `alpha` label means that breaking revisions are expected before the
contract is declared stable. It does not mean that the meaning of an existing
version may change silently. A consumer can use the version field or schema ID
to select the exact contract it understands.

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

## Changes allowed within `v1alpha1`

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

Use a new version, beginning with `mininet-ai/v1alpha2`, when a change can make
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
a new substrate contract version, such as `mininet-ai/substrate/v1alpha2`.
Changes to runtime operations, lifecycle semantics, or their serialized models
require a new runtime contract version, such as
`mininet-ai/substrate-runtime/v1alpha2`. Merely adding an implementation or
changing which optional features a specific adapter advertises does not.
Changes to persisted ownership fields or their recovery meaning require a new
runtime-state version, such as `mininet-ai/runtime-state/v1alpha2`.
Changes to the SDK provider protocols or serialized agent-runtime models require
a new agent-runtime version, such as `mininet-ai/agent-runtime/v1alpha2`.

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
4. Run the full test suite and compile the Phase 1 example through the CLI.
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
