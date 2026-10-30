# H2O real-network diagnosis matrix (2026-10-30)

## Verdict: five-case live matrix passed; general model acceptance still limited

The operator ran `examples/diamond-recovery/live_diagnosis.py` once with real
Mininet/OVS, all four compiled ExperimentRuntime agents and the remote native
H2O service. Reported exit: **0**. All **20 retained invocations** match the
independent labels and meet the unchanged five-second deadline. This establishes
the tested deployment path, not general diagnosis correctness, network recovery,
sustained-load capacity or closure of #124's known model limitation.

| Trial | Correct / samples | Independent cleanup |
| --- | --- | --- |
| Healthy | 4 / 4 | Verified |
| s1-s2 link down | 4 / 4 | Verified |
| s2-s4 link down | 4 / 4 | Verified |
| Missing s2 flow | 4 / 4 | Verified |
| Unavailable port evidence | 4 / 4 | Verified |

The unavailable-evidence trial deliberately substitutes unavailable port-state
results; other trials use real collectors. Trusted setup/fault injection is not
model action authority. No action proposals, fallback, automatic diagnosis
override or inference retry was introduced. Each case starts a fresh network.
One invocation per agent per case was retained: the configured one-second
cadence is exercised through the runtime but not qualified over sustained rounds.

## Conditions and timing

- Endpoint: `http://10.10.10.152:9931`.
- Observed alias: `h2oai/h2o-lightning-4b`, Q8_0, build `b11941-19ff817fb`.
- Four slots, 8192 context tokens each; all **11** retained preflight/before/after
  slot observations explicitly idle.
- Declared artifact: `h2oai/h2o-lightning-4b-gguf`, revision
  `aba20a244eb1038b8546cd25c2d6046f0c3e4256`,
  `h2o-lightning-4b-Q8_0.gguf`, SHA-256
  `4127b2ae7710c3864c21c3d946c316f28a654a53d3a4ba99f383bbcaf2768e81`.
- Artifact hash/revision are declarations, not independently verified remote
  bytes. Actual server launch/backend and current hardware remain unverified;
  runner-host properties are not server hardware evidence. Unrelated remote
  load outside the retained idle observations is unmeasured.
- Explicit current-only context v1alpha2 with static local flow roles, original
  prompt, empty proposals, cadence **1s**, deadline **5s**.
- All five saved plans load with digest
  `sha256:61ec02128e336e7b94ca7ca378efa19794e0f801355710aec80189fcaf79515e`.

Warm inference phase: **0.174586–0.918448 seconds**, mean **0.591117**, n=20.
Elapsed time from the recorded deadline start through decision completion:
**0.220611–0.967554 seconds**. All responses report zero output tokens. State
sizes: 2164–4383 UTF-8 bytes. Startup interface probes precede scheduled
decisions; cold-start latency is not measured. These are not network recovery
or full teardown durations.

## Evidence audit and cleanup distinction

The post-run review checked all 20 execution records and actual serialized
states against the strict published schemas, recomputed labels from the supplied
inputs, checked empty proposals/effects, deadline admission, unique agent coverage,
coordination/event correlations, and scenario equality with saved plans. All five
SQLite databases pass integrity and foreign-key checks; run identities, plan
digests, contiguous sequences and four trigger-event IDs per case reconcile.
The ledger contains lifecycle/events, not invocation payloads; those are retained
in `invocations.jsonl` and `runtime-report.json`.

Built-in runtime cleanup remains **unknown**, and runtime reports/ledger terminal
states therefore say **failed**, solely for `experiment.cleanup.unverified`.
Those records are preserved, not rewritten as success. The runner's separate
trusted observer verifies absence of owned interfaces, bridges, shell identities,
descendants, process groups and private network namespaces after each teardown,
without remaining/unknown resources or evidence gaps. Thus operation succeeded
and the independently checked matrix passed; this does not upgrade the generic
Substrate cleanup guarantee. Idle service observations separately establish
between-trial isolation, not individual historical request termination causes.

Source directory: `diagnosis-live-20261030T201718Z` at the repository root.
Archive: `evidence/diagnosis-live-h2o-20261030.tar.gz`, **232683 bytes**,
SHA-256 `f6a460104ea2b2d0dc6f62a5c36e8d5ef3098b8510b2652590130c1b7fb38b8c`.
It contains the entire supplied directory plus `post-run-review.json`: effective
experiment, model properties/provenance, all slot observations, five plans,
ledgers, invocation records, runtime/summary reports and setup/fault/independent
cleanup records. No exact executed-code snapshot was captured at launch;
implementation base `38ad977` and current source were reviewed afterwards.
The pre-existing user validator edit remained in the working tree, uncommitted.

## Support and remaining limitation

The real-network matrix is now measured and passed, not unattempted or blocked
on host access. It does **not** erase the constructed missing-s1 probe failure
under H2O enriched state or the original missing-s2 failure shared by Kev/H2O.
Insufficient information/representation remains a hypothesis, not an established
cause. See the [rich-state report](bounded-decision-rich-state-evaluation.md).

#124 remains the known-limitation record. #125/#116 require an explicit final
acceptance/support disposition because the broader native correctness prerequisite
did not pass. No issue closure, gate waiver, universal model-support claim or
automatic production adoption follows from this narrower live result. Further
model sweeps/tuning remain paused per owner direction.
