# Pinned native Ollama deployment and containment evidence

Research date: 2026-10-22. Source-only answer to [Identify a pinned Ollama
deployment and containment evidence](https://github.com/santiagopedro1/mininet-ai/issues/100),
under [Plan clearer output and simplified run storage](https://github.com/santiagopedro1/mininet-ai/issues/38).
Immutable client/protocol baseline: [provider-containment-evidence.md at
5081fcf78fdad3a9f8e41ff00d595530e2c55d31][baseline], read with `git show` rather
than switching branches. The map Notes and full ticket were read through `gh`.

## Verdict and evidence classes

**Propose Ollama v0.40.8, Linux amd64, native `ollama serve` plus its packaged
llama.cpp runner, as a release candidate for separately authorized validation;
do not approve a deployment.** CPU-only and GPU configurations are distinct
candidate profiles, not an implicit human hardware choice. No runtime boundary,
cleanup bound, device cessation, absence of remote compute, or architecture
acceptance is established here.

The human-selected route remains dedicated per-run Ollama, native non-streaming
foreground and learning requests, no shared daemon/proxy/cloud, durable complete
per-attempt accounting, and whole-owned-Provider shutdown for ambiguity (ticket
and map). This note does not replace the long-lived per-run reasoning worker,
change workflow concurrency, choose production deadlines or compatibility policy,
or revise an ADR.

- **S:** fact in identified first-party source/release metadata, not a deployed fact.
- **D:** mutable first-party documentation retrieved on the research date.
- **I:** proposed requirement/inference, not implemented or human-approved policy.
- **U:** required but unobserved deployment behavior.

## Exact candidate identity (S), with reproducibility limits (U)

| Component | Pin / provenance |
| --- | --- |
| Ollama release | [v0.40.8][release], release ID `419777982`, published `2026-10-21T16:20:30Z`; latest stable returned by GitHub's first-party API on the research date |
| Ollama source | Tag ref resolved directly to commit `f580b5484a58c8370c35d669555bf887783a3f68` ([ref API][ollama-ref]); do not use mutable `main` or `target_commitish=main` as a pin |
| Linux amd64 distribution candidate | [ollama-linux-amd64.tar.zst][asset], asset ID `663838027`, size `1505219659` bytes, API-reported SHA-256 `0bb17cc19e9c1339ddbe0cba55148bf068f8f19dec8ec5fd531461aaa207282a` ([release metadata][release-api]) |
| llama.cpp source selected by Ollama | [`LLAMA_CPP_VERSION`][llama-version] says `b11738`; upstream [tag ref][llama-ref] resolved to `f174553e1621c6a22a64e7bfcbb58d3a5c49e6fb` |
| Runner build composition | Ollama's [CMake recipe][cmake] fetches that tag, normally applies its own [compatibility overlay][compat], and enables the server; it disables CURL, OpenSSL, and subprocess support. Plain upstream llama.cpp is **not** the complete Ollama runner source identity. |

The release metadata says `immutable=false`; tag names and asset URLs alone are
not immutable identities. The asset digest above is **publisher metadata**, not
a locally verified archive hash: no 1.5 GB binary archive was downloaded,
installed, executed, or matched to source. [Release checksum file][checksums]
is another first-party verification input for a future authorized download.
The checksum text was retrieved and its amd64 entry matches the API digest;
this still does not verify the archive bytes. No signature/attestation verification
or bit-for-bit reproducible-build claim is made. Future validation must hash the
downloaded archive and installed
executable/helper/library set, record build provenance and model manifests/blob
digests, and reject drift. A version response is insufficient. The baseline's
Python lock pins neither this server nor its model/backend. [baseline]

**Candidate profiles (I/U):**

1. **Linux amd64 CPU-only llama.cpp:** same release artifact as an acquisition
   candidate, explicitly constrained to CPU operation and validated library/device
   access. `options.num_gpu=0` becomes runner `-ngl 0` in [launch source][launch],
   but is not proof that discovery never opens a GPU or that other components
   cannot offload. Observe the entire lifecycle, not just inference placement.
   A CPU-only rebuild would need a separately recorded build/artifact identity;
   it is not silently interchangeable with the published archive.
2. **Linux amd64 local GPU llama.cpp:** same base candidate only with a separately
   pinned/observed CUDA, ROCm, or Vulkan backend, exact GPU(s), driver/runtime,
   loaded libraries, placement and fallback behavior. The [packaging script][packaging]
   separates ROCm and MLX supplemental archives; the base archive is not a
   universal GPU package or CPU-only package. [Build presets][presets] distinguish
   CPU, CUDA, HIP and Vulkan configurations. Supplemental bytes require their own
   recorded digests before use. No particular GPU profile is selected here.

MLX, macOS, Windows/WSL, arm64/JetPack, remote/RPC/distributed backends,
containers with unverified host ownership, custom binaries, speculative/draft
models and multimodal/projector paths are outside this bounded candidate and
need separate coverage if requested. Exclusion is not a claim of impossibility.
No model is selected: model compatibility, capacity, model license and exact
manifest/blob set remain prerequisites; release recency does not validate them.

## Startup, process and listener ownership

**S:** `serve` binds the configured address using `net.Listen` in
[`cmd/cmd.go`][serve-command]. [Server startup][serve] prepares/prunes model files,
initializes caches/scheduler and performs GPU discovery before normal service.
Therefore the containment boundary must exist **before exec/startup**, not only
before the first inference, and the model store cannot be an unmanaged writable
shared store accidentally subjected to startup mutation (I).

**S:** [GPU discovery][discovery] itself starts a short-lived `llama-server` with
a loopback port, kills/waits for it, runs another `--list-devices` command, and
invokes native discovery. [Discovery orchestration][discovery-scheduling] can
initialize different libraries/devices, including parallel validation passes.
The Provider inventory includes these startup helpers, native probes and failed
or retried launches, not just the eventual loaded-model PID (I).

**S:** [Scheduler loading][scheduler-load] creates the runner and waits for model
loading/readiness. [Launch code][launch] locates `llama-server`, temporarily
allocates then releases a port before child bind (a race, not socket activation),
passes `--host 127.0.0.1`, `--offline`, `--no-webui`, model path, context/parallel
and placement arguments, then uses `exec.Command` and `cmd.Start`. The
[binary search][binary-search] considers library, packaged and local build/CWD
layouts. [Environment setup][launch] inherits `os.Environ` and constructs library
search paths including user/system paths. Merely pinning `ollama` does not pin
the executable or shared objects it actually loads (I).

**S:** Linux [process attributes][linux-attr] are an empty `syscall.SysProcAttr`;
[AddRunnerToJob][job] is a no-op outside Windows. This is not a Linux cgroup
manager or a parent-death containment guarantee. [Kernel cgroup v2 documentation][cgroup]
says children inherit the forking parent's membership; moving a parent later
does not move existing descendants, and migration remains possible subject to
permissions. `cgroup.kill` signals the whole subtree, handles concurrent forks
and is protected against migrations; `cgroup.events populated=0` reports absence
of live processes throughout the subtree. These are documented mechanisms,
not observed capabilities of the user's kernel/delegation (D/U).

**I/U required boundary:** an exclusively owned, per-run, non-root domain cgroup
subtree seeded before startup, with a trusted external supervisor and no migration
escape or automatic restart/adoption of an old Provider incarnation. Observe all
startup/load/discovery/runner descendants and nested cgroups, including failure
paths. Verify authority to terminate that subtree without touching unrelated
processes. A PID/PGID list, reaped reasoning worker, or killed server PID cannot
substitute for this complete boundary. Observe actual API and helper listener
socket ownership, namespace/address/port, executable hash and process birth
identity; correlate outgoing loopback connections with owned runner listeners.
Reject port collision, stale listener reuse, unexpected executable/CWD fallback,
external runner, unknown helper, privileged escape or shared ownership before
trial admission. Loopback and CORS alone do not confer exclusive access.

**S:** [signal handling][serve] closes the HTTP server, cancels scheduling and
calls [unloadAllRunners][unload], including an active-loading runner.
[Runner shutdown][runner-stop] kills its process and waits on its done channel.
These calls are useful paths, not a bounded all-descendants/device-stop contract;
unload calls do not produce a durable per-attempt termination acknowledgement.
External supervision must observe completion after any escalation, not just
successful signal submission (I/U). An unkillable task, failed cleanup observation,
or populated subtree at budget exhaustion means unresolved cleanup and run abort,
not permission to start another independent trial.

## Exclude remote work before admission

**S/D:** [FAQ][faq] documents `OLLAMA_NO_CLOUD=1`, restart, and the cloud-disabled
log. [Configuration source][config] also supports `disable_ollama_cloud` in
server configuration; `OLLAMA_REMOTES` defaults to `ollama.com`, not deny-all.
The [native chat handler][remote-chat] can proxy a model with `RemoteHost` and
`RemoteModel` and checks cloud-disabled status; explicit cloud models have a
[separate proxy path][cloud-proxy]. The [route inventory][routes] also exposes
web search/fetch, compatible APIs and a Codex desktop proxy. Native protocol and
local listener address alone do not prove local compute.

**S:** inherited runner environment is another escape surface. At the pinned
upstream commit, [`common/arg.cpp`][upstream-args] supports `LLAMA_ARG_RPC`
(`--rpc`, when supported by the build), device selection, model URL/repository,
MCP/tool/agent options and other environment-derived settings. `--offline` is
not evidence that arbitrary RPC/tools/network work is disabled. This is a source
hazard inventory, not proof the published runner enables every upstream feature.

**I/U admission requirements:**

- Explicit native `/api/chat`, `stream=false`, fixed per-run origin and owned
  server incarnation for **every** foreground/learning/memory client; no registry
  fallback, alternate base URL, HTTP redirect, compatible gateway, reverse proxy,
  tunnel, shared daemon or external/pre-existing runner.
- Sanitized allowlisted environment/configuration for client, server and helpers;
  cloud explicitly disabled; no inherited proxy settings, cloud base overrides,
  remote/RPC endpoints, dynamic-loader injection, alternate runner or upstream
  tool/MCP/agent configuration. No credentials inspection is needed for this note.
- Pin/review selected local model manifests, child-runner variants, model options
  and binaries; reject cloud source names, remote host/model metadata, unexpected
  runner selection or any fallback outside the profile. Empty remote metadata is
  corroboration, not network proof. Do not invoke `ollama run` as a supervisor:
  its [CLI source][serve-command] contains model pull/cloud-suggestion behavior.
- Independently enforced and observed network policy: only the authorized local
  client→server and server→owned-runner paths; no arbitrary loopback forwarding,
  remote RPC, DNS/upstream compute or tool egress. Cloud-disabled logs alone cannot
  establish this. Test denial with separately authorized controlled fixtures,
  not live cloud requests. Unexpected outbound attempt, redirect, endpoint, proxy
  or remote metadata rejects eligibility; absence of traffic in one short sample
  cannot prove all configurations safe.
- Reject remote tools/delegates and unaccounted auxiliary models. A returned
  tool-call structure is not tool completion: later local/remote tool execution
  and subsequent model calls have independent lifetimes. Trusted worker-local
  tools must separately meet the existing extension/ownership constraints; this
  research adds no new eligible tool surface.

## Final-response semantics, ambiguity and concurrent work

**S/D:** [native chat reference][chat-doc] and [response types][response-types]
define `done` as response completion and `done_reason` as why generation stopped.
Non-streaming [writeChatResponse][write-response] aggregates internal events and
returns one JSON object. In the [native runner chat path][native-chat], callbacks
carry completion and metrics. The [llama.cpp adapter][adapter-chat] converts
finish reasons into terminal callbacks, drains and closes its internal response
body before the final callback. Ollama-rendered chat/completion uses a [different
internal path][adapter-completion], also with delayed final callback. Thus
client non-streaming does **not** mean no internal SSE/streaming or second socket.

**I:** accept only a complete, successfully parsed, correlated response with
`done=true` for the exact admitted transport attempt, validated against the
intended endpoint and request kind. `length` is terminal generation, not necessarily
successful reasoning. [Unload responses][remote-chat] can also say `done=true`
with `done_reason=unload`; preload/unload/debug-only operations must not be confused
with the outstanding chat attempt. The writer itself does not establish a new
server request-ID/retrieve/cancel protocol. HTTP 200, EOF, final content without
`done`, `/api/ps`, no tokens, model unload, keep-alive expiry and idle CPU/GPU are
not generic quiescence witnesses. Final request completion does not clear siblings,
late local learning, queued work or retries. [baseline]

**S:** [scheduler admission][scheduler-admission] has pending channels and can
reuse an already-loaded runner; [scheduler processing][scheduler-pending] checks
cancelled pending contexts. [Configuration][config] exposes parallelism, loaded
models and queue limits; [FAQ][faq] describes model-level concurrency and queueing.
[Loading source][scheduler-load] includes OOM/context adjustment and startup
retries; [runner load][runner-load] includes a projector CPU fallback/restart.
These can create multiple process incarnations even for one client attempt.
One model slot or `OLLAMA_NUM_PARALLEL=1` is not proof that no queued requests,
HTTP handlers, discovery or learning work exists.

**I/U accounting and failure matrix:**

| Window / event | Required handling and future observation |
| --- | --- |
| Before send / acceptance before acknowledgement | Persist attempt intent before send; distinguish definitely not submitted from possibly accepted. Crash after server acceptance but before receipt remains unknown. Demonstrate this window with a controlled test. |
| Completion produced but socket/worker lost | No durable terminal witness is available merely because the server may have finished. Preserve uncertainty and close admission; use observed whole-Provider shutdown when unreconciled. |
| Response received but durable evidence commit fails | Do not silently mark terminal or advance; persistence/admission/quarantine handoffs still apply. Demonstrate supervisor survival and evidence delivery independent of worker exit. |
| Disconnect, cancellation, SDK timeout or worker death | Context propagation exists in the cited routes/adapter, but there is no demonstrated bounded cessation acknowledgement. Neither local exit nor cancellation request clears the attempt. |
| Error, overload, failed/corrupt JSON, missing done | Classify the correlated result conservatively; an error is not a universal non-admission/termination proof. Validate explicit pre-admission rejection cases separately. |
| Retries and tool/learning loops | Inventory Agent, model guidance retries, SDK/HTTP transport reconnect/replay, proxy and supervisor layers. Disable automatic reasoning retries per baseline; verify effective settings rather than assuming defaults. A successful later attempt never clears an earlier uncertain attempt. |
| Queueing / parallel foreground and learning | Persist separate logical-call/attempt identities and origin incarnation, retain all pending/in-flight/terminal states, and cover scheduler slots/loads and all clients. Preserve agreed concurrency rather than silently serializing workflows to simplify proof. |
| Whole-Provider shutdown | Stop new dispatch/learning/retries first; shut down the complete owned subtree, observe process exit/reaping and listener disappearance, deny restart and verify the selected device boundary. Record uncertain attempts as contained by shutdown, not falsely successful completions. |

The baseline documents Agno guidance retry and auxiliary learning hazards, plus
Ollama Python source identity. This note does not re-audit all locked Python
dependencies or establish compatibility with this new server release.

## Required deployment evidence and rejection criteria (I/U)

No rows below have been runtime-tested here. These are validation requirements,
not commands to run now or a claim that the existing prototype implements them.

| Required observation | Reject / keep unresolved if |
| --- | --- |
| Authorized acquisition verifies archive digest and records installed server, runner, libraries, model manifests/blobs and immutable launch/config identity | Missing/mismatched digest, moving tag-only identity, unreviewed source override, unexpected model/runner or incompatible native response semantics |
| Start inside the owned cgroup before discovery; record helper/process birth, membership, lifecycle and listener ownership during startup, load/reload/OOM and shutdown | Any earlier/unowned process, migration escape, stale PID/socket reuse, shared service, helper omitted from inventory or ineffective delegation |
| Confirm effective cloud rejection, endpoint restrictions, network deny rules and absence of unowned runner/RPC/tool paths using controlled negative fixtures | Unexpected endpoint/redirect/egress, cloud accepted, hidden proxy/replay or unobservable remote work |
| Correlate complete final responses and durable per-attempt evidence for foreground plus learning, parallel/queued calls, failures and every configured retry layer | Missing done, missing/contradictory correlation, orphaned intent, silent retry, untracked learning/tool call or lost required persistence evidence |
| Inject disconnect, partial response, acceptance-before-ack loss, worker/controller death, runner crash, startup failure, server death and cleanup failure; verify closed admission and complete shutdown/quarantine | Later trial admitted while any attempt/cleanup is uncertain, late actions/writes, restart into old origin, missing durable failure evidence or cleanup budget exhaustion treated as success |
| **CPU profile:** observe no GPU/RPC/backend use across startup/discovery/load/inference/cleanup; observe complete CPU process boundary cessation | CPU setting only, helper GPU access, external backend, incomplete process inventory or no trustworthy shutdown witness |
| **GPU profile:** separately record hardware/driver/runtime/library identities, all device contexts and any external device service; validate cessation/release after normal and forced termination under authorized fault cases | Reliance on cgroup empty, PID exit, free-VRAM recovery, utilization zero or sampled telemetry alone as proof of device work cessation; unowned shared compute service, reset requirement, unsupported/unobservable cleanup |

The cgroup interface accounts for live processes, **not** a general accelerator
fence. GPU command lifetime, driver behavior after process death, device-service
ownership and bounded recovery remain unknown. No GPU-vendor stop guarantee has
been established by this bounded source review. CPU validation avoids those
specific accelerator questions only if absence of device/offload use is actually
demonstrated; it does not inherit runtime acceptance by default. If adequate
device evidence cannot be obtained, the GPU profile is ineligible, rather than
silently substituting CPU on the human's behalf.

## Checks and exclusions

Primary sources only: first-party GitHub release/ref APIs, commit-pinned Ollama
and llama.cpp files, Ollama documentation and Linux kernel documentation. Source
requests were read as text, never imported/executed. Tag refs, source regions and
release metadata were inspected. All 39 reference definitions resolve locally;
HTTP HEAD checks returned 200 for all 28 distinct cited URLs (anchors excluded).
The release checksum entry matched API metadata. Staged whitespace is checked
before commit; no Markdown linter was executed for this note.
Documentation can drift and is subordinate to the identified
source for this candidate; neither proves actual deployment behavior.

No installed-daemon discovery, unrelated-process inspection, credential/config
inspection outside this worktree, provisioning, model/inference downloads,
inference, live Provider calls, production tests/code, migration or ADR edits.
No deployment measurements, dependency compatibility test, GPU cleanup test or
architecture review was performed. Remaining blockers are human profile/model
selection and separately authorized runtime/deployment validation, not a missing
source-only safety certificate. Tracker resolution/map updates are delegated to
the parent session; this branch is pushed without PR or merge.

[baseline]: https://github.com/santiagopedro1/mininet-ai/blob/5081fcf78fdad3a9f8e41ff00d595530e2c55d31/docs/research/provider-containment-evidence.md
[release]: https://github.com/ollama/ollama/releases/tag/v0.40.8
[release-api]: https://api.github.com/repos/ollama/ollama/releases/419777982
[ollama-ref]: https://api.github.com/repos/ollama/ollama/git/ref/tags/v0.40.8
[asset]: https://github.com/ollama/ollama/releases/download/v0.40.8/ollama-linux-amd64.tar.zst
[checksums]: https://github.com/ollama/ollama/releases/download/v0.40.8/sha256sum.txt
[llama-version]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/LLAMA_CPP_VERSION
[llama-ref]: https://api.github.com/repos/ggml-org/llama.cpp/git/ref/tags/b11738
[cmake]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llama/server/CMakeLists.txt#L112-L188
[compat]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llama/compat/README.md
[packaging]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/scripts/build_linux.sh#L32-L63
[presets]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llama/server/CMakePresets.json
[launch]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llama_server.go#L364-L545
[serve-command]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/cmd/cmd.go
[serve]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/routes.go#L2448-L2545
[discovery]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/discover/llama_server.go#L42-L159
[discovery-scheduling]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/discover/runner.go#L36-L180
[scheduler-load]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/sched.go#L539-L809
[binary-search]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llama_binary.go#L21-L145
[linux-attr]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llm_linux.go
[job]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/runner_job_other.go
[cgroup]: https://docs.kernel.org/admin-guide/cgroup-v2.html
[unload]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/sched.go#L1745-L1761
[runner-stop]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llama_server.go#L2734-L2754
[faq]: https://docs.ollama.com/faq
[config]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/envconfig/config.go
[remote-chat]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/routes.go#L2989-L3140
[cloud-proxy]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/cloud_proxy.go
[routes]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/routes.go
[upstream-args]: https://github.com/ggml-org/llama.cpp/blob/f174553e1621c6a22a64e7bfcbb58d3a5c49e6fb/common/arg.cpp
[chat-doc]: https://docs.ollama.com/api/chat
[response-types]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/api/types.go#L525-L545
[write-response]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/routes.go#L2936-L2987
[native-chat]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/routes.go#L3438-L3528
[adapter-chat]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llama_server.go#L2028-L2225
[adapter-completion]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llama_server.go#L1657-L1912
[scheduler-admission]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/sched.go#L212-L254
[scheduler-pending]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/server/sched.go#L268-L310
[runner-load]: https://github.com/ollama/ollama/blob/f580b5484a58c8370c35d669555bf887783a3f68/llm/llama_server.go#L1083-L1145
