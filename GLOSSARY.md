# Mininet AI

Mininet AI is a declarative experiment compiler and runtime for agentic
networking research. This glossary defines project-specific terms.

## Language

**Recovery Policy**:
A predefined action or set of actions intended to restore required network
communication when its applicability conditions are satisfied.

**Recovery Policy Selection**:
Choosing a recovery policy from diagnostic evidence, or deciding that none of
the available policies should be applied.

**Escalation**:
Referring an incident to another decision-maker when local evidence or recovery
capabilities are insufficient.

**System One**:
The local bounded-decision agent that selects a recovery policy from current
diagnostics.
_Avoid_: local model, edge agent

**System Two**:
The experiment-wide decision-maker invoked directly or through escalation.
_Avoid_: global model, fallback agent

**Confidence Gate**:
A runner-side policy that replaces a post-fault local choice with escalation
when the reported confidence is below a configured threshold.
_Avoid_: confidence filter, model fallback

**Post-Fault Check**:
A completed evaluation of diagnostics observed after a fault, including a failed
decision attempt. A decision based on observations collected before the fault is
not a post-fault check.

**Maintained Example**:
A user-facing scenario in `examples/` with a README, validated and planned as
part of the test suite. Each maintained example demonstrates a coherent
research workflow or a cluster of supported features.
_Avoid_: demo, sample, tutorial

**Test Fixture**:
A specification in `tests/fixtures/specifications/` used for compiler and
runtime testing. Test fixtures are not user-facing and may use minimal or
artificial topologies.
_Avoid_: test example, mock example

**Substrate**:
The component that owns an experiment's topology lifecycle, from construction
through teardown.
_Avoid_: driver, backend, platform

**Experiment Cleanup**:
The release of resources owned by an experiment, distinct from restoring the
whole machine to an earlier state.

**Cleanup Verification**:
Evidence-based confirmation that an experiment's cleanup obligations have been
met, distinct from successful execution of a cleanup procedure.

**VM Recovery**:
An explicit operator action to remove abandoned experiment resources from a
disposable VM when normal experiment cleanup is unavailable or insufficient.
_Avoid_: Recovery Policy

**Provider**:
The inference service that supplies the model responses an agent reasons with.
_Avoid_: model backend, LLM provider, AI provider

**Independent Agent Invocations**:
Agent invocations in the same experiment that do not require one another's results
and do not make incompatible changes to shared network or decision state.

**Primary Goal**:
The main purpose of a maintained example. One of: `feature demonstration`
(showcases specific features), `realistic scenario` (a plausible research use
case), or `both`.
_Avoid_: objective, purpose, aim
**Run Evidence**:
The retained record of an experiment run's inputs, provenance, activity,
measurements, and outcomes, including failures and known gaps.
_Avoid_: run memory

**Shared Operational State**:
Mutable coordination information shared by agents within a run, distinct from
the historical record of its changes and from learned memory.

**Agent Session**:
The context of an agent's invocations within a run, including conversation
history and local state used for continuity.

**Learned Memory**:
Knowledge retained by an agent for reuse beyond an individual invocation,
with reuse bounded by its declared scope.

**Run-Scoped Memory**:
Agent memory whose reuse is confined to one run.

**Agent-Scoped Learned Memory**:
Learned memory associated with an agent for reuse across runs within a selected
storage root, distinct from a run's evidence and session context.
