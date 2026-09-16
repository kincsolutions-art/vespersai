# Provider compatibility spike

Status (2026-09-15): bounded live spike stopped; Gemini generation failed with HTTP 504,
direct Tavily passed. Composio live actions are deferred until onboarding UI.

## Approved decision

Gemini Developer API BYOK replaces Nebius (unavailable to the owner in Nepal).
Direct Tavily uses a separate platform-managed key with future per-tenant quotas.
Composio remains for connected-app authentication/actions. Other architecture and
product decisions remain unchanged. Historical Nebius/NVIDIA hackathon eligibility
and program benefits remain unresolved; Gemini is not evidence of eligibility.

## Evidence matrix

| Capability | Exact model/tool/SDK versions | Offline/live | Verdict | Evidence | Limitations |
| --- | --- | --- | --- | --- | --- |
| Gemini discovery | `gemini-3.5-flash-lite`; google-genai 2.23.0 | Live | Pass | One discovery returned the exact selected model | Listing does not demonstrate generation compatibility |
| Gemini text/tool/structured output | Same model; Pydantic AI 2.43.0 native GoogleProvider | Live | Failed / subsequent checks blocked | Two minimal-text generation attempts returned `ModelHTTPError`, HTTP 504 | No text, local tool, or structured-output success demonstrated; no generation token usage returned |
| Native Google protocol and assertions | Same pinned SDKs; synthetic mocked responses | Offline | Pass | Text, actual local tool invocation and final token, validated structured result | Mocked protocol is not provider compatibility |
| Direct search | Tavily 0.8.3; `search`, basic, max_results=3 | Live | Pass | One public asyncio query, three usable relevant sources; provider reported 1 credit | Shape/relevance checks do not establish general search accuracy; tenant quotas not implemented |
| Pydantic AI–DBOS interrupted recovery | Pydantic AI 2.43.0 / DBOS 2.31.1; FunctionModel and adapter_add | Offline PostgreSQL | Pass | Completed model/tool counters 1/1; interrupted final model counter 2, final result 42 | No live inference or external side effects |
| Composio interfaces | Composio 0.21.1; ConnectedAccounts.initiate, Tools.execute | Offline | Pass | Imported and inspected pinned signatures without constructing network clients | No connections, authorization links, or app actions tested |
| Composio live app actions | Composio 0.21.1; tool/version not selected | Live | Deferred until onboarding UI | Explicit user scope amendment | Backend identity must enforce connection ownership; later ownership-checked live test required |

The selected Gemini model is **not yet on a demonstrated supported-model list**.
The transient 504 result does not establish permanent incompatibility. Investigate
provider availability and retry only in a separately budgeted live batch; no fallback.
Composio's approved deferral does not block tenant foundations or authentication/onboarding.
Phase 2 remains incomplete; provider generation compatibility and environment provisioning
remain outstanding. No phase 3/product implementation was started in this batch.

## Exact local configuration

If `.env.spike` does not already exist, copy `.env.spike.example` to it. Edit locally;
never overwrite an existing secret file or paste keys into commands/chat.

```dotenv
VESPERS_SPIKE_GEMINI_API_KEY=<designated Gemini Developer API test key>
VESPERS_SPIKE_GEMINI_MODEL_ID=gemini-3.5-flash-lite
VESPERS_SPIKE_TAVILY_API_KEY=<separate platform-managed Tavily test key>
VESPERS_SPIKE_COMPOSIO_API_KEY=<optional Composio test API key>
VESPERS_SPIKE_COMPOSIO_TEST_USER_ID=
VESPERS_SPIKE_COMPOSIO_TEST_CONNECTED_ACCOUNT_ID=
```

Use real values only in `.env.spike`. Composio fields can all remain blank for this
batch. Tavily does not require a Composio account. Git ignores `.env.spike`; Docker
excludes `.env*`. Normal API/worker startup and CI do not load spike credentials.
The example now contains empty key placeholders. Credential-looking values found in
that example during inspection were removed. An exact-key scan also found configured
keys in `.env.example`; those values were removed without changing private configuration.
Rotate the exposed keys. A final exact-key scan of non-ignored files passed; this is not
a comprehensive secret-history audit.

## Commands and request accounting

Offline checks (no provider requests):

```sh
make check
npm run build --prefix apps/dashboard
make integration
docker compose config --quiet
git diff --check
```

`make integration` builds a fresh isolated Compose project, disables development bind
mounts only in its test override, and removes only that project's resources.

The two live commands actually used in this batch were:

```sh
uv run python -m backend.agent.live_spike --live --free-allowance-confirmed gemini tavily > /tmp/vespers-live-report.json
uv run python -m backend.agent.live_spike --live --free-allowance-confirmed gemini --resume-report /tmp/vespers-live-report.json > /tmp/vespers-live-followup.json
```

Do not rerun these as ordinary verification: they incur new provider requests. The
user confirmed both free allowances before execution. Cumulative attempted requests:
**2 Gemini generations, 1 Gemini discovery, 1 Tavily search, 0 Composio requests**.
Gemini returned no generation token usage. Tavily reported **1 credit**; no currency
bill is known. Requested output limit was 2,048 tokens per generation, not measured usage.
No billing settings, credits, or plans were changed.

Runner limits: six generation attempts (including transport retries), two discovery
requests, one basic search with at most three results, 30-second request timeouts and
90 seconds per provider (180 seconds total for both). Gemini retries are disabled;
all allowed requests pass counted transport boundaries. Synthetic prompts only,
no search grounding/extraction/crawling/images, no model/provider fallback. Prompt
body limit is 20,000 bytes; responses over 256,000 bytes are rejected after receipt.
Normalized title/content are capped at 200/2,000 characters per result; source URLs
are preserved and capped at 2,000 characters. Errors report class/status, not raw
provider bodies. Failed checks now exit nonzero.

`--resume-report` retains attempted counts and skips already-passed providers; it
must reference the last trusted local report when continuing a bounded run. Starting
without it starts new accounting, so it is not a persistent account quota system.
A report and these guards do not replace production atomic per-tenant budgets.

## Interfaces, data handling, and limitations

GoogleProvider receives an explicit per-run key and model, uses Developer API
(`vertexai=False`), and does not use ambient/global keys or Vertex credentials.
Pinned legacy httpx injection is deprecated for Pydantic AI v3; no dependency was
upgraded in this batch. Offline tests use synthetic keys/mock transports.

Composio `ConnectedAccounts.initiate` takes user_id and auth_config_id;
`Tools.execute` takes slug/arguments and supports explicit user_id,
connected_account_id and version. Future trusted backend context must supply identity
and check ownership before execution; model arguments cannot authorize connections.

Only synthetic/non-sensitive Gemini prompts were used. Free and paid Gemini API data
terms differ; consumer Gemini subscriptions do not establish API allowance. Costs for
Gemini, direct Tavily and Composio remain separate.

Official implementation references:
[Pydantic AI Google](https://pydantic.dev/docs/ai/models/google/),
[Pydantic AI DBOS](https://pydantic.dev/docs/ai/capabilities/durable_execution/dbos/),
[Gemini model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite),
[Gemini terms](https://ai.google.dev/gemini-api/terms),
[Tavily search](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[Composio execution](https://docs.composio.dev/docs/tools-direct/executing-tools).

## Preserved infrastructure evidence

The previous batch verified 19 tests, quality checks, dashboard production build,
AUD-01–AUD-04 fixes, and isolated full-stack PostgreSQL startup/smoke. The resident
DBOS 2.31.1 workflow recovered after SIGKILL without repeating its recorded completed
step; the existing-volume ownership transition preserved disposable data on two runs.
The real development volume was untouched. These are historical results, not live
provider results or a claim that this preparation batch reran recovery.

Pydantic AI 2.43.0's DBOS adapter and Composio 0.21.1 imports were inspected previously.
The harmless recovery test does not prove external-action safety, Telegram delivery,
distributed failover, or full Pydantic AI durability. See
[local verification](local-verification.md). Remote CI results remain unobserved.

## Offline preparation verification (2026-09-15)

`uv sync --frozen`, `make check` (26 tests plus lint/format/types), dashboard production
build, `docker compose config --quiet`, and `git diff --check` pass. Seven new tests
use synthetic keys and mocked constructors; they verify explicit per-call keys/model,
Developer API rather than Vertex configuration, redaction, and no Composio account
requirement for Tavily. No live provider or PostgreSQL integration/recovery spike
was run in this preparation batch.

## Bounded spike verification (2026-09-15)

`make check` passed 37 offline tests plus formatting, lint and type checks; dashboard
production build passed. The disposable full-stack integration passed startup,
runtime role/RLS denials, ownership transition twice, original resident recovery,
adapter recovery, secret-sentinel checks, and outage/readiness/reconnection checks.
The test project was removed. These are local results; remote CI remains unobserved.

The adapter wraps model calls; the deterministic local tool explicitly uses
`@DBOS.step()`. Initial testing without that decorator replayed the local tool, so
the explicit tool step is a required implementation boundary. The successful probe
reused the first model call and local tool (one execution each), reran the interrupted
final model call (two executions), and returned 42 from the same recovered workflow.
A synthetic secret held by the FunctionModel closure was absent from captured logs,
workflow status/input/output rows and operation outputs, including decoded base64
strings. No unsafe unpickling or live credentials were used. This checks the probe's
serialization boundary, not comprehensive credential security or live Google client
serialization. Offline constructor/error tests cover synthetic provider credentials.

Recovery does not establish exactly-once external actions, Telegram delivery safety,
distributed failover, or duplicate-free inference billing: an interrupted provider
request can already have been accepted or charged before local recovery retries it.
