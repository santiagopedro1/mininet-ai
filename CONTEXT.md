# Mininet AI

Mininet AI is a declarative experiment compiler and runtime for agentic
networking research. This glossary defines project-specific terms.

## Language

**Maintained Example**:
A user-facing scenario in `examples/` with a README, validated and planned as
part of the test suite. Each maintained example demonstrates a coherent
research workflow or a cluster of v1alpha3 features.
_Avoid_: demo, sample, tutorial

**Test Fixture**:
A specification in `tests/fixtures/specifications/` used for compiler and
runtime testing. Test fixtures are not user-facing and may use minimal or
artificial topologies.
_Avoid_: test example, mock example

**Substrate**:
The network driver that owns the topology lifecycle. Two substrates are
available: `fake` (compile-time, in-memory, no root required) and
`mininet-ovs` (live Mininet/OVS, requires Linux networking privileges).
_Avoid_: driver, backend, platform

**Provider**:
The model provider that backs an agent's reasoning. The active provider is
`ollama` (requires a running Ollama server). The `mock` provider returns
deterministic responses for offline use. Legacy generic providers are
deprecated.
_Avoid_: model backend, LLM provider, AI provider

**Primary Goal**:
The main purpose of a maintained example. One of: `feature demonstration`
(showcases specific v1alpha3 features), `realistic scenario` (a plausible
research use case), or `both`. An example's primary goal is stated in its
README and in the examples index.
_Avoid_: objective, purpose, aim
