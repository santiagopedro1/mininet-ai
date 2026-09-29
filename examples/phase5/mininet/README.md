# Phase 5 Mininet/OVS coordination experiment

This is the live-network counterpart of the rootless Phase 5 example. It keeps
the same deterministic coordinator, two switch remediators, centralized graph,
and `reject` conflict policy, but deploys a real Mininet topology with one OVS
switch, two hosts, and the built-in controller. The winning proposal executes
`ovs-ofctl add-flow`; the conflicting proposal is rejected before a second
network mutation occurs.

Compile it without root privileges:

```bash
uv run mininet-ai validate examples/phase5/mininet/experiment.yaml
uv run mininet-ai run examples/phase5/mininet/experiment.yaml --dry-run
```

Run it only inside the disposable Vagrant environment used for Mininet tests:

```bash
vagrant up
vagrant ssh
cd /vagrant
sudo scripts/vm-run.sh mininet-ai run \
  examples/phase5/mininet/experiment.yaml \
  --intent 'primary-remediator@s1=Coordinate a safe forwarding repair for s1.' \
  --stop-after-intents \
  --verbose \
  --format json
```

Verbose progress is written to stderr, while the final JSON report remains on
stdout. The same progress is appended to `.mininet-ai/run.log`; override that
location with `--log-file PATH`. The owner automatically drains and tears down
Mininet after the intent's complete coordinated result is recorded. The final
JSON report
contains the versioned coordination outcome under
`continuous.invocations[0].coordination`. It should contain three messages,
three invocations, one `execute` arbitration decision, and one `reject`
decision with issue code `coordination.conflict.rejected`.

The live run writes ownership state under `/run/mininet-ai` and must retain its
foreground owner until teardown. If a normal stop fails in the disposable VM,
follow the repository's Mininet cleanup procedure; do not use emergency cleanup
on a shared machine.

## Ollama-backed agents

The default experiment uses deterministic model responses so coordination and
network behavior are reproducible. To create the Agno agents with the Ollama
model `qwen2.5:7b`, use `experiment-ollama.yaml`. Agent objects are created
lazily when the initial intent invokes the coordinator; the coordinator then
delegates to both remediators. This example uses an Agno Python factory to make
Qwen use prompt-parsed structured output; native recursive JSON-schema output
can expand the required OpenFlow command strings into invalid objects.

From inside the Vagrant VM, first verify that Ollama is reachable:

```bash
curl http://10.10.10.152:11434/api/tags
```

Then run the live experiment. Passing `OLLAMA_HOST` through `sudo` is required:

```bash
cd /vagrant
sudo env OLLAMA_HOST=http://10.10.10.152:11434 \
  scripts/vm-run.sh mininet-ai run \
  examples/phase5/mininet/experiment-ollama.yaml \
  --intent 'primary-remediator@s1=Coordinate a safe forwarding repair for s1.' \
  --stop-after-intents \
  --agno-db .mininet-ai/phase5-ollama-agno.sqlite3 \
  --ledger-db .mininet-ai/phase5-ollama-ledger.sqlite3 \
  --shared-state-db .mininet-ai/phase5-ollama-state.sqlite3 \
  --log-file .mininet-ai/phase5-ollama-run.log \
  --verbose \
  --format json
```

The coordinator and remediators use one persistent Agno instance each for the
duration of the run. After the initial intent's coordination and actions
finish, the owner automatically drains work, tears down Mininet, prints the
JSON report, and exits. Mininet-AI remains the only component allowed to execute
their proposed network actions. If agent construction or execution fails,
verbose output and the run log show the error immediately and the same teardown
path exits nonzero.
