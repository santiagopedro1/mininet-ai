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

**Primary Goal**:
The main purpose of a maintained example. One of: `feature demonstration`
(showcases specific features), `realistic scenario` (a plausible research use
case), or `both`.
_Avoid_: objective, purpose, aim
