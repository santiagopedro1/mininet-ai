# V2 latency acceptance

Authoritative contracts: ADRs 0001–0006 and
`docs/fixtures/link-failure-latency-seed-v1-0.json`. Rootless results are not live
acceptance. The human acceptance owner must retain commands, environment, output
directories, validation diagnostics and actual results. **Live remains unverified.**

## Automated ADR-0005 checklist

| Requirement | Automated boundary |
| --- | --- |
| Generator seed/version, streams, graph, placements, ports, paths, failure order | `tests/experiments/test_seeded_scenarios.py`, runner golden manifest test |
| Schema variants/enums, references, duplicate IDs, sequence gaps, counts, corruption, truncated/incomplete artifacts | `tests/experiments/test_latency_artifacts.py` and published fixture matrix |
| Same-domain/zero/missing/malformed ticks, pre-onset recognition, post-recovery action, late/failed phases | `tests/runtime/test_instrumentation.py`, artifact tests, runner summary tests |
| Fixed slots/misses/no backfill, all trial outcomes, bounded cleanup, frozen results, warm-up separation, admission races | `tests/experiments/test_trial_coordinator.py`, end-to-end runner tests |
| Normal Provider terminal proof versus local interruption/cancel, unknown containment abort | Provider adapter tests in `tests/agents/`, recorder/coordinator/runner tests |
| Durable v2 layout, full agent/capability seams, storage loss, interruption, summaries and explicit live skip | `tests/experiments/test_latency_runner.py` |

Project checks (repository root):

```bash
uv run ruff check .
uv run pyright
uv run pytest -q
```

Check formatting for changed Python files with `uv run ruff format --check
<paths>`. A whole-repository format audit also reports unrelated pre-existing
drift; do not silently reformat unrelated code in this integration slice.

Implementation validation (2026-10-19): **822 tests passed, 4 skipped, 179
subtests passed**; Ruff passed; Pyright reported zero errors; formatting passed
for all 14 touched Python files. The skipped tests are not live acceptance.
Both review axes against `main` completed, with concrete contract findings fixed;
one nonblocking policy-serialization coupling heuristic was deferred.

## Opt-in live recovery and teardown

Requires Linux/root, installed Mininet and project dependencies, OVS service,
`ovs-vsctl`, `ovs-ofctl`, `ip`, and `ping`. Use an isolated machine/VM, not a
shared network experiment host. The tooling uses the maintained runner,
`SubstrateTrialSession`, agent/capability adapters, budgets and validator.

```bash
sudo env MININET_AI_LATENCY_LIVE=1 "$PWD/.venv/bin/python" \
  scripts/accept-latency-live.py --mode recovery --output "$PWD/live-recovery-001"
```

This mode uses deterministic Agno policy responses for seed 0, one measured
attempt, no warm-up. Setup/injection/response/cleanup bounds are 60/10/60/30
seconds, with an outer 200-second worker-process limit. It must show both
directions failing after injection, then three complete consecutive success
rounds bracketed by failed-link-down checks, and **verified** network absence,
stopped workers and Provider containment. Inspect the persisted evidence.

Exit **77** is unavailable/not opted in: **UNVERIFIED**, not pass. Exit 1,
timeout, unknown cleanup, failed baseline or no-observed-disruption is not a
pass either. The current legacy Mininet adapter may lack sufficient resource-
absence proof; this must remain a failed/unverified live gate. This ticket does
not implement the privileged helper. After interruption/outer process timeout,
manually inspect and remove owned resources before retrying; killing the worker
does not certify cleanup or remote termination. Never run another trial over
uncertain leftovers.

## Provider-evidence acceptance (human owner)

Install the requested Ollama model; record Provider version/configuration and
resolved artifact identity if independently available. The manifest preserves an
explicit unknown identity otherwise. Run the same workflow with the actual adapter:

```bash
sudo env MININET_AI_LATENCY_LIVE=1 "$PWD/.venv/bin/python" \
  scripts/accept-latency-live.py --mode provider \
  --model qwen3.5:latest --ollama-host http://localhost:11434 \
  --output "$PWD/live-provider-001"
```

In addition to recovery/teardown requirements, inspect same-trial/invocation
`provider_request_started` → `provider_request_terminal` normal-response records.
All requests must have protocol-terminal evidence before verified cleanup. This
normal-response test does not certify a remote termination protocol.

Separately, on an isolated Provider, arrange a request that outlasts the local
deadline (e.g. a deliberately slow model and configured reasoning timeout), then
run the maintained CLI with two requested measured attempts, zero warm-up and a
short cleanup budget. Retain local timeout/cancellation evidence and confirm:

1. No remote-terminal acknowledgement/status is fabricated.
2. Outstanding requests leave Provider containment unknown at cleanup expiry.
3. Run aborts with only one started attempt and one unstarted remainder.
4. The earlier frozen trial outcome is unchanged.
5. Inspect the Provider and restore quiescence manually before another run.

Ollama has no supported authoritative remote cancellation/status adapter here.
Do not claim termination based on socket closure, local timeout, completed local
workers, or factory closure. Native/model timings remain supplementary.

The human owner must attach both live artifacts and this negative-containment
result to issue #78 (and the parent acceptance tracking issue #53). Do not close
the live acceptance gate based on rootless tests or skipped tooling.
