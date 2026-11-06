# NOMS 2027: single-switch System One recovery

**Primary Goal:** realistic scenario.

One System One agent monitors `h1 — s1 — h2` and selects among seven policies.
Each run starts with a fresh, healthy topology. The runner sends three warm-up
diagnostic requests by default, independently checks connectivity, then
automatically brings `s1-eth1` down. It records the command and confirmed-down
timestamps and waits for the agent. There is no Enter prompt.

Before the fault, `no_action` is correct. After this fault, only
`enable_access_port` is correct. Every other selection is wrong. Use
`--fault-port s1-eth2` to fault the other access link instead; its correct policy
is `enable_peer_port`.

The model is not told which answer is expected. Its instructions, policy
descriptions, and current observations do not identify implementation stubs.
The evaluator keeps the answer key and implementation details separately.

The native choice descriptions state **applicability conditions**, such as
"Required switch port s1-eth1 is down," rather than action-oriented instructions.
Choice IDs still map to the same fixed actions. This description set is recorded
as `applicability-conditions-v2` in the evaluator contract. Development replays
with healthy, port-1-down, and port-2-down inputs motivated the change; they are
not held-out validation or proof of successful live recovery. Earlier failed
runs remain valid records of the earlier configuration.

The local instruction, observation bindings, expected local configuration and
descriptions for shared choices match the escalation example; only the local
choice sets differ.

## Choices and implementation

| Choice | Meaning | Implementation in this experiment |
| --- | --- | --- |
| `restore_reverse_flow` | Restore the h2-to-h1 entry | Fixed flow proposal; refused if already present |
| `enable_access_port` | Enable s1-eth1, facing h1 | Fixed switch-scoped port-enable action |
| `enable_peer_port` | Enable s1-eth2, facing h2 | Fixed switch-scoped port-enable action |
| `restore_forward_flow` | Restore the h1-to-h2 entry | Fixed flow proposal through normal capability authorization and execution |
| `repair_neighbor_entry` | Refresh a missing or incorrect neighbor mapping | Stub; records the selection without changing the network |
| `no_action` | Leave a healthy configuration unchanged | No mutation |
| `escalate` | Ask an LLM to handle an incident outside local capability | Stub; records escalation requested, but calls no second model |

Stubs are intentional distractors, not working recovery mechanisms. Results
evaluate selection and the implemented correct repair, not the effectiveness of
the stub policies or an escalation system. Policy order is fixed in this initial
experiment; this is not a position-bias or generalization study.

The new opt-in `current-recovery` input mode permits two to eight unique choices.
Each actionable choice restores one exact flow or enables one declared switch
port from `localExpectations`. The port action cannot change a host endpoint,
another switch's interface, or an undeclared port. Before admission, trusted
inspection refuses repairs whose desired state is already present or unknown.
Enabling an administratively up but carrier-down interface cannot fix its peer;
the action does not establish recovery. Normal authorization, action admission,
deadline enforcement, and completion recording still apply. Existing
`current-only` diagnosis remains action-free and limited to two choices.

## Validate without network privileges

```bash
uv run mininet-ai validate examples/noms27/single-switch-recovery/experiment.yaml
uv run mininet-ai plan examples/noms27/single-switch-recovery/experiment.yaml
uv run pytest tests/examples/test_noms27.py
```

The checked-in specification uses the Mininet/OVS substrate, so validation and
planning exercise Mininet/OVS constraints without requiring root or deploying a
topology. Its model revision and all-zero hash are explicit placeholders, not
verified identity. Supply actual identity declarations or explicitly skip
identity verification; do not use planning placeholders as if they identified a
verified artifact.

## Run on a disposable Linux experiment host

Requirements: root access, working Mininet/OVS, `ovs-ofctl`, `ovs-vsctl`, `ip`,
`ping`, and access to the native System One service. The default endpoint is
`http://10.10.10.152:9931`, with model alias `h2oai/h2o-lightning-4b` and artifact
`h2o-lightning-4b-Q8_0.gguf`. The supplied server command sets a 32,768-token
context, four parallel slots, and disables the multimodal projector. These are
server settings, not additional client model parameters. The runner neither
starts nor reconfigures that service; agent invocations remain non-overlapping.

For an automatic batch without revision/hash declarations:

```bash
sudo env PYTHONPATH=. .venv/bin/python -m examples.noms27.single-switch-recovery \
  --skip-model-verification \
  --runs 10 \
  --warmup-requests 3 \
  --evidence ./run-evidence/single-switch-batch-001
```

`--skip-model-verification` skips model-name and artifact-identity matching. It
records unverified identity with no invented revision or hash. It does **not**
skip health/readiness checks, the native interface probe, strict response
validation, deadlines, or action authorization. Without the flag,
`--model-repository`, `--model-revision`, `--model-artifact`, and
`--model-sha256` are required. Observable identity is checked; the runner does
not independently hash the remote artifact.

The evidence directory must not exist. The runner refuses conflicting scenario
resource names or a model service whose idle state cannot be established. Run
with exclusive use of the model service; idle checks alone cannot guarantee
exclusivity throughout a trial. No broad Mininet cleanup is performed.

`--runs` controls the number of fresh topology trials (default 1).
`--warmup-requests` controls the warm-up attempts **per run** (default 3; use 2
for two requests). The former `--settle-checks` name remains an alias.
Each runtime also performs its usual native startup interface probe; that is an
additional request, not one of the declared diagnostic warm-ups.

Failed decisions are retained and the batch continues after verified cleanup.
Unsafe cleanup, non-idle/unknown model service state, setup errors, or Ctrl+C
stop the batch rather than blindly redeploying. The command exits nonzero if
any run fails or the batch is incomplete. Existing evidence is never deleted or
overwritten by this script.

## What the runner verifies

1. Set up permanent neighbor entries and explicit forwarding with table-miss
   drop. There is no controller, automatic failover, or `NORMAL` forwarding.
2. Establish healthy bidirectional connectivity.
3. Complete the fixed number of warm-up decision attempts. These use real
   current diagnostics and should choose `no_action`. They are tagged `warmup`
   and kept separate from post-fault results. A failed/incorrect warm-up fails
   that run before fault injection; the runner does not keep retrying until it
   gets enough correct answers. `--ready-timeout` bounds this phase.
4. Pause new decisions after warm-up and independently verify bidirectional
   connectivity, retaining `ready.json`. If healthy, execute
   `ip link set dev s1-eth1 down`, confirm the administrative down state and its
   timestamp, then resume monitoring automatically. If readiness fails, do not
   inject the fault. These checks are outside diagnostic collection and do not
   consume the agent's decision budget.
5. Observe up to three completed post-fault checks (`--max-checks`, default 3).
   Stop after the first repair/escalation choice, even when wrong, or fail after
   three no-action/failed checks. In-flight observations collected before the
   fault do not count. Earlier wrong checks remain wrong even if a later check
   repairs the fault. `--trial-timeout` is a separate post-fault wall-time limit.
6. Check three consecutive bidirectional probe rounds after a terminal result,
   stop without draining pending ticks into new decisions, and
   independently check the scenario bridge, interfaces, and captured host
   process identities for cleanup. Runtime reports retain worker/drain issues.

Non-draining shutdown rechecks admission after waking a paused worker and after
reserving capacity. Pending ticks are discarded rather than becoming extra
failed invocations; work already admitted before stopping still completes.

The agent's interval is configured by `agents[].triggers[].every`, not the
blueprint. Bounded execution currently requires one-second polling and one
invocation at a time. Busy ticks are coalesced/dropped, not extra chances; a
three-check budget is not a three-second timeout. Each invocation retains the
five-second deadline. Startup probes the interface, so this is not cold inference.
There is no second-model fallback. Ctrl+C requests a stop and retains evidence.
After cleanup, the next run gets a new deployment, runtime, ledger, warm-up
sequence, and fault timestamp. The remote model service is not restarted or
reconfigured; network freshness does not imply cold inference or reset caches.

Passing the recovery trial requires a correct choice within the check budget,
successful invocation, observed
connectivity recovery, successful runtime operation, no non-cleanup runtime
issues, an idle remote service after stop, and verified scoped cleanup. A correct
model choice alone is not a pass. First-check correctness and all incorrect
post-fault checks are separately reported; eventual recovery does not erase them.

## Run Evidence and measurements

Even a one-run batch now uses this layout:

```text
single-switch-batch-001/
  batch-config.json
  summary.json             # Counts and compact per-run outcomes
  run-001/
    summary.json           # Full individual trial result
    ready.json
    fault.json
    invocations.jsonl
    evaluations.jsonl
    native/
    ...
  run-002/
    ...
```

The batch summary is updated after each trial and distinguishes failed completed
trials, setup errors, and interrupted runs. Per-run summaries include warm-up
counts, first-check correctness, recovery time, and the final answer's reported
confidence and choice probability. Those scores are not calibrated guarantees.

The runner retains the resolved experiment and plan, evaluator contract,
baseline and fault evidence, per-invocation native request/response under
`native/<invocation-id>/`, append-only `invocations.jsonl` and `evaluations.jsonl`,
SQLite ledger, runtime report, cleanup checks, and summary. Startup failures are
recorded separately and do not become successful recovery trials.

The summary records selection correctness, confirmed connectivity recovery,
end-to-end recovery time, unnecessary action attempts, and the server's token
usage fields. Missing token counts remain unavailable rather than being
estimated. Input and output counts can be read from the retained native response.
Unnecessary policy selections are recorded separately: a selected stub does not
count as an executed network action.

Recovery time runs from confirmed administrative down state through the third
successful bidirectional probe round. The fault record separately retains
command-submission and completion timestamps, in UTC and process-monotonic time.
The interval includes waiting for the next usable check and confirmation probes;
it is not pure model latency or the exact instant forwarding was restored.

This is an administratively disabled switch-side interface, not a physically
disconnected cable or deleted interface. Recovery re-enables that interface; it
does not find an alternate path or recover around a fault kept forcibly down.

Offline tests use scripted model responses and the Fake Substrate. They do not
establish live batch reliability, latency distributions, or generalization.
