# Phase 3 acceptance example

This rootless experiment demonstrates the Phase 3 extension seams without a
live model or privileged network:

- `agent-blueprints/edge-operator.yaml` defines a declarative agent using the
  deterministic `mock` model provider.
- `capabilities/custom-telemetry.yaml` declares a read-only telemetry
  capability.
- `capabilities/custom-action.yaml` declares a typed mutating capability.
- `providers.py` exports both implementations as `ProviderPlugin`
  descriptors, exactly as an external Python package would expose through the
  `mininet_ai.capabilities` entry-point group.
- `experiment.yaml` attaches the agent only to `s1`.

Run the automated completion demonstration with:

```bash
uv run pytest -q tests/acceptance/test_phase3.py
```

The suite compiles and deploys the fake substrate, discovers both providers,
invokes the agent through the public runtime and CLI, verifies typed results
and audit records, and proves that changing the action target to `s2` is
rejected before the provider can act.
