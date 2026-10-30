# Static local flow-role state experiment (2026-10-30)

> **Owner handoff:** Further model/prompt/state experiments are paused. Continue
> implementation with the original shared Kev/H2O miss recorded on #124 as a
> known limitation. Insufficient information or state representation is a possible
> explanation, not a proven cause. The later enriched-state s1 miss is H2O-only;
> Kev was not evaluated with that state. This does not establish live acceptance
> or waive support gates.

## Verdict: original s2 miss corrected / general correctness still fails

The user approved enriching the request's state rather than further tuning the
prompt. The example now explicitly opts into versioned static local annotations,
and its instructions and both choice descriptions are restored to the original
wording. The driver checked equality against the original H2O configuration
before sending inference. Raw expected and observed flows, ports, evaluator,
model, cadence, deadline and empty proposals were not changed.

### What the model receives

`mininet-ai/bounded-decision-context/v1alpha2` adds only two switch-local fields:

```json
{
  "diagnosticScope": "required-local-configuration",
  "flowRoles": {
    "0x2b67": {
      "purpose": "Diagnostic probe 10.0.0.12 to 10.0.0.11",
      "required": true,
      "requirement": "Mandatory diagnostic configuration even if active traffic could still pass"
    }
  }
}
```

The snippet shows one role; the actual state contains the complete role mapping
for the current switch only. Roles come from immutable scenario declarations,
not observations or evaluator comparisons. Both healthy and faulty states carry
the same annotations. No `missing`, `isDegraded`, `expectedDiagnosis`, history,
other-switch role table, or automatic answer override is added. Raw flow objects
still contain only cookie/table/priority/match/actions expectations; the collector
is not expected to supply author-owned role descriptions.

The optional v1alpha5 authoring declaration is `diagnosticContext`, with an
explicit v1alpha2 context identifier. It is embedded in compiled plans, validates
complete target/cookie bindings, and is omitted entirely when not configured.
Existing unannotated plans keep the original context v1alpha1 shape and digest.
The current example opts in explicitly; it does not enrich archived plans.
See [compatibility](../compatibility.md#bounded-decision-context-v1alpha1-to-v1alpha2)
and the strict `bounded-decision-context-v1alpha2.json` schema.

## Native conditions and result

Endpoint: `http://10.10.10.152:9931`; observed H2O-Lightning-4B Q8_0,
build `b11941-19ff817fb`, four idle 8192-context slots. Expected revision/hash
and remote provenance gaps remain those of the
[original H2O evaluation](bounded-decision-h2o-evaluation.md): the remote checksum
result is not separately confirmed; actual backend/launch and current hardware
are not independently observed. No model/service substitution occurred.

The real four-agent `ExperimentRuntime`, native client, scheduling, coordination
and retained execution path are used with constructed Substrate observations
only. The transport checks the exact state against the v1alpha2 schema and the
32000-byte H2O safety bound before POST; it does not modify the request. Cadence
is one second and the operational deadline five seconds. Startup interface probes
precede scheduled decisions; cold latency is unmeasured. This is a designed
representation experiment after inspected failures, not held-out validation.

| Case | Correct / retained | Result |
| --- | --- | --- |
| Healthy | 4 / 4 | Passed |
| Original missing s2 probe rule | 4 / 4 | Passed |
| s1-s2 local port fault | 4 / 4 | Passed |
| s2-s4 local port fault | 4 / 4 | Passed |
| Missing s1 probe rule | 3 / 4 | Failed: s1 selected healthy |
| Remaining 12 declared cases | Not attempted | Stopped after first failed case |

All 20 native invocations completed successfully as runtime operations, with
zero output tokens and no timeouts. Nineteen matched their labels. Inference
phase latency: 0.1577–0.8161 seconds, mean 0.5393, n=20. These are not whole
lifecycle or connectivity-recovery durations. State sizes were 2099–3883 bytes.
No inference was retried. Remote slots were observed idle before/after each
attempted case; Fake cleanup verifies its in-memory model, not host/network state.

The corrected s2 case's raw context was compared with the original failed H2O
request: after removing identity and the explicit new version/static fields,
they are identical. No missing-flow hint was smuggled into its observations.

The new failure is s1's absent rule `cookie 0x2b6e`: IPv4
`10.0.0.22 -> 10.0.0.21`, ingress 3, output 1, table 0, priority 100.
The model receives its static diagnostic-probe role with `required: true`, but
still selects healthy (probability 0.6324384659, 1693 input tokens, zero output
tokens). That is wrong under the unchanged evaluator. Correcting the inspected
s2 case did not establish general missing-rule correctness.

## Evidence and validation

`evidence/diagnosis-h2o-rich-state-20261030.tar.gz` includes the exact prompt and
effective scenario, complete declared 17-case matrix, static annotations,
driver, plans, all requests/responses/labels, ledgers, runtime reports, slot
observations, state schema, validated results and effective implementation patch.
The patch captures the uncommitted working-tree code used for the run, including
the pre-existing validator edit as provenance; it does not imply that user edit
was committed. The base commit and source digest are recorded in conditions.

- SHA-256: `9280b34dbcda2d0bcbc796e07e8d2c0516a0adec62d2a89e32e20e319875acd1`.
- All **20 execution records** validate against strict evidence schemas; actual
  v1alpha2 states also validate against the new strict state schema.
- **17 previously archived plans** validated without field or digest changes.
- Source run: `/tmp/opencode/diagnosis-h2o-rich-state-20261030T175136Z`.

Full offline regression: **1218 passed, 3 opt-in skips, 179 subtests passed**.
Lint, formatting and Pyright pass. Tests cover immutable save/reload, version
selection, omission for old plans, strict annotations, per-switch filtering,
raw flow separation, invalid/incomplete mappings, absence of observed verdicts,
and actual retained requests through continuous runtime/coordination. Scripted
tests do not establish native model quality.

The context experiment is implemented, but #124 remains open for the failing
missing-rule case and unattempted cases. #125's privileged real-network matrix
remains blocked. No further tuning, model swap, network mutation or support
promotion was performed.
