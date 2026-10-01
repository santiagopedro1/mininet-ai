# Specification fixtures

These YAML files are shared inputs for tests that need a valid compiled
experiment. They are test infrastructure, not maintained user examples.

## `compiler-multilayer/`

A fake-substrate specification with one reusable agent blueprint deployed at
global, control, data, and host layers. Compiler, agent, capability,
coordination, runtime, SDK, CLI, plugin, ledger, and golden-plan tests compile
or modify its normalized snapshot.

Keep the external blueprint and capability paths relative to `experiment.yaml`.
When an intentional compiler change alters its deployment plan, regenerate the
golden fixture with:

```bash
uv run python -m tests.update_golden_plans
```

## `mininet-ovs-smoke/`

A minimal live Mininet/OVS topology with one controller, one switch, and two
hosts. Live integration and observation tests use it to verify deployment,
inspection, recovery, and resource discovery.
