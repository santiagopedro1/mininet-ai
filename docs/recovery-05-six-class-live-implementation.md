# Six-class live integration: implementation

## Scope and authority

Ticket 05 slice two, on branch `feat/recovery-05-six-class-live` (base
`b56af8f`, the retained ticket-05 integration record). Pedro authorized
offline implementation and tests plus a local VM mechanism smoke — no model
machine contact (models are scripted fixtures), no held-out or campaign-scale
execution, no feasibility/budget approval, no pushes. Ticket 06 still owns
feasibility and budgets; ticket 03's live acceptance stands.

## Capability built

The scenario seam already carried frozen `Scenario` instances to
`run_campaign`, S2 port authority and emitted evidence; the live side was
diamond-only. What this slice adds:

1. **`substrate.py`** — a pure planner: declared scenario → nodes/links/port
   map, ingress-matched flow plans (priority 100 active initial forwarding,
   priority 200 preinstalled downstream backup, table-miss drop 0x1100,
   deterministic per-scenario cookies), fault/reset targets by fault kind,
   and the owned cleanup inventory. Contract tests cross-check the compiled
   rule identity against the verified controlled adapter for every placement
   and assert local/broader/no-path restoration over the plans alone. The s1
   port-1→port-3 catalogue bypass is runtime-only in every plan.
2. **`harness.py`** — a scenario-directed Mininet/OVS harness (diamond's
   sibling, not its replacement): pre-existence refusals for owned bridges and
   interfaces, plan-exact topology and baseline flows, readiness (fail-secure,
   no controllers, OpenFlow13, host MAC and permanent neighbor), persistent
   link-cut and transit-switch isolation fault primitives, reset, and a
   budget-sharing absence-verified cleanup. Isolation records
   `switch_process_stopped`/`state_corruption` so a crash or corroded flow
   state is distinguishable from the intended fault. Unit tests use injected
   `root_command`/`exists_command`/`net_factory`/`environment_check` seams.
3. **`live.py` dispatch** — LiveRuntime setup/inject/observation/admission/
   cleanup dispatch on the frozen scenario instance. The diamond path keeps
   its byte-compat behavior; the six-class path builds the harness from the
   plan, freezes per-link bottleneck bandwidth, checks groups on every
   scenario switch, writes plan-derived network manifests (including
   preinstalled rules), and revalidates S2 action admission with the frozen
   port authority instead of diamond PORTS. Shared model warm-up was
   extracted unchanged. Port up/down parsing accepts numeric (state 0) and
   ovs 3.6 (LIVE/LINK_DOWN) dump formats.
4. **`runner.py`** — six-class `live_feasibility` configs are constructable
   only with a frozen scenario instance; the campaign runtime gate now
   requires a live runtime kind for live-purpose matched scenarios and a
   mechanism kind otherwise. A persistent isolation whose switch process
   failed or whose flow state corroded invalidates the trial
   (`underlying_switch_failed_during_isolation`). Campaign-scale execution
   still waits on the later feasibility/budget/calibration/freeze gates; the
   blocks rehearsal keeps rejecting non-mechanism runtimes.
5. **`smoke.py`** — a real-substrate, scripted-model mechanism smoke CLI for
   one declared placement/treatment. It constructs the network through the
   same seam, drives a full incident with `evidence_kind =
   mechanism_validation`, refuses existing evidence directories, and prints
   honest outcome/validity/containment blocks.

## Offline validation

- Focused: campaign + s2 + live-six contracts, 118 passed (45.86 s);
  8 six-class and 12 harness/17 substrate boundary tests all pass.
- Final full regression after the fixes below: **1,495 passed, 3 skipped,
  179 subtests passed, 124 warnings** in 203.24 s (the restored observation
  test is the 1,495th), plus repository ruff, scoped formatting, and scoped
  pyright clean. The six-class `refuses live purpose` boundary test
  was replaced by the capability-boundary test documented in the commit.

## VM mechanism smoke (Fedora 43 box, OVS 3.6, Mininet 2.3)

Scripted models; every smoke trial runs a full 5 s baseline + 6 s deadline
+ 5 s post window with real UDP workers and real ovs flows.

| Trial | Configuration | Outcome |
| --- | --- | --- |
| `diamond-link-s1-only-003` | diamond_link-p0, s1_only | valid; S1 bypass over preinstalled downstream verified; recovered |
| `diamond-switch-s1-only` | diamond_switch-p0 isolation, s1_only | valid; both incident links persistently down; recovered |
| `extended-switch-combined-004` | extended_switch-p0 isolation, combined_ungated | valid; S1 verification fails, escalates, multi-switch S2 repair recovers |
| `tail-switch-s1-only-001` | tail_switch-p1 isolation, s1_only | valid; executed; no surviving path; unsuccessful censored outcome retained |

Retained failures are kept as originals (`-002` contract probes, first
`br-exists` exit-code discovery, pre-fix authority and observation runs under
`extended-switch-combined..003`), demonstrating live originals-being-retained
semantics. Evidence is preserved under the worktree's ignored
`run-evidence/recovery-05-smoke/` and the tarball
`/tmp/opencode/recovery-05-smoke-evidence.tgz`
(SHA-256 `8a5bbd2d93b9231d68063194d22d66a3d8b9e1604f5b09b7a22a6916f0c613fb`).
The VM's synced checkout at `/vagrant` was not written except removing a
misplaced `recovery-05-work/` directory extracted there by mistake (which
never overwrote tracked/state-changed files; the host checkout's pre-existing
uncommitted work was verified untouched by timestamps).

## Fix commits during validation (live smoke findings)

1. `harness.py`: treat any nonzero ovs existence exit as absent (Fedora ovs
   returns 2, not 1).
2. `smoke.py`: honor the zero-generation-token native choice contract.
3. `live.py` observation: replace the leftover diamond `PORTS` loop with the
   computed peers map (scenario path had an unbound local at S2 telemetry);
   restored the guard test.
4. `live.py` admission: re-observation validation now uses the frozen
   scenario port authority (diamond default wrongly rejected valid
   broader-repair actions).

## Remaining acceptance

Everything else in ticket 05's pending list stands: independent review of
this slice, integration into the retained ticket-05 integration line, the
live feasibility/numerical-budget/calibration/freeze path (ticket 06/07),
and Pedro's held-out authorization (ticket 08). No scientific claims,
effect estimates or budget numbers were produced.
