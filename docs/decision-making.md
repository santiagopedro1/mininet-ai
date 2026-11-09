# System One and System Two

Mininet AI uses **System One** for direct, bounded decision-making and
**System Two** for deliberative reasoning through intermediate steps. These
approaches are independent of agent placement, network scope, action authority,
and whether an agent is invoked through escalation. The canonical definitions
are in the [glossary](../GLOSSARY.md).

## Models, agents, and execution

- A **System One decision model** supplies bounded decisions directly. We assume
  these models provide native typed outputs and calibrated probabilities.
- A **System Two model** supports deliberative reasoning, potentially including
  investigation, planning, and revision. It can also return typed answers and
  probabilities; output format alone does not determine the approach.
- A **System One agent** or **System Two agent** uses the corresponding approach
  within an experiment's configured observations, triggers, and capabilities.
- An **execution mechanism** integrates an agent's inference with the runtime.
  The current mechanisms are `bounded-decision` and `agno`; these identifiers
  describe execution contracts rather than model classes.

[Bounded-decision execution](bounded-decision.md) consumes native choices and
maps them to scenario-owned proposals. It validates responses and enforces
choice constraints, but does not establish probability calibration. Agno
supports deliberative agents; selecting Agno alone does not establish that a
particular invocation performs System Two reasoning.

Native typed outputs and calibrated probabilities are our assumed System One
model contract, not findings established for every configured model by runtime
checks. Calibration concerns how probabilities correspond to observed accuracy
across predictions; it does not guarantee correctness of an individual choice.
Schema-valid answers can still be wrong. Reported confidence and probability
remain distinct fields with the meanings and uses documented by the provider
and experiment.

## Placement and escalation are experiment choices

System One is not inherently local or limited to recovery policy selection.
The [diamond diagnosis example](../examples/diamond-recovery/README.md) uses
System One agents for action-free diagnosis. Its per-switch placement and
current-only observations are explicit scenario constraints.

System Two is not inherently experiment-wide and does not require broader
authority. In the
[unified recovery example](../examples/noms27/unified-system-1-2-recovery/README.md),
both agents are scoped to `s1` and have the same two capabilities. That runner
invokes the System Two agent only after an explicit escalation choice or a
post-fault confidence-gate replacement. This is a workflow rule, not a general
requirement for System Two. A failed local repair does not automatically cause
handoff, and a bounded `escalate` choice alone does not invoke another agent.

## Terminology source

The starting point is TypeSafe AI's
[Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
which describes fast, structured decisions and contrasts them with deliberate
reasoning. We adopt native typed outputs and calibrated probabilities as assumed
properties of System One decision models. The project's terminology does not
require Jev, a particular provider, training method, model size, or universal
latency threshold. The article's performance claims are not Mininet AI results.
