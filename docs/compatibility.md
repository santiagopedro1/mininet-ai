# Compatibility and versioning

Mininet AI versions its machine-readable contracts independently from the
Python package. The package version in `pyproject.toml` describes a software
release; it does not replace the version carried by a document or substrate
driver contract.

## Versioned contracts

Phase 1 defines two contract families:

- `mininet-ai/v1alpha1` covers `Experiment`, `AgentBlueprint`, and `Capability`
  documents, plus the `DeploymentPlan` produced by the compiler. The deployment
  plan JSON Schema uses the matching identifier
  `urn:mininet-ai:schema:v1alpha1:deployment-plan`.
- `mininet-ai/substrate/v1alpha1` covers the compile-time interface implemented
  by substrate drivers. It is versioned separately because driver integration
  can evolve without changing experiment documents.

The `alpha` label means that breaking revisions are expected before the
contract is declared stable. It does not mean that the meaning of an existing
version may change silently. A consumer can use the version field or schema ID
to select the exact contract it understands.

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

Changes to the driver protocol or the meaning of its manifest require a new
substrate contract version, such as `mininet-ai/substrate/v1alpha2`. Merely
adding a driver implementation or changing which optional features a specific
driver advertises does not.

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
   schema ID, and relevant tests together. Update the substrate version only
   when its independent driver contract changes.
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
