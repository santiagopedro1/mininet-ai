# Shared model configurations: integrated handoff

Final acceptance evidence for [#94](https://github.com/santiagopedro1/mininet-ai/issues/94),
parent [#89](https://github.com/santiagopedro1/mininet-ai/issues/89), on
`feat/94-shared-model-handoff`. Starting integration tip:
`a0edacd` on `feat/89-reusable-model-configurations` (#90/#91/#92/#93 integrated).
`main` remained at `847a317`; neither integration nor `main` was merged into or
modified by this handoff. `git merge --no-edit feat/89-reusable-model-configurations`
reported **Already up to date** before the final gates.

## Delivered and checked

- Adapted the existing Autonomous Network Operation Maintained Example: both
  blueprints select `shared-operation`; host traffic replaces the complete
  `parameters.response` object and inherits `usage`. Its documents now use v4;
  the shared file remains an unversioned plain mapping. Getting Started remains
  inline. No Maintained Example was added.
- Closed the audit's diagnostic gaps, not just documented them: malformed model
  forms now report the blueprint, known target/entry, offending field, reason,
  and YAML position without Pydantic union names. Invalid selected/unused hosts
  identify `models.<entry>.parameters.host`. Invalid effective hosts point to the
  actual supplied override in its blueprint document. Owner-specific errors and
  literal-credential errors also retain field positions. Programmatic overrides
  do not falsely claim their base field's YAML position.
- Completed authoring/compatibility/example guidance, including decision links,
  shallow replacement, null-not-deletion, fixed Provider, owner-specific checks,
  offline limits, provenance/identity, source warnings and original saved plans.
- Kept Agno runtime environment lookup and custom construction ownership intact.
  No credential-selector fields, Provider allowlist, live inference, or new
  execution abstraction were introduced.

### Parent acceptance map (existing public seams)

| Concern | Retained and extended behavior evidence |
| --- | --- |
| Inline/reference/override identity | `tests/compiler/test_model_overrides.py`: equivalent native-v4 inline, reference, override, and differently named entry/file give equal snapshots/digests; recompiling the effective snapshot preserves agents too; provenance remains distinct and non-executable. |
| Mixed old/current sources | `tests/compiler/test_model_handoff.py`: v3 experiment, external v3 blueprint in a distinct directory, embedded v4 blueprint and v3 capability; visible warnings, v4 emission, resolved override and unchanged loaded input. Invalid old/current bases and effective models fail through both CLI commands. |
| Construction ownership | `test_model_overrides.py`, `test_model_handoff.py`, `test_model_validation.py`: independent base/effective Ollama and mock checks (overrides cannot repair bases), both bundled Ollama-only factories, nested/null custom values, optional custom models, consuming factories retaining ownership. Compilation guards forbid factory import/invocation, credential lookup, client/model construction. |
| Paths and consistent reads | `tests/compiler/test_model_references.py`: document-relative embedded/external, absolute/parent-relative, cwd change, programmatic explicit source/fallback, canonical aliases observing one source version per compile and later compiles seeing edits. `test_model_handoff.py` adds actual traversal to a separate parent configuration directory. |
| Saved execution independence | `test_model_references.py`, `test_model_overrides.py`: serialize, edit/move/remove sources, reconstruct and invoke offline declarative/mock or consuming custom factories; old effective settings survive while later compilation sees edits. Guards now intercept the actual `Path.open` source boundary, not an unused imported loader binding. |
| Safe diagnostics/no plan | `test_model_handoff.py` plus reference/override tests: validate AND plan, embedded/external incomplete/mixed forms and forbidden fields, exact invalid-host/override locations, root/entry/nested duplicate keys with both positions, malformed YAML, unknown fields, unused invalid entries and all six literal credential names. No sentinel values or executable plan on failure. |
| Original saved-plan compatibility | `tests/test_contract_compatibility.py`: frozen original v3 plan shape/digest, unchanged snapshot/version/digest, no source lookup/recompilation, original runtime-only failures, invalid shape/digest and alias bypass rejection. `test_model_references.py` rejects unresolved executable references, including unused blueprints. Frozen legacy schemas/fixture were not regenerated. |
| Runtime environment | `tests/agents/test_ollama_configuration.py` and override tests: actual clean-baseline Agno behavior for explicit/omitted hosts, runtime env/cloud/default precedence, missing/blank keys, controlled HTTP transport and no process-environment mutation. No new selector syntax is implied. |
| Maintained Example behavior | `tests/examples/test_examples.py`: all four original autonomous-operation IDs, exact effective responses (including original switch proposal), shared Provider/model ID and inherited usage through the SDK catalog. |

All exhaustive layouts above are temporary Test Fixtures built from the existing
compiler fixture, not additional user-facing scenarios or new public test APIs.

## Test-first diagnostic fixes

The TDD skill was loaded; the seams were already human-confirmed in #89.
Each fix followed a failing regression before implementation:

1. `python -m pytest -q tests/compiler/test_model_handoff.py`: four failures
   demonstrated lost selected/unused host field paths; all four then passed.
2. The reference-shape regression failed on missing blueprint/position context,
   including an override-only reference; the embedded/external validate/plan
   matrix then passed after contextual rendering.
3. `python -m pytest -q tests/compiler/test_model_handoff.py::test_effective_host_errors_point_to_the_supplied_override`:
   two failures demonstrated misattributed override locations; both then passed.
4. `python -m pytest -q tests/compiler/test_model_handoff.py::test_programmatic_override_errors_do_not_claim_the_base_field_position`:
   failed on the incorrect base location, then passed with the actual override path.
5. The new Maintained Example assertion failed on absent shared provenance before
   adapting the YAML, then passed with exact original response expectations.

## Final repository gates

Executed from the worktree with the main checkout's existing environment, **no
editable-install repointing or dependency installation**:

```bash
export PYTHONPATH="$PWD"
PY=/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python

"$PY" -m pytest -q --disable-warnings tests/test_compiler.py tests/compiler tests/sdk tests/test_contract_compatibility.py tests/test_deployment_plan_schema.py tests/test_public_contract_version.py tests/test_golden_plans.py tests/agents/test_agno_provider.py tests/agents/test_ollama_configuration.py tests/agents/test_ollama_model.py tests/examples/test_examples.py
"$PY" -m pytest -q -rs --disable-warnings
"$PY" -m ruff check .
"$PY" -m pyright --pythonpath "$PY"
git diff --check
```

Results (2026-10-21):

- Focused: **394 passed, 84 subtests passed, 110 warnings** (12.62s).
- Full: **1091 passed, 179 subtests passed, 4 skipped, 277 warnings** (59.73s).
- Ruff: **All checks passed**. Pyright: **0 errors, 0 warnings**.
- `git diff --check`: clean.
- Warnings are expected visible v3 source deprecations. The four skips require
  opt-in live Mininet/OVS development-VM prerequisites; live inference/network
  deployment is not an acceptance prerequisite for this feature.

An initial Pyright invocation without `--pythonpath` could not locate the shared
environment's dependencies; explicitly choosing the interpreter fixed the tooling
configuration. Intermediate Ruff import-order findings were corrected and the
final check is clean. No unresolved test/lint/type-check failures remain.

### CLI gates: all five Maintained Examples and explicit iperf compilation

Exact commands (output retained outside the repo):

```bash
mkdir -p /tmp/opencode/mininet-ai-89/94-gates
export PYTHONPATH="$PWD"
PY=/home/pedro/projects/mininet-ai/mininet-ai/.venv/bin/python
for name in getting-started autonomous-operation hierarchical-routing iperf-throughput link-failure-latency; do
  "$PY" -m mininet_ai.cli validate "examples/$name/experiment.yaml" > "/tmp/opencode/mininet-ai-89/94-gates/$name.validate.txt" 2> "/tmp/opencode/mininet-ai-89/94-gates/$name.validate.stderr" || exit
  "$PY" -m mininet_ai.cli plan "examples/$name/experiment.yaml" --format json > "/tmp/opencode/mininet-ai-89/94-gates/$name.plan.json" 2> "/tmp/opencode/mininet-ai-89/94-gates/$name.plan.stderr" || exit
done
"$PY" -m mininet_ai.cli plan examples/iperf-throughput/experiment.yaml --format json > /tmp/opencode/mininet-ai-89/94-gates/iperf-explicit.plan.json 2> /tmp/opencode/mininet-ai-89/94-gates/iperf-explicit.stderr
```

All eleven commands exited **0**, emitting only v4 plans:

| Maintained Example | Agents | Deprecation warnings per command |
| --- | ---: | ---: |
| getting-started | 1 | 3 |
| autonomous-operation | 4 | 0 |
| hierarchical-routing | 6 | 7 |
| iperf-throughput | 2 | 4 |
| link-failure-latency | 10 | 3 |

The additional explicit maintained iperf command also exited **0**, with
`iperf-client@client` and `iperf-server@server` and four expected warnings.
This is actual CLI compilation, not dry-run or deployment.

## Complete golden diff and example parity

Reviewed the **complete**, unfiltered output of:

```bash
git diff main -- tests/golden
git diff a0edacd -- tests/golden mininet_ai/compiler/schemas tests/fixtures/plans
```

The only golden is `tests/golden/compiler-multilayer-deployment-plan.json`.
Against `main` (`847a317`) there are exactly five scalar changes:

- Plan `apiVersion`, snapshot experiment `apiVersion`, snapshot blueprint
  `apiVersion`, and snapshot capability `apiVersion`: v3 → v4.
- Digest:
  `sha256:2d4cc0a6117c25b5f7dd31521d7bf4f05bdf0e654fb08b33fd98ef2674386f2b`
  → `sha256:fa573b29f587c958dd981ddb1e6f3968581912cdcb8f7dbd185d594cd4d8f752`.
  The versioned snapshot changes its identity; cross-version equality is not promised.

**No resources, topology, agents, coordination, effective inline models, list
ordering, or other snapshot fields changed.** Empty provenance is omitted.
There is no additional golden/schema/legacy-fixture diff against `a0edacd`.
No golden update command or frozen legacy regeneration was run for #94.

Also compiled autonomous-operation with its original `a0edacd` inline blueprints
(read using `git show`, parsed under bounded source compatibility) and compared
the entire plan with the adapted plan, excluding only `model_provenance`:
**exact parity**, digest
`sha256:135227dadd93385fca1864aa4bc824c86eaad79037c3d2736aa672b0ce116dc3`.
This independently checks preserved scenario behavior, resources and ordering.

## Review and issue-tracker handoff

This file is the parent final-review pointer, not a substitute for that review.
No issues were closed and neither integration nor `main` was merged. Parent
review and authorized integration/merge/issue-tracker completion remain with the
driving session. Runtime still depends on its environment, installed factory code
and remote services when those are used; offline compilation establishes none
of their availability or inference correctness.
