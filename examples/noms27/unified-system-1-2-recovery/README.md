# Unified System One/System Two recovery

**Primary Goal:** both feature demonstration and realistic scenario.

This is the combined form of the two focused NOMS examples. One experiment runs
either a link failure or a missing-flow fault in a diamond topology
`h1 — s1 — s2/s3 — s4 — h2`. System One is placed only on `s1`, the switch that
carries the host access link plus both diamond links. The agent-free switches
`s2`–`s4` are statically seeded by the runner: `h1 → s2 → s4 → h2` is the
primary path, the backup leg `h1 → s3 → s4 → h2` is seeded for the forward
direction only, and the required `s1` flow pair always feeds the primary link.
System One can repair every declared fault locally. System Two is invoked only
when System One explicitly escalates or its post-fault confidence falls below
the configured gate.

Here, System One and System Two name the agents' decision-making approaches:
direct bounded decisions and deliberative reasoning, respectively. Their
placement and escalation-only handoff are choices of this workflow. Both agents
have authority only over `s1`; System Two does not imply experiment-wide
authority. See [decision-making approaches](../../../docs/decision-making.md)
for the model assumptions and execution distinction.

## Faults

Each run selects one concrete fault. The default is random, seeded and recorded:

| Category | Target | Expected local choice |
| --- | --- | --- |
| `link-failure` | `s1-eth1` | `enable_access_port` |
| `link-failure` | `s1-eth2` | `enable_s2_link` |
| `link-failure` | `s1-eth3` | `enable_s3_link` |
| `missing-flow` | `forward` (`0x5101`) | `restore_forward_flow` |
| `missing-flow` | `reverse` (`0x5102`) | `restore_reverse_flow` |

The local choice set is exactly those five repairs plus `no_action` and
`escalate`. The shared instruction and the shared choice descriptions match the
focused recovery example.

## Models

| Role | Provider | Model / served alias | Endpoint |
| --- | --- | --- | --- |
| System One | `llamacpp-systemone` | `h2oai/h2o-lightning-4b` | `http://10.10.10.152:9931` |
| System Two | `ollama` | `qwen3.5:latest` | `http://10.10.10.152:11434` |

This example's System One agent requires a llama.cpp service implementing native
`/v1/systemone`, `/health`, `/props`, and `/slots`. Configure the served alias to
match the table. Client decisions remain non-overlapping, at 1-second cadence
with a 5-second decision deadline and 5-second action timeout.

The planning specification explicitly disables identity verification. The live
CLI instead requires identity fields or an explicit `--skip-model-verification`.
Skipping identity does not bypass native interface checks or strict bounded
response validation.

## Authority

- System One is placed at the data layer, scoped to `s1`, with
  `openflow.flow.install` and `ovs.port.enable`.
- System Two is placed at the control layer, scoped to `s1`, with the same two
  capabilities.
- s2–s4 host no agents; the runner statically seeds their forwarding flows and
  every injected fault is confined to `s1`.
- System Two is a separate agent, not a local retry. The local agent is paused
  before the one manual System Two intent is submitted.
- A failed local repair does **not** automatically invoke System Two. Escalation
  happens only through an explicit `escalate` choice or the confidence gate.
- System Two must return exactly one authorized action; zero, multiple, or failed
  actions fail the trial.
- System Two's prompt distinguishes observed `switch.diagnosis` state from the
  required `switch.expectations` configuration. A required flow cookie absent
  from the observed flows is missing; expectations are never treated as
  observations.

## Confidence gate

The default threshold is `0.9`. The gate is enabled only after fault injection;
warm-up is unaffected. Any non-`escalate` post-fault choice whose confidence is
below the threshold is replaced by `escalate` before local action preparation.
The existing runner-mediated handoff then invokes System Two.

The gate is a runner-side policy, not a core bounded-decision feature. The raw
native response is retained in `native/<invocation>/response.json`. When the gate
fires, `effective-response.json` and `gate.json` retain the replacement choice,
original choice, original confidence and probability, threshold, and metric. The
invocation response also carries the gate record. Evidence distinguishes
`escalationCause: confidence-gate` from `escalationCause: model-explicit`.

## Run

Validate without network privileges:

```bash
uv run mininet-ai validate examples/noms27/unified-system-1-2-recovery/experiment.yaml
uv run mininet-ai plan examples/noms27/unified-system-1-2-recovery/experiment.yaml
uv run pytest tests/examples/test_unified_system_1_2_recovery.py
```

Live runs require root, Mininet/OVS, `ovs-ofctl`, `ovs-vsctl`, `ip`, `ping`, and
both model services. The evidence directory must not already exist.

```bash
sudo env PYTHONPATH=. .venv/bin/python -m examples.noms27.unified-system-1-2-recovery \
  --skip-model-verification \
  --runs 10 \
  --evidence ./run-evidence/unified-001
```

Random faults are the default; the batch seed is generated and recorded when
`--seed` is omitted. Reproduce a batch by passing the recorded seed.

Override a category or target:

```bash
# Random target within a category
--fault link-failure

# Exact concrete fault; the category is inferred
--fault-target forward

# Disable gate replacement for a comparison run (threshold below every valid confidence)
--confidence-threshold 0
```

Other overrides: `--endpoint`, `--model-name`, `--llm-host`, `--llm-model`,
`--warmup-requests`, `--max-checks`, `--ready-timeout`, `--trial-timeout`, and
`--seed`.

## Evidence and success

Each run retains the selected fault (`fault-selection.json`), the effective
experiment and plan, evaluation contract, ledger, native requests/responses,
invocation and evaluation JSONL, baseline/readiness, confirmed fault, handoff
context/event when escalated, System Two result, recovery probes, runtime report,
remote idle checks, and cleanup verification as those stages are reached.

Success requires healthy warm-up, a successful local repair **or** one successful
System Two repair, and three consecutive successful bidirectional probe rounds,
plus successful runtime stop and verified cleanup. A gate-triggered escalation is
accepted but recorded separately from a model-explicit escalation. A wrong local
choice fails the trial without automatic escalation to System Two. A System Two timeout or
failed repair stops the batch; an ordinary failed trial with verified cleanup
does not.

This example does not establish model calibration, correctness for untested
faults, or generalization beyond the five declared faults. Confidence remains a
diagnostic, not a correctness guarantee.
