# Mininet AI

Mininet AI is a declarative experiment compiler and runtime for agentic
networking research. This glossary defines project-specific terms.

## Language

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

**Link-Failure Trial**:
A single experiment repetition that establishes healthy connectivity, introduces
one link failure, and observes the agents' response and any resulting recovery.

**Connectivity Recovery**:
Restoration of host-to-host packet delivery over an alternate path while the
failed link remains unavailable. Successful configuration changes alone do not
establish connectivity recovery.
