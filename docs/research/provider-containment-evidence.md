# Provider completion and cancellation evidence

Research date: 2026-10-15. Repository baseline: `8db21fc58017d6ae2ba10da9193bd66210223ba8`.
Resolution of [Verify Provider request completion and cancellation evidence](https://github.com/santiagopedro1/mininet-ai/issues/55), under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).

## Verdict and scope

**Source-only negative acceptance verdict:** the configured client paths do not,
by themselves, establish remote quiescence after timeout, disconnect, or worker
death. There are useful *per-request* completion signals, and OpenAI background
Responses have a documented status/cancel protocol. Neither is currently a
complete, durable accounting protocol for every foreground and learning request.
An uncertain request must prevent the next independent trial; elapsed time,
an Agno error, a cancellation attempt, or a reaped local worker cannot clear it.

This is research, not runtime validation or architecture acceptance. No inference,
credential inspection, package execution, service provisioning, or production
changes occurred. Wheel archives were downloaded, hash-checked against `uv.lock`,
and read without installation. First-party HTTP documentation/source was read.
No deployed server version, endpoint, proxy, credentials, or model was inspected.

Classification used below:

- **S — source fact:** behavior visible in the exact locked client source, or
  separately identified server-source snapshot; not a deployment observation.
- **D — documented contract:** first-party API documentation, retrieved on the
  research date; mutable and not proof of a particular compatible server.
- **I — inference / proposed gate:** conservative consequences of those facts,
  not implemented behavior or a new human decision.
- **U — untested:** deployment behavior, bounded termination, complete request
  coverage, and evidence delivery remain unverified.

## Actual execution paths (S)

Repository evidence: [dependency declaration](https://github.com/santiagopedro1/mininet-ai/blob/8db21fc58017d6ae2ba10da9193bd66210223ba8/pyproject.toml),
[frozen lock](https://github.com/santiagopedro1/mininet-ai/blob/8db21fc58017d6ae2ba10da9193bd66210223ba8/uv.lock),
[factory, resolver, invocation and timeout adapter](https://github.com/santiagopedro1/mininet-ai/blob/8db21fc58017d6ae2ba10da9193bd66210223ba8/mininet_ai/agents/agno/provider.py),
[model configuration](https://github.com/santiagopedro1/mininet-ai/blob/8db21fc58017d6ae2ba10da9193bd66210223ba8/mininet_ai/specification/models.py),
and [Ollama construction](https://github.com/santiagopedro1/mininet-ai/blob/8db21fc58017d6ae2ba10da9193bd66210223ba8/mininet_ai/agents/agno/ollama_model.py).

| Input/path | Locked implementation | Consequence |
| --- | --- | --- |
| `mock` | Repository `DeterministicAgnoModel` | Offline response; no remote inference in this model. Not evidence for real providers. |
| `ollama` | Agno `Ollama` → Ollama Python `chat` → `POST /api/chat` | Explicit declarative parameters are only `host` and boolean `think`; host accepts HTTP/HTTPS, not only loopback. Empty parameters use registry construction. |
| `openai` **and** `openai-responses` | Agno registry selects `OpenAIResponses` → OpenAI SDK `responses.create` | Do not mistake the default `openai` key for Chat Completions. `background` defaults to `None`, not enabled by the repository. |
| `openai-chat` | Agno `OpenAIChat` → SDK `chat.completions.create` | Different completion object and no Responses cancellation API for this request. |
| `ollama-responses` | Agno `OllamaResponses(OpenResponses)` → OpenAI SDK → `/v1/responses` | Stateless by default (`store=False`); not Ollama Python `/api/chat`. Host/key-dependent endpoint selection can select Ollama Cloud. |
| Other provider strings | Passed as `provider:name` into Agno's registry, with no repository provider allowlist | Registry membership is not containment certification; some require additional optional SDKs absent from the declared extras. |
| Python entrypoints / injected resolver | User-supplied Agent/model/client/tool configuration | Can change endpoint, SDK, background mode, learning model, retries, and external work. Not exhaustively characterizable from the stock paths. |

Agno's exact `agno/models/utils.py::_PROVIDERS` registry contains these keys
(inventory, **not** a list of validated integrations):

```text
aimlapi anthropic aws-bedrock aws-claude azure-ai-foundry azure-foundry-claude
azure-openai azure-openai-responses cerebras cerebras-openai cohere cometapi
cloudflare dashscope deepinfra deepseek fireworks google google-interactions
groq huggingface ibm inception internlm langdb litellm litellm-openai llama-cpp
llama-openai llmman lmstudio meta minimax mistral moonshot n1n nebius neosantara
nexus nvidia ollama ollama-responses openai openai-chat openai-responses
open-responses openrouter openrouter-responses perplexity portkey ramp requesty
sambanova siliconflow synthorai together tokenlab trustedrouter tuning-engines
vercel vertexai-claude vllm xai xai-responses xiaomi yapi
```

**Boundary:** detailed remote protocol evidence below covers the declared
Ollama/OpenAI SDK paths. Other registry providers, compatible gateways, custom
models, tools, built-in hosted tools, and auxiliary services require their own
protocol/ownership assessment before claiming containment. An OpenAI-shaped
response does not transfer OpenAI's service guarantees to a different server.

The repository invokes synchronous `Agent.run` without a reasoning timeout and
`Agent.arun` inside `asyncio.wait_for` with one. The latter cancels local async
execution; it does not invoke a repository remote-status reconciler. Streaming
`RunOutput` iterators are rejected by this adapter, although upstream models and
custom factories expose streaming capabilities. Normalized output records Agno
run/session identity and metrics, not a durable ledger of every provider request,
attempt, terminal status, and cancellation acknowledgement.

## Completion, cancellation, and ambiguity

| Situation | Evidence available (S/D) | Conservative interpretation (I); still untested (U) |
| --- | --- | --- |
| Ordinary Ollama completion | Native API defines `done=true` as finished and `done_reason` as its reason. Non-stream SDK returns parsed `ChatResponse`; streams yield JSON records. | Retain the final complete response for that transport attempt. HTTP success or a partial chunk without `done=true` is not enough. It says nothing about sibling learning/tool requests. |
| Ordinary OpenAI Responses completion | `Response.id`, `status`; SDK status values are `completed`, `failed`, `incomplete`, `cancelled`, `queued`, `in_progress`. Background guide defines departure from queued/in-progress as terminal. | A correlated terminal object is API-level evidence for that response, not necessarily successful reasoning. Failed/incomplete/cancelled remain unsuccessful where applicable; they do not imply rollback of local learning. |
| Ordinary Chat Completions | SDK returns a completion `id` and choices with `finish_reason`; Agno parses content/tools and preserves completion ID in `provider_data`. | Capture a complete correlated response/final stream event; tool calls may cause further requests. Completion ID is not a Responses ID. No general chat in-flight status/cancel protocol was found in the inspected SDK. |
| Client deadline / read timeout / async cancellation | Repository `wait_for`; Ollama defaults client timeout to `None`; OpenAI SDK defaults to 600 seconds with 5-second connect timeout. HTTPX documents connect/read/write/pool phase timeouts. | Local waiting has ended, not proven remote work. Defaults are source facts, **not** selected production budgets or a bound on entire operation/cleanup. |
| Disconnect / closed socket / killed worker | Client socket close; Ollama server source propagates request context into runner requests. OpenAI background guide says: “To cancel a synchronous response, terminate the connection.” | This is a documented synchronous cancellation mechanism, not a bounded, correlated remote-stop acknowledgement. An actual origin disconnect through intermediaries and server cessation require validation; process death alone proves neither. |
| Background queued/in-progress | OpenAI `responses.retrieve(id)`; generation survives a dropped background stream per guide. | Not complete. Preserve identity outside the worker; continue reconciliation or explicitly cancel. Do not allow another trial merely because the stream/worker ended. |
| Explicit OpenAI cancellation | SDK `POST /responses/{response_id}/cancel` returns a `Response`; only `background=true` responses can use it. Guide says repeated cancellation is idempotent and returns the final response object. | Preserve returned terminal status for the exact response ID, or retrieve until terminal. A sent cancel, timeout, exception, or missing response remains uncertain. A raced completion can be terminal without being `cancelled`. |
| Lost submission acknowledgement | Server may accept a create before client sees response ID/status. A client correlation ID can aid diagnosis. | Neither missing ID nor a connection exception proves non-admission. No universal lookup-by-client-correlation-ID or exactly-once create guarantee was established here; fail closed if unreconciled. |
| Retry / replay | OpenAI SDK defaults to two retries; eligible transport errors, timeouts and certain status errors can replay. Agno model and Agent retry counts default to zero but are configurable, with additional guidance retry handling in model code. | One Agent invocation may produce several transport attempts and several logical model requests. Disable automatic reasoning retries as ADR 0004 requires and verify SDK/proxy layers too; never assume a later successful attempt cleared an earlier uncertain one. |

Primary sources for the table:

- [Ollama native chat reference](https://docs.ollama.com/api/chat) (`done`,
  `done_reason`, stream and keep-alive fields); [generate reference](https://docs.ollama.com/api/generate).
- [OpenAI background guide](https://developers.openai.com/api/docs/guides/background),
  especially “Polling background responses”, “Cancelling a background response”,
  streaming/disconnect examples, and “Limits”. Its temporary polling retention
  description is not a maximum compute lifetime: expiry or a later 404 is not a
  termination acknowledgement. Retention and storage policies can change.
- [OpenAI cancel reference](https://developers.openai.com/api/reference/resources/responses/methods/cancel),
  corroborated by locked SDK `resources/responses/responses.py::cancel/retrieve`
  and `types/responses/response_status.py`.
- Locked SDK `resources/chat/completions/completions.py::retrieve/delete` documents
  retrieval/deletion of **stored** completions only (`store=true`); deletion is
  not an in-flight cancellation operation.
- [HTTPX timeout documentation](https://www.python-httpx.org/advanced/timeouts/)
  describes phase/inactivity timeouts, not remote termination. OpenAI in this lock
  uses **httpx2**, not the repository's separate HTTPX package; see the pinned
  SDK `_constants.py` and `_base_client.py` for its actual client defaults/retries.

### Important locked-source gaps (S)

1. **Agno background polling is not a containment journal.**
   `OpenAIResponses._poll_background_response` and its async counterpart return
   on terminal status. On their own max-wait expiry they call `responses.cancel`,
   ignore its returned object, log any cancellation exception, and raise
   `ModelProviderError` either way. The polling deadline is checked *after* each
   retrieval, whose HTTP wait/retries are separate. An outer `wait_for` cancellation
   is not the same control path; there is no encompassing remote-cancel `finally`
   in these methods. Thus neither an Agno timeout nor its max-wait error certifies
   remote termination. No production timeout values are proposed here.
2. **Identity exists below the normalized boundary, not necessarily durably.**
   Responses parser stores `response_id` in message `provider_data`; Chat parser
   stores completion `id`. OpenAI `_request_id` is the response header
   `x-request-id` exposed by SDK models; API status errors also expose request ID.
   It identifies an HTTP request, not an Agno run or cancellable response resource.
   Failed sends may never deliver it. Native Ollama's inspected response types
   provide model/time/done/metrics, not a server request-ID + query/cancel resource.
3. **Retry defaults matter independently of Agent retry count.**
   Agno passes `max_retries` to OpenAI only when configured. SDK defaults therefore
   apply to ordinary registry construction. `_base_client.py` generates internal
   idempotency keys, but `_idempotency_header` defaults to `None`; this source is
   not evidence that create requests are deduplicated remotely. Retry headers and
   one eventual completion cannot establish all-attempt quiescence. Agno's
   `Model.retry_with_guidance` defaults to `True`, with limit one, independently
   of `Model.retries=0`; `_invoke_with_retry` handles
   `RetryableModelProviderError` by adding guidance and reinvoking. Validate this
   path too rather than treating zero ordinary retries as sufficient.
4. **Learning has the same remote gap.** Agno `_init.set_memory_manager` and
   `set_learning_machine` normally inject the Agent model, but custom managers
   can supply different models. `MemoryManager.get_model` even has an OpenAIChat
   fallback when no model was supplied. `_managers` starts learning/memory futures;
   `_run` error cleanup calls `future.cancel()` and swallows immediate result
   exceptions. A running future is not thereby stopped, nor is its provider
   request remotely cancelled. Include extraction, updates, optimization,
   retries, and any tool-triggered remote work in accounting, not just final
   foreground output. This research did not execute any of them.

## Server ownership and compatible endpoints

**S:** no Ollama server release is locked by `uv.lock`; Python SDK version is not
server version. Repository host validation does not establish server ownership.
Ollama clients accept host/environment-derived configuration. OpenAI models allow
base URL/client overrides. No actual environment values were read.

**S (illustrative server snapshot, not the deployment):** Ollama source at
[`d919272e53593d1d55d40e1180eaa06dfbede3ac`, chat routes](https://github.com/ollama/ollama/blob/d919272e53593d1d55d40e1180eaa06dfbede3ac/server/routes.go#L3287-L3417)
creates a context from `c.Request.Context()`, passes it to `r.Completion`, and
defers cancellation. The [runner transport](https://github.com/ollama/ollama/blob/d919272e53593d1d55d40e1180eaa06dfbede3ac/llm/llama_server.go#L1757-L1775)
uses `http.NewRequestWithContext` for the runner completion request. This supports
the existence of a cancellation propagation path, **not** a bounded guarantee
that all queued/in-flight backend work has ceased before the client returns.
The same routes include cloud proxy paths, further separating local socket and
upstream compute lifetimes. This snapshot is deliberately distinguished from
the locked Python source and from unknown deployed server/backend versions.

**D:** [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
documents a non-stateful `/v1/responses` implementation (no `previous_response_id`
or conversation). Neither the inspected routes nor this compatibility description
establish the OpenAI hosted background retrieve/cancel lifecycle for Ollama.
Agno's `OllamaResponses` docstring says `/v1/responses` requires Ollama >=0.13.3;
this is an adapter claim, not detected deployment provenance.

**I:** for an externally managed local or remote server, Mininet cannot assert
termination by killing its own client. For an exclusively owned server, verified
shutdown of the complete server/runner/descendant boundary and disabled upstream
offload could be an alternative containment witness—but ownership, coverage,
shutdown completion and admission closure must be demonstrated. Killing a shared
daemon is not authorized here and cannot be assumed safe. `keep_alive=0`, model
unload, `/api/ps`, cache residency, process-idle observations, and “no more tokens”
are not per-request completion acknowledgements. Warm model caches remain allowed
under ADR 0004; outstanding requests do not.

## Fail-closed acceptance gates for follow-up (I/U)

These are candidate evidence requirements, not acceptance of a design or new
decisions. [Validate the integrated worker persistence and containment protocol](https://github.com/santiagopedro1/mininet-ai/issues/56)
must validate them with the agreed integration boundary before architecture review.

1. **Admission and complete inventory:** close trial admission before cleanup.
   Identify each provider/endpoint/backend and ownership class without recording
   secrets. Account for foreground, learning, memory, tools, delegates, retries,
   and proxy replays; unsupported/unobservable paths must not bypass the gate.
2. **Durable correlation:** distinguish run, trial, Agent, session, invocation,
   logical model call, transport attempt, provider response ID, HTTP request ID,
   and server incarnation. Record admission/submission intent before send and
   deliver provider IDs/status independently of worker survival. Demonstrate the
   crash window after remote acceptance but before ID delivery; an intent record
   alone does not reconcile it. Payloads/credentials are not needed for this ledger.
3. **Per-request terminal witness:** accept a correlated final response or a
   documented terminal status/cancel result, with validated semantics for that
   service; alternatively demonstrate complete owned-server containment. Pending,
   missing, contradictory, expired, and transport-error evidence remains unknown.
   A foreground success with uncertain background work is insufficient.
4. **Independent local gate:** verify worker/descendant exit or drained inactivity,
   persistence-error delivery, no late writes/actions, lock ownership and orphan
   handling separately. Remote terminal status does not prove local quiescence;
   local process exit does not prove remote quiescence. Prevent further retries
   or learning after the supposed terminal boundary.
5. **Deterministic protocol tests before authorized runtime tests:** fake-transport
   checks should cover normal final responses, partial stream/EOF, queued status,
   cancel/completion races, failed cancel, timeout while polling, cancellation
   during create/retrieve, duplicate attempts, and worker death before ID delivery.
   Such tests verify the candidate state machine only, not a live provider.
6. **Separately authorized deployment validation:** pin actual server/runner/proxy
   provenance, observe origin admission and task termination, force disconnects,
   delayed cancellation and supervisor death, and test learning in parallel with
   foreground failure. Establish coverage and bounded cleanup on the intended
   deployment, including shared-server ownership. No such tests were authorized
   or performed in this research.
7. **Budget exhaustion stays failed:** if any request remains unresolved within
   the existing cleanup budget, preserve incomplete-cleanup evidence and abort
   the run; **do not start another independent trial**. Late terminal evidence
   cannot revise the frozen trial outcome. Do not replace this with a sleep,
   SDK timeout, successful retry, cache unload, or “probably stopped” inference.

The reviewed [single-database prototype resolution](https://github.com/santiagopedro1/mininet-ai/issues/47#issuecomment-6200438441)
prefers one long-lived per-run reasoning worker with stable session Agents and
requires run failure on required volatile-context loss. This research neither
replaces that direction nor recommends fresh workers per invocation. It does not
resolve [Define long-lived Agno worker integration boundaries](https://github.com/santiagopedro1/mininet-ai/issues/54)
or accept [Choose a database-free or single-database storage architecture](https://github.com/santiagopedro1/mininet-ai/issues/41).
ADRs 0001–0006, especially [ADR 0004](../adr/0004-repeated-failure-trial-lifecycle.md),
remain unchanged; source research clears no runtime or architecture gate.

## Reproducible source provenance and checks

The following wheel URLs are the primary version-pinned source artifacts, not
floating repository tags. SHA-256 was verified against the baseline `uv.lock`:

| Package | Exact wheel source | SHA-256 |
| --- | --- | --- |
| Agno 3.0.11 | [wheel](https://files.pythonhosted.org/packages/85/dc/df6eb84fe95ffb5bb8a34b997ad63886151f9c565acb60bf57225f71365e/agno-3.0.11-py3-none-any.whl) | `2517e8524739956648f4b62e6cf568623986144fa1426e430ace14efa1b78257` |
| Ollama Python 0.6.2 | [wheel](https://files.pythonhosted.org/packages/c4/ab/d6722beeb2d10f7a3b9ff49375708904fde18f82b5609a0bc4aeb5996a4d/ollama-0.6.2-py3-none-any.whl) | `3ad7daab28e5a973445c36a73882a3ef698c2ebb00e21e308652741577509f7d` |
| OpenAI Python 3.19.2 | [wheel](https://files.pythonhosted.org/packages/bd/20/4fe123e60525375878c67d1d8d051c9c5dec81cc56a579ba9304ca743303/openai-3.19.2-py3-none-any.whl) | `66247fcd07266e72536e90656dc27f3b0bb1e9d8696d4013fc55402c0b96a5c2` |
| HTTPX 0.28.1 | [wheel](https://files.pythonhosted.org/packages/2a/39/e50c7c3a983047577ee07d2a9e53faf5a69493943ec3f6a384bdc792deb2/httpx-0.28.1-py3-none-any.whl) | `d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad` |
| httpx2 2.13.1 | [wheel](https://files.pythonhosted.org/packages/d8/9c/6fe8931fd9f381042a9e4c7d5a7b4cbf7016b252bec0c99a49fce42c3326/httpx2-2.13.1-py3-none-any.whl) | `6dff50fabc270ee5fd25d845d0b078ed20564579744d6d962850975996d2f9a4` |

Inspect the named files by unzipping these archives as data (no package imports
or provider initialization required). Key Agno files: `models/utils.py`,
`models/ollama/{chat,responses}.py`, `models/openai/{chat,responses,open_responses}.py`,
`models/base.py`, `agent/{agent,_init,_managers,_run}.py`, `memory/manager.py`.
Ollama files: `_client.py`, `_types.py`. OpenAI files: `_constants.py`,
`_base_client.py`, `_models.py`, `_exceptions.py`, `resources/responses/responses.py`,
`resources/chat/completions/completions.py`, `types/responses/response_status.py`.
Paths are relative to each archive's package directory.

Checks: five wheel hashes matched; registry mappings and cited method bodies
were inspected; documentation links were retrieved; research-only scope and
whitespace/link checks were performed. No production tests, live provider calls,
remote-stop measurements, or deployment acceptance checks were run. Mutable
documentation is attributed as retrieved, not represented as a locked guarantee.
