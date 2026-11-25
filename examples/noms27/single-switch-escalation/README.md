# Single-switch recovery with LLM escalation

**Primary Goal:** feature demonstration.

On `h1 — s1 — h2`, a bounded System One agent can restore missing flows but
cannot enable ports. The runner automatically brings a switch port down after
healthy warm-up. System One must select `escalate`; only then does the runner
invoke the experiment-wide LLM once. The LLM diagnoses the incident and proposes
a port repair through normal capability authorization and Substrate execution.

This deliberately restricted action set demonstrates capability-based
escalation, not a model benchmark, generalization study, or failed-repair retry.
It leaves the baseline in `examples/noms27/single-switch-recovery/` unchanged
and reuses its topology,
probes, resource-conflict checks, and cleanup harness.

## Models

| Role | Provider | Model / served alias | Endpoint |
| --- | --- | --- | --- |
| Local System One | `llamacpp-systemone` | `h2oai/h2o-lightning-4b` | `http://10.10.10.152:9931` |
| Global recovery decision-maker | `ollama` | `qwen3.5:latest` | `http://10.10.10.152:11434` |

System One still requires a llama.cpp service implementing native
`/v1/systemone`, `/health`, `/props`, and `/slots`. This is **not** Ollama System
One support. Configure the served alias to match the table. Retain the baseline
server settings: 32,768-token context, four parallel slots, and disabled
multimodal projector. These are server settings, not new client parameters.
Client decisions remain non-overlapping, at 1-second cadence with a 5-second
decision deadline and 5-second action timeout.

The planning specification explicitly disables identity verification; it makes
no verified revision/hash claim. The live CLI instead requires identity fields
or an explicit `--skip-model-verification`. Skipping identity does not bypass
native interface checks or strict bounded response validation. Compatibility and
live reliability of the selected model/server must still be established on the
experiment host; offline tests do not prove either.

## Handoff and authority

The global prompt explicitly distinguishes the escalation recipient's authority
from the local agent's restrictions. This revision follows an observed trial in
which Qwen identified a down port but returned no proposal, citing the local
agent's lack of port-enabling capability. It clarifies role and authority without
changing capabilities, scoring, or retry policy; it does not establish improved
reliability. Each trial retains its effective prompt in `experiment.yaml`.

The local instruction, observation bindings, expected local configuration and
descriptions for shared choices match the recovery example. Only the local choice
set differs because this local agent cannot enable ports.

- Local choices: `restore_reverse_flow`, `restore_forward_flow`, `no_action`,
  `escalate`. Escalation has no action proposals.
- The LLM is **global in responsibility**, but explicitly placed at the
  **control layer, scoped to `s1`**. A literal global-layer network attachment
  cannot directly authorize a switch-scoped repair in the current framework.
- It has only `ovs.port.enable`, constrained to `s1-eth1` or `s1-eth2`; no shell
  capability, unrestricted command execution, or further delegation.
- The runner pauses local admission before submitting one manual LLM intent.
  This is **runner-mediated escalation**, not native coordinated delegation.
  Running the YAML alone does not perform the handoff.
- The intent includes the latest local flow/port diagnostic snapshot and an
  allowlisted local decision history. The global invocation also observes flows.
  No fault label, expected choice, evaluator score, or injected-port answer is
  forwarded. The model must infer the repair from observed state.
- No escalation is synthesized after `no_action`, an invalid response, a local
  timeout, or an unsuccessful local repair. The first non-no-action selection
  ends local checking; otherwise at most three post-fault checks are allowed.

## Validate offline

```bash
uv run mininet-ai validate examples/noms27/single-switch-escalation/experiment.yaml
uv run mininet-ai plan examples/noms27/single-switch-escalation/experiment.yaml
uv run pytest tests/examples/test_single_switch_escalation.py
```

## Run on a disposable Linux experiment host

Requires root, Mininet/OVS, `ovs-ofctl`, `ovs-vsctl`, `ip`, `ping`, both model
services, and exclusive use of the System One service. No service is started or
reconfigured by the runner. The evidence directory must not already exist.

```bash
sudo env PYTHONPATH=. .venv/bin/python -m examples.noms27.single-switch-escalation \
  --skip-model-verification \
  --runs 1 \
  --evidence ./run-evidence/escalation-001
```

At experiment startup, before any topology is deployed, one small `/api/chat`
request loads `qwen3.5:latest` with `keep_alive: -1`, `think: false`, and a
two-token output limit. Failure aborts the experiment before fault injection.
This request is made once per batch, not once per trial, and is never scored as
a recovery decision. Ollama may still evict a model under memory pressure; the
runner neither guarantees residency nor unloads a potentially shared model.
The actual recovery agent uses deterministic sampling (`temperature: 0`, seed 0).

Each fresh topology receives three healthy System One warm-up requests, in
addition to its startup interface probe. Any incorrect/failed warm-up prevents
fault injection. The runner independently checks bidirectional readiness, brings
`s1-eth1` down, and confirms its administrative state. Use
`--fault-port s1-eth2` to fault the other port instead.

Local checking has a 30-second wall-time limit (`--trial-timeout`); the one
global invocation has a separate 120-second limit. Global timeout, invalid
output, rejected/failed actions, or unsuccessful repair fail the trial without
retrying the LLM. Timeout/cancellation does not prove remote inference stopped;
after a global failure the batch stops rather than risk overlapping another
trial. Cleanup verification remains mandatory. No broad Mininet cleanup runs.

Other overrides: `--endpoint`, `--model-name`, `--llm-host`, `--llm-model`,
`--warmup-requests`, `--max-checks`, `--ready-timeout`, and
`--confidence-threshold`. For verified model identity, omit
`--skip-model-verification` and supply `--model-repository`,
`--model-revision`, `--model-artifact`, and `--model-sha256` from the actual served
artifact. Hashes are operator declarations, not independently read remotely.

## Optional confidence gate

`--confidence-threshold 0.9` adds a runner-side safety net for post-fault local
decisions. Before a local repair is prepared, a System One choice whose reported
confidence is below the threshold is replaced by `escalate`; the existing handoff
then invokes the global agent. Warm-up is unaffected because the gate is enabled
only after fault injection. The option is disabled by default and requires the
local scenario to declare `escalate`.

This is not a core bounded-decision policy and does not affect other examples.
The raw native response is retained in `native/<invocation>/response.json`; when
the gate fires, `effective-response.json` and `gate.json` retain the replacement
choice, original choice, confidence, probability, threshold, and metric. The
invocation response also carries the gate record, and the run summary reports
`confidenceThreshold`, `confidenceGateApplied`, `modelChoice`, `confidence`, and
`choiceProbability`.

## Evidence and success

The batch retains `llm-warmup-{request,response}.json` (or an error), and a
`summary.json`. Each `run-NNN/` retains the effective experiment and plan, ledger,
native requests/responses, invocation and evaluation JSONL, baseline/readiness,
confirmed fault, handoff context/event, global result, recovery probes, runtime
report, remote idle checks, and cleanup verification as those stages are reached.
When the confidence gate fires, it also retains the raw response separately from
the effective response and gate record.
Failures and existing evidence are never overwritten or discarded.

Success requires healthy warm-up, explicit post-fault escalation, one successful
authorized global action, and **three consecutive successful bidirectional probe
rounds**, plus successful runtime stop and verified cleanup. Evidence separates
escalation requested, escalation submitted (`escalationExecuted`), action success,
and connectivity recovery. Submission alone is not proof that the LLM repaired
anything. The batch exits nonzero for any failed or incomplete trial.
