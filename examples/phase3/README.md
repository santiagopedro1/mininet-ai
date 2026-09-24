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

Run the complete example in one process with:

```bash
uv run python -m examples.phase3
```

This prints the invocation result and teardown state as JSON, and writes the
full event stream to `.mininet-ai/phase3-demo-audit.jsonl`. Choose another
destination or intent when needed:

```bash
uv run python -m examples.phase3 \
  --audit-log /tmp/phase3-audit.jsonl \
  --intent "Inspect s1 and apply the declared safe change"
```

The workflow deliberately stays in one process because the fake substrate is
in-memory. It compiles and deploys the experiment, discovers both example
providers, invokes `edge-operator@s1`, records the audit trail, and tears down
the run before exiting. It requires neither root access nor Mininet.

Run the automated acceptance checks with:

```bash
uv run pytest -q tests/acceptance/test_phase3.py
```

The suite also verifies typed results and audit records and proves that changing
the action target to `s2` is rejected before the provider can act.
