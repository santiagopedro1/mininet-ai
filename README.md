# Mininet AI

Mininet AI is a declarative experiment compiler and runtime for agentic networking
research. Define a topology, choose where agents attach, and specify what they
can observe and change.

Agno handles agent execution, models, sessions, and memory. Mininet AI owns the
network lifecycle, scheduling, coordination, capability authorization, shared
operational state, and experiment ledger. Agents propose actions; authorized
capabilities execute them.

## Current capabilities

The current specification contract is `mininet-ai/v1alpha3`. When upgrading
from `v1alpha2`, update `apiVersion` on the experiment and every agent blueprint
and capability, then recompile saved deployment plans. See the
[migration guide](docs/compatibility.md#public-specification-v1alpha2-to-v1alpha3).

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
uv run mininet-ai validate examples/getting-started/experiment.yaml
uv run mininet-ai plan examples/getting-started/experiment.yaml
uv run mininet-ai run examples/getting-started/experiment.yaml --dry-run
```

Validation, planning, and dry runs need neither root access nor a running Mininet
network or model service. Use `plan --format json` for machine-readable output.

The maintained [iperf throughput example](examples/iperf-throughput/README.md)
uses two Ollama-backed host agents to start an iperf server and client across an
OVS switch. See its guide for model setup, traffic inspection, and limitations.
For a simpler starting point, see the [getting started example](examples/getting-started/README.md).

## Running a live experiment

Live Mininet/OVS execution needs Linux networking privileges. Use the disposable
Vagrant VM:

```bash
vagrant up --provision
vagrant ssh
cd /vagrant
```

Set `model.parameters.host` in both example agent blueprints to an endpoint
reachable from the VM, with the example's `qwen3.5:latest` model available:

```yaml
model:
  provider: ollama
  name: qwen3.5:latest
  parameters:
    host: http://YOUR_OLLAMA_HOST:11434
```

The explicit host overrides `OLLAMA_HOST`. Omit `parameters.host` to preserve
Agno/Ollama's environment and default behavior. Keep credentials in environment
variables, not in the endpoint URL. The checked-in example uses
`http://localhost:11434`, which requires Ollama inside the VM unless you change it.

```bash
sudo scripts/vm-run.sh mininet-ai run \
  examples/iperf-throughput/experiment.yaml \
  --intent 'iperf-server@server=Start the iperf server on server.' \
  --intent 'iperf-client@client=Run a TCP throughput test against 10.0.0.12.' \
  --verbose
```

Pass startup intents as `AGENT=TEXT`, server first. Runs remain in the foreground
until `Ctrl+C`
or a stop request, then drain accepted work and tear down the network.
`--stop-after-intents` instead stops after all submitted intents finish;
for iperf, keep the run alive while observing traffic because process-start
completion is not throughput-test completion.

From another VM terminal:

```bash
cd /vagrant
sudo scripts/vm-run.sh mininet-ai status <run-id>
sudo scripts/vm-run.sh mininet-ai topology <run-id>
sudo scripts/vm-run.sh mininet-ai agents <run-id>
sudo scripts/vm-run.sh mininet-ai invoke \
  <run-id> iperf-client@client \
  --intent 'Run a TCP throughput test against 10.0.0.12.'
sudo scripts/vm-run.sh mininet-ai stop <run-id>
```

`invoke` queues the intent in the foreground owner's scheduler and returns an
accepted event (`--format json`) or its ID. Acceptance is not execution success;
follow the owner's log and ledger for completion or failure. Submit only to
agents with a manual trigger. `agents <run-id>` lists compiled instance IDs and
whether they accept manual intents; `--format json` includes the run ID, plan
digest, `agents`, and `manualAgents`. Both commands contact the live owner;
neither reads or recompiles experiment YAML. Editing the original files does
not change the running experiment.
Run both commands as the same OS user on the same machine; default discovery
works from any working directory. Root uses `/run/mininet-ai/control`; other
users use `$XDG_RUNTIME_DIR/mininet-ai/control` when configured and valid, or
`/tmp/mininet-ai-<uid>/control` when unset (regardless of `TMPDIR`). Invalid XDG
configuration fails rather than falling back. `--control-dir` overrides the base;
use the same absolute path on all commands. Each endpoint is
`<base>/<run-id-hash>/control.sock`. Directories are private (0700), sockets are
0600, and shutdown removes only the owner's socket and empty run directory.
Restart existing runs after upgrading: the new per-run layout cannot address an
old flat socket, even with an explicit legacy `--control-dir`. There is no
automatic retry after a timeout because the owner may have accepted the intent.
Intents are limited to 8192 characters. Configure databases, models, and plugins
on `run`, not `invoke`.

`status` and `topology` support `--format json`. Saved output defaults to
`.mininet-ai/<run-id>/`: `logs/run.log`, `dbs/ledger.sqlite3`,
`dbs/shared-state.sqlite3`, `dbs/agno.sqlite3`, and an `artifacts/` directory.
`--artifact-root` changes the saved-output root, not socket discovery. Startup
prints and logs absolute effective paths; `--verbose` streams progress to stderr.
Explicit `--log-file`, `--ledger-db`, `--shared-state-db`, and `--agno-db` paths
remain honored. Existing output is never migrated or deleted, and run directory
collisions fail. Failed starts retain logs and databases for diagnosis.

When any agent requests `learned.scope: agent`, the default Agno store is instead
`<artifact-root>/memory/agno.sqlite3`, shared across runs under that root. Agno
uses one database for both sessions and memory; sessions remain keyed by run,
while learned memory uses the stable agent identity. Use the same absolute
artifact root across working directories, or an explicit shared `--agno-db`, to
reuse agent memory. Run ledgers and shared operational state remain per-run.
If the legacy `<artifact-root>/agno.sqlite3` exists, agent-scoped memory requires
an explicit `--agno-db` selection; choose that file to retain previous learning.
The CLI never silently replaces existing learned memory with a fresh store.
No multiple-database Agno routing or schema/protocol changes are introduced.

Treat saved artifacts as sensitive: they may contain prompts and observations.
Keep credentials outside YAML. Private permissions and SQLite locking are still
required; on VirtualBox shared mounts, use a private local artifact root, e.g.
`sudo mininet-ai run experiment.yaml --artifact-root /var/lib/mininet-ai/runs`.
Sockets use the local runtime filesystem even when running from `/vagrant`.
`--dry-run` creates no runtime or saved-output directories.

## Examples

| Example | Description | Substrate | Provider |
| --- | --- | --- | --- |
| [Getting Started](examples/getting-started/README.md) | Minimal offline experiment | `fake` | `mock` |
| [Autonomous Network Operation](examples/autonomous-operation/README.md) | Self-healing network with event triggers, detectors, rollback | `fake` | `mock` |
| [Hierarchical Routing Coordination](examples/hierarchical-routing/README.md) | Three-tier routing with hierarchical coordination | `mininet-ovs` | `ollama` |
| [iperf Throughput](examples/iperf-throughput/README.md) | Ollama-driven TCP throughput test | `mininet-ovs` | `ollama` |

See the [examples index](examples/README.md) for a feature matrix and progression guide.

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

Maintained examples in `examples/` are validated and planned as part of the
test suite (`tests/examples/test_examples.py`). After an intentional compiler
or specification change, run the tests to verify the examples still compile.

## Plans

### v1alpha3

- [x] Support submitting `--intent` from another terminal.
- [x] Allow the Ollama host to be specified in the configuration file.
- [x] Support noncanonical names in Mininet networks, if possible.
- [x] Add better, more practical examples.
