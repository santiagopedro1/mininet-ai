# Ollama System One diagnosis

**Primary Goal:** feature demonstration.

One bounded-decision agent diagnoses one switch from its current local port
state. The model is a local Ollama decision model reached through
`POST /v1/systemone`; no chat completion or generated text is used. A fake
substrate keeps the example focused on the native decision path.

System One describes the agent's direct, bounded decision-making approach.
Single-switch placement and port-only observations are this example's task, not
requirements of System One. See
[decision-making approaches](../../docs/decision-making.md) and
[bounded-decision execution](../../docs/bounded-decision.md).

## Model configuration

The checked-in blueprint targets the test model `clef-flash:9b` at
`http://10.10.10.152:11434`:

```yaml
model:
  provider: ollama-systemone
  name: clef-flash:9b
  parameters:
    host: http://10.10.10.152:11434
    startupTimeout: 300s
    expectedIdentity:
      digest: 73e026ff389b28464bb26f8978f3164851a76832f0d339032747e2c56f98ea29
```

`host` is required. The startup check reads `/api/version` and `/api/tags`,
requires the model tag to exist, requires a `decision` capability when the server
reports capabilities, and compares the optional pinned digest. `startupTimeout`
bounds only the startup probe, which may load the model. Each later decision is
bounded by the scenario's `decisionDeadline`.

Test the endpoint without deploying a network:

```bash
python scripts/smoke-ollama-systemone.py \
  --host http://10.10.10.152:11434 \
  --model clef-flash:9b \
  --digest 73e026ff389b28464bb26f8978f3164851a76832f0d339032747e2c56f98ea29
```

Replace the host, model tag, and digest for another local Ollama server. Omit
`expectedIdentity` to check the model tag without pinning the artifact; the
evidence then records provenance as unverified. Use `verifyIdentity: false` to
skip identity checks explicitly.

## Validate and plan without a model

```bash
uv run mininet-ai validate examples/ollama-system-one/experiment.yaml
uv run mininet-ai plan examples/ollama-system-one/experiment.yaml
uv run pytest tests/agents/test_ollama_systemone.py
```

Validation and planning do not contact Ollama. The fake substrate also makes
live execution possible without Mininet privileges, but `run` still contacts the
configured Ollama host for startup and every scheduled decision. Stop the run
with `Ctrl+C`.

The example is a working integration path, not a model-quality or latency
benchmark. It does not establish that a particular decision model diagnoses
correctly, and its probability and confidence values remain diagnostics rather
than correctness guarantees.
