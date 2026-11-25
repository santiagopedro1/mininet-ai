# Ticket 03 Live Success Diagnosis — 2026-11-07

## Summary

Three targeted changes to the combined recovery path increased the likelihood of S2 success:
1. Filter S1's installed flows from the network observation
2. Simplify the handoff to omit S1's decision/action details
3. Strengthen the system prompt to explicitly disambiguate S1's outcome from route existence

## Changes Made

### 1. Observation Filtering (`examples/noms27/recovery_campaign/s2.py`)

**Problem:** S2 observed S1's bypass flow (`cookie=0x3101`) in the network-wide telemetry, creating confusion about the actual network state.

**Solution:** Added `_s1_flow_cookies()` helper that extracts cookie values from S1's attempted action flows. Modified `decision_view()` and `route_view()` to accept an optional `exclude_cookies` parameter that filters out matching flows from the projected observation.

**Implementation:**
- Extract cookies from `handoff["s1_actions"][*]["result"]["flow"]`
- Filter observation flows by matching cookie strings
- Applied only when a handoff exists (combined treatment)
- Full S1 action records remain in retained evidence

### 2. Simplified Handoff (`examples/noms27/recovery_campaign/s2.py`)

**Problem:** The `route_handoff_view()` exposed S1's decision, attempted action, and outcome, potentially biasing S2 toward "S1 tried and failed, so no route exists."

**Solution:** Simplified `route_handoff_view()` to return only:
```python
{
    "cause": handoff["cause"],
    "remaining_seconds_at_handoff": handoff["remaining_seconds"],
}
```

**Rationale:** S2 needs to know *why* it's being invoked and *how much time* remains. It does not need to know what S1 specifically tried. The full handoff record (with all S1 details) remains in the runner's evidence for post-hoc analysis.

### 3. Strengthened System Prompt (`examples/noms27/recovery_campaign/s2.py`)

**Problem:** The model could misinterpret S1's failed verification as evidence that no network-wide route exists.

**Solution:** Added explicit guidance to `ROUTE_INSTRUCTIONS`:
```
"S1's local attempt was insufficient; its outcome does not determine
whether a network-wide route exists. Decide from the current
observation, specifically surviving_links and port states."
```

**Rationale:** Reinforce that S1's local scope and outcome are separate from S2's broader permissions and the actual network topology.

### 4. Replay Script Update (`scripts/recovery-03-replay.py`)

Updated the non-incident replay tool to apply all three production changes:
- Extract and filter S1's flow cookies from the observation
- Use the simplified handoff view
- Apply the strengthened system prompt
- Removed the `--clarify-handoff` option (guidance is now in the base prompt)

## Test Results

### Offline Validation

- **28/28** ticket-03-specific tests pass (including updated assertions for simplified handoff and observation filtering)
- **1,462/1,462** full regression tests pass
- **3 skipped** (pre-existing)
- **124 warnings** (pre-existing deprecation warnings)
- Lint, formatting, and type checks all clean

### Non-Incident Replay

Replayed the failed live trial `development-live-combined-001` with the modified projection:

| Metric | Before (failed) | After (replay) |
|--------|----------------|----------------|
| Status | `unable` | `repair` ✅ |
| Route | `[]` | `["h1", "s1", "s3", "s4", "h2"]` ✅ |
| Input tokens | 5,996 | 928 |
| Output tokens | 18 | 51 |
| Latency | 1.72s | 1.16s |

**Key observations:**
- Model now selects the correct bypass route through s3
- Input tokens dropped from 5,996 to 928 (closer to direct-S2 baseline's 863)
- Compiled actions correctly install forwarding on s1, s3, s4
- This is a non-incident replay (no live service verification)

### Evidence Verification

The replay request shows:
- **Handoff:** `{"cause": "verification_failed", "remaining_seconds_at_handoff": 8.59}` (simplified)
- **S1 flows filtered:** Only the original failing flow (`cookie=0x2b5d`) remains; S1's bypass (`cookie=0x3101`) is removed
- **System prompt:** Includes explicit S1 disambiguation guidance

## Remaining Blockers

These changes address the **model decision blocker** (S2 returning `unable` when a route exists).

The **timeout criterion blocker** remains open per Pedro's 2026-11-07 decision:
- Unknown S1 inference/action termination still denies S2 admission
- Containment mechanism required before the timeout criterion can be checked
- This is a separate policy/safety concern, not a model capability issue

## Next Steps

1. **Live trial required:** Run a fresh combined incident with the modified projection to confirm live service recovery (not just replay)
2. **Timeout criterion:** Resolve the containment mechanism for S1 timeout handling
3. **Ticket 04:** Once both blockers are cleared, advance to confidence-gated combined treatment

## Files Modified

- `examples/noms27/recovery_campaign/s2.py` — observation filtering, handoff simplification, prompt strengthening
- `scripts/recovery-03-replay.py` — replay tool updated to apply production changes
- `tests/examples/test_recovery_campaign_ungated.py` — updated assertions for new handoff structure and observation filtering

## Evidence Archives

- Replay request/response/summary: `/home/pedro/projects/mininet-ai/live-acceptance/recovery-03-acceptance/replay-003-filtered-handoff/`
- Original failed trial: `/home/pedro/projects/mininet-ai/live-acceptance/recovery-03-acceptance/recovery-03-evidence-001/`
