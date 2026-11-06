# Six-class matched blocks: offline implementation

## Status and authority

Ticket 05 only, based on retained ticket-03 integration `1b0ea1a`.
The worktree and integration HEADs matched before editing; the worktree was clean.
Pedro authorized **offline implementation and mechanism tests only** despite the
unresolved ticket-03 prerequisite. Nothing here closes that prerequisite, approves
budgets, freezes the scientific campaign, or authorizes held-out execution.

The seam remains declared configuration → `run_campaign` → exported evidence.
`blocks.py` composes that existing incident boundary, rather than implementing a
second recovery policy. Existing cancellation, termination, cleanup and handoff
containment checks remain authoritative.

## Structural scenarios and placements

`scenarios.py` declares six classes:

| Classes | Topology/forwarding | Structural regime |
| --- | --- | --- |
| `diamond_link`, `diamond_switch` | Diamond, complete downstream backup forwarding | Local repair sufficient |
| `extended_link`, `extended_switch` | Extended diamond, missing downstream forwarding on two surviving backup switches | Broader repair required |
| `tail_link`, `tail_switch` | Extended diamond with a serial tail, cut link or transit switch | No surviving path |

Every class has one calibration placement (`p0`) and two held-out placements
(`p1`, `p2`). These are explicit topology-instance/fault pairs: primary-arm lengths
vary by instance; serial-tail fault positions also vary. They are not a claim of
unseen-network generalization or approved/final scenario realism.

Before any incident runs, graph connectivity, healthy/faulted initial forwarding,
the single-switch catalogue redirect, and broader forwarding-only repairs establish
the regime. Structural checks have no treatment-outcome input. Repairable regimes
retain path witnesses; serial-tail cuts retain structural disconnection. Host and
attachment switches survive. Switch isolation disables **all incident links** for
the observation window, not a switch-process crash, restart or state corruption.
Reversing even one isolated link invalidates persistence evidence.

All treatment slots share the same initial forwarding and installed resources.
Complete downstream backups exist only in the diamond regime; broader-repair
instances deliberately require new downstream forwarding. No automatic restoration
is allowed. S1 still has the same single `s1` bypass catalogue and port numbers.
Its common prompt now avoids falsely asserting complete downstream forwarding in
every class; it remains identical across available S1-bearing treatments and
contains no evaluator class/fault label. S2 validation uses the frozen scenario's
port authority, plus observed up ports. The original diamond authority remains
the default for older incidents. Unknown switches, resource revival and
non-forwarding actions remain forbidden.

## Manifest and future allocation

`build_manifest()` produces a serializable, validated development manifest.
Calibration traffic seeds 1000–1029 and evaluation seeds 2000–2029 are disjoint;
environmental seeds are also retained and matched. The manifest reserves:

- Primary: 6 classes × 2 held-out placements × 30 repetitions × 4 slots = **1,440**.
- Higher load: the same placements/repetitions × direct S2 and both combined
  slots = **1,080**.
- Calibration: at least 30 repetitions per placement, separate from those totals.

The primary loads are 10% and 70% of bottleneck capacity, 1,200-byte payloads.
`plan()` materializes placement, topology hash, split, traffic/environment seeds,
fault timing, load, repetition and treatment slots. `combined_gated` is reserved,
**not implemented or silently substituted**. Gate-disabled selection cannot reduce
these allocation slots. Feasibility and replacement runs are additional. No model
seed is claimed to make inference deterministic.

## Controlled execution and artifacts

Network-free manifest check/export:

```bash
python -m examples.noms27.recovery_campaign.blocks \
  --check --write-manifest run-evidence/recovery-05-manifest.json
```

Small offline run (all six calibration placements, three available treatments,
two independent invocations per placement, and one invalid-block replacement):

```bash
python -m examples.noms27.recovery_campaign.blocks \
  --manifest run-evidence/recovery-05-manifest.json --controlled \
  --evidence run-evidence/recovery-05-controlled \
  --repetitions 2 --invalid-first essential_measurement
```

Use `--load 0.7` for the higher-load controlled rehearsal; it runs direct S2 and
combined ungated only, retaining the future gated slot in allocation evidence.
Output paths must be new; existing freezes/evidence are never overwritten.

The controlled adapter constructs no network, sockets or model clients. Each
trial owns fresh graph, forwarding and decision state. Scripted responses are
recorded as mechanism validation, including requests/responses for every repeat.
Packet deliveries follow an independent directed forwarding walk and action
timeline, not policy status or structural regime labels. The existing packet
evaluator computes service recovery and observes the full deadline plus five
seconds, including unsuccessful serial-tail trials. These synthetic packets and
scripts **are not independent live measurements or effectiveness evidence**.
Synthetic traffic is deterministic: seeds are retained split/matching inputs,
not evidence that real workload/environment variability has been exercised.
Scripted token values are accounting fixtures, not measured model resource use.

The rehearsal only executes calibration placements. Its six-second deadline,
three-second S1 cap and 9.6-Mbit/s synthetic capacity are explicitly unapproved
development fixtures, not newly approved budgets or workload settings. The current
live adapter still accepts only the original diamond incident. Six-class configs
reject live purpose; the rehearsal rejects non-mechanism runtimes. No held-out
executor or scientific approval override is provided.

Retained artifacts:

- `manifest.json`, its SHA-256, `allocation.json` (including all future slots),
  and `structural-checks.json`, written before running treatments.
- `blocks.json`: seeded randomized execution order, matching inputs, per-trial
  reset receipts, validity, complete/incomplete block status and replacement links.
- Unique block/trial directories containing the existing incident configuration,
  event journal, packet records, model/action records, outcome, provenance and
  containment/cleanup evidence.

Injection, initial-state and essential-measurement failures invalidate a block;
all original trials remain on disk and a whole fresh-ID matched block replaces it.
Replacements preserve matching inputs, including seeds; the bounded replacement
limit stops rather than retrying until success. Model/malformed-response/action
failures and no-permitted-repair outcomes remain valid. A containment denial stops
before any subsequent trial or replacement. Its incomplete block is not relabeled
experimentally invalid, and recorded treatment validity is unchanged.

## Remaining acceptance

The parent handles independent review, integration and the paper tracker. Still
pending: ticket-03 prerequisite acceptance; real six-class substrate/injection
integration; live feasibility across all classes and treatments; measurement and
provider containment validation; approved numerical budgets/resources; calibrated
confidence gating; frozen prompts/catalogues/configuration; Pedro's approval before
any held-out execution. No new scientific claims or effect estimates are made.

## Review follow-up: ingress-matched controlled forwarding

The P2 review finding on `93ddbc8` identified a controlled-adapter mismatch with
the real S2 contract: S2 actions carried `in_port`/`out_port`, but installation
and packet delivery previously used a switch-wide next hop. An accepted rule for
the wrong ingress could therefore falsely appear to restore h1 traffic.

The controlled adapter now stores rules by switch and ingress port. Initial
directional forwarding is compiled into explicit ingress/output rules for the
declared primary and backup predecessors, including both ingresses at merge
switches. Missing downstream forwarding stays missing. Reset evidence retains
`initial_rules`; diagnostic flow records contain both match and output ports.
Initial health and packet delivery use the installed rules, carrying the incoming
neighbor/port through every hop and detecting loops by switch/ingress state.
S1 changes only its frozen port-1-to-port-3 rule; S2 installs exactly its declared
ingress/output pairs without replacing rules for other ingresses.

A wrong-ingress action can still execute successfully—it is not an invalid
experiment or malformed action—but it does not restore service when h1 packets
do not match it. Paired configuration-to-evidence regressions cover the reported
downstream-primary-link fault and a downstream switch in a multi-switch repair.
Correctly matched variants must recover; wrong-ingress variants retain valid
unsuccessful/censored outcomes. These remain synthetic mechanism checks, not
held-out effectiveness or live evidence. No runner containment rule was changed.
