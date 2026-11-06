# Combined ungated recovery: offline implementation

## Scope and status

Based on direct-S2 integration `f31d1cc`, on `feat/recovery-03-ungated`.
The approved boundary remains `CampaignConfig` → `run_campaign` → exported
events, independent packet evidence and outcomes. No changes to the preserved
unified pilot or core bounded-decision runtime are required.

**Offline mechanism validation is not live/campaign evidence.** The prerequisite
live acceptance for S1-only and direct S2 remains open. This work authorizes no
VM startup, model-machine access, live incident, held-out campaign or push.

## Policy

- Select `combined_ungated`. S1 receives affected-switch diagnostics and the same
  frozen prompt, catalogue, criteria and native response/confidence fields used
  by S1-only. It makes at most one local repair attempt. Low, missing or invalid
  confidence alone has no effect on this policy.
- Successful action execution is not recovery. Following a completed local
  action, operational verification waits for the next complete send bin, one
  second of delivery and 100 ms arrival allowance, bounded by the S1 cap and common
  deadline. A `no_action` choice also requires successful operational verification
  before completing locally; otherwise it requests escalation.
- Explicit inability, unusable decisions, failed execution, failed verification,
  S1 stage timeout and local inference/telemetry errors request escalation. Failed
  calls remain treatment outcomes, not automatic experimental invalidity.
- Local success completes without S2. Otherwise, if safe and time remains, invoke
  the **same** broader S2 policy, permissions, forwarding schema and three-attempt
  bound used by direct S2. S2 receives the original local snapshot and handoff
  containing S1 decisions, attempted repair, observed results and operational
  verification/timeout events. Injection/reset evidence is not passed as context.
- Injection, observation, inference, execution, verification and handoff consume
  the original common deadline. Neither stage nor S2 retries reset it. S1 cap
  must not exceed that deadline. A contained S1 stage expiry is recorded in
  `outcome.s1_status` and the handoff cause, not as end-to-end exhaustion when S2
  subsequently completes without timeout.

## Cancellation and safety limits

Escalation requests and actual handoffs are separate events. With no remaining
time, record `escalation_denied: deadline_exhausted`. With outstanding local work,
missing terminal provider evidence or unknown action execution, record
`escalation_denied: s1_work_uncontained` and status `escalation_uncontained`.
No S2 request is admitted in that state. A cancelled await/socket is not evidence
of remote termination. This implementation conservatively refuses handoff
instead of waiting indefinitely for containment; later terminal evidence is
retained but does not retroactively change the policy outcome.

Consequently, S1 inference/action timeouts **can prevent actual escalation** even
with nominal remaining time. Stage verification timeout with terminated local
verification can safely escalate. These distinctions are covered offline and
must be assessed in authorized feasibility; this is not a claim of cancellation
support from either provider. Unknown termination still prevents another trial.

## Configuration and adapters

```bash
python -m examples.noms27.recovery_campaign \
  --config examples/noms27/recovery_campaign/development-ungated.json --check
```

This command does not construct a network or contact a provider. It declares
both local catalogue and S2 permissions/schema. Development budgets are
**unapproved** and both models intentionally unconfigured.

`endpoint`, `model_name` and optional `expected_identity` describe native S1.
Optional `s2_endpoint` and `s2_model_name` select the existing Ollama-compatible
S2 adapter; absent overrides, S2 uses `endpoint` and `model_name` as before.
Combined setup warms/verifies both adapters before traffic/injection, retaining
their separate startup results. S2 immutable model identity remains unavailable;
S1 identity verification does not establish S2 identity or equivalence. These
settings enable offline integration checks, not approval to use different models
in the campaign. Model identity/configuration parity still needs verification.

## Evidence and tests

Combined export uses `recovery-combined/v1`. Both stages' model/action records
have `stage` tags, token counts and timing; aggregate token counts include both
stages without adding unavailable reasoning usage. Local status is retained
independently of final policy status. `handoffs` and the `handoff` event retain the
same context given to S2. Message accounting counts that event once as
runner-adapter JSON bytes, **not wire bytes**. Source hashes and S1/S2 prompts,
schemas and endpoint/model settings remain in provenance.

Tests cover local completion, confidence edge cases, all failure triggers,
inherited deadlines, attempt bounds, containment refusal, and startup/incident
accounting through controlled native and S2 HTTP transports. The offline
`controlled_broader_repair` fixture now supports direct S2 and combined ungated:
an upstream local redirect fails to restore the independently walked forwarding
graph; subsequent S2 actions install missing downstream forwarding. Failure and
stale-port variants remain unsuccessful. This fixture is not an implemented
extended-diamond live campaign scenario or treatment-effect evidence.

Independent packet measurement and full observation continue unchanged from
[S1-only](recovery-01-implementation.md), including valid unsuccessful outcomes,
cleanup verification and retained late terminal records. The real live adapter
still supports only the original diamond link scenario. All six-class live
feasibility, approved budgets/resources, confidence calibration and scientific
execution approval remain outstanding.
