# Mininet AI

Mininet AI is a declarative experiment compiler and runtime for agentic networking
research. Define a topology, choose where agents attach, and specify what they
can observe and change.

Agno handles agent execution, models, sessions, and memory. Mininet AI owns the
network lifecycle, scheduling, coordination, capability authorization, shared
operational state, and experiment ledger. Agents propose actions; authorized
capabilities execute them.

## Current capabilities

The current specification contract is `mininet-ai/v1alpha2`.

- YAML and Python specifications, with inline definitions or external YAML files.
- Deterministic deployment plans, scoped placement, target selectors, and
  singleton, per-target, or per-group agent expansion.
- Fake and Mininet/OVS substrates, with topology inspection and network telemetry.
- Agno agents defined declaratively or through Python factories.
- Manual, interval, and event triggers; observation aggregation and detectors;
  bounded queues, concurrency, and restart policies.
- Independent, centralized, hierarchical, and distributed coordination with
  conflict arbitration.
- Capability authorization, schema validation, deadlines, postcondition checks,
  and supported rollback operations.
- Persistent sessions, shared state, audit records, model usage, and run manifests.

Placement currently describes logical scope, not enforced process or namespace
isolation. Python agent factories run as trusted code in the orchestrator.
Legacy generic agent/model providers remain deprecated; Agno is the active
runtime.

## Installation and quick start

Requires **Python 3.14+** and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run mininet-ai validate examples/iperf-throughput/experiment.yaml
uv run mininet-ai plan examples/iperf-throughput/experiment.yaml
uv run mininet-ai run examples/iperf-throughput/experiment.yaml --dry-run
```

Validation, planning, and dry runs need neither root access nor a running Mininet
network or model service. Use `plan --format json` for machine-readable output.

The maintained [iperf throughput example](examples/iperf-throughput/README.md)
uses two Ollama-backed host agents to start an iperf server and client across an
OVS switch. See its guide for model setup, traffic inspection, and limitations.

## Running a live experiment

Live Mininet/OVS execution needs Linux networking privileges. Use the disposable
Vagrant VM:

```bash
vagrant up --provision
vagrant ssh
cd /vagrant
```

Replace the Ollama endpoint below with one reachable from the VM, with the
example's `qwen3.5:latest` model available:

```bash
sudo env OLLAMA_HOST=http://YOUR_OLLAMA_HOST:11434 \
  scripts/vm-run.sh mininet-ai run \
  examples/iperf-throughput/experiment.yaml \
  --intent 'iperf-server@server=Start the iperf server on server.' \
  --intent 'iperf-client@client=Run a TCP throughput test against 10.0.0.12.' \
  --verbose
```

Pass startup intents as `AGENT=TEXT`, server first. Cross-terminal intent
submission is not yet supported. Runs remain in the foreground until `Ctrl+C`
or a stop request, then drain accepted work and tear down the network.
`--stop-after-intents` instead stops after all submitted intents finish;
for iperf, keep the run alive while observing traffic because process-start
completion is not throughput-test completion.

From another VM terminal:

```bash
cd /vagrant
sudo scripts/vm-run.sh mininet-ai status <run-id>
sudo scripts/vm-run.sh mininet-ai topology <run-id>
sudo scripts/vm-run.sh mininet-ai stop <run-id>
```

`status` and `topology` support `--format json`. Progress is written to
`.mininet-ai/run.log`; `--verbose` also streams it to stderr. Run history, Agno
sessions, and shared state are stored in private SQLite files under
`.mininet-ai/`. Treat these artifacts as sensitive: they may contain prompts and
observations. Keep model credentials outside experiment YAML.

## Writing experiments

Use the [YAML authoring reference](docs/yaml-authoring-reference.md) when
generating experiments, topologies, agent blueprints, or capabilities. It covers
field names, compatible placement, triggers, coordination, and validation.
Always run both `validate` and `plan` before live execution.

Export input schemas with:

```bash
uv run mininet-ai schema experiment
uv run mininet-ai schema agent-blueprint
uv run mininet-ai schema capability
```

The CLI also exports deployment-plan, runtime-event, coordination-message, and
coordination-outcome schemas. See
[compatibility and versioning](docs/compatibility.md) for contract-change rules
and [ADR 0001](docs/adr/0001-use-agno-as-v1-agent-runtime.md) for the Agno decision.

## Development

```bash
uv run pytest -q
uv run ruff check .
uv run pyright
```

Shared YAML test inputs live in
[`tests/fixtures/specifications/`](tests/fixtures/specifications/README.md).
Golden plans detect changes to the compiled contract. After an intentional
compiler or specification change, regenerate and review them:

```bash
uv run python -m tests.update_golden_plans
git diff -- tests/golden
```

For privileged integration tests and the VM environment check, run from the host:

```bash
scripts/test-vm.sh
```

The runner reports PASS, FAIL, or SKIP for each check, continues independent
checks after failures, and exits nonzero if any check fails. It checks tests,
lint, types, CLI output, networking prerequisites, live integration, and cleanup.
It invokes `mn -c`; use it only with the disposable VM. For manual
cleanup checks inside that VM:

```bash
sudo scripts/check-mininet-cleanup.sh snapshot
# Run and stop an experiment, then compare against the baseline:
sudo scripts/check-mininet-cleanup.sh check
```

`scripts/check-mininet-cleanup.sh recover` is an emergency fallback that may
remove every Mininet/OVS topology on the machine, not just the current run.

## Plans

From [TODO.md](TODO.md):

### v1alpha3

- [ ] Support submitting `--intent` from another terminal.
- [ ] Allow the Ollama host to be specified in the configuration file.
- [ ] Support noncanonical names in Mininet networks, if possible.
- [ ] Add better, more practical examples.
