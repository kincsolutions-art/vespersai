# Telegram AI Agent — Implementation Checklist

Status: scope locked; local scaffold startup, privilege boundaries, worker readiness, and harmless PostgreSQL interrupted recovery verified; direct Tavily live search verified; Gemini generation compatibility and product features remain pending.
Prepared: 2026-09-15.

Provider change approved 2026-09-15: Gemini Developer API BYOK replaces Nebius because Nebius is unavailable to the project owner in Nepal. Direct Tavily search with a separate platform-managed key and per-tenant quotas is retained. This changes planned providers, not historical infrastructure results.

Work through phases in order. Check items only after implementation and verification. Phase gates are release requirements, not claims that work is complete.

## 1. Locked scope and operating rules

- [x] Record the product scope in the repository: a general-purpose Telegram agent with connected-app actions, search, persistent schedules, memory, and proactive use-case discovery.
- [ ] Use one shared BotFather bot; support private chats only.
- [ ] Build a web dashboard for onboarding and account management, not web chat.
- [ ] Initially allow one verified real account and one Telegram connection through a backend signup allowlist.
- [ ] Build multi-tenancy from day one and test with at least two synthetic tenants.
- [ ] Use shared always-on trusted workers with separate per-run user context; do not provision a process or VM per user.
- [ ] Execute direct requests and configured schedules without approval screens. Clarify genuinely missing information; do not guess recipients or accounts.
- [ ] Treat unsolicited suggestions as proposals, not standing instructions. A user accepting a suggestion starts execution without another approval step.
- [ ] Exclude browser execution, shell execution, and web chat from the MVP. Future shell support requires isolated sandboxes, never generated code inside shared workers.
- [ ] Use Gemini Developer API BYOK through native Pydantic AI Google support, with an explicit tested model ID and no fallback.
- [ ] Use Composio for connected-app authentication/actions and direct Tavily API for web search.
- [ ] If pursuing the external event, verify eligibility and program benefits separately; historical Nebius partner references do not establish Gemini eligibility, bundled billing, credits, or shared keys.

## 2. Scaffold the codebase

Stack: Next.js dashboard, FastAPI backend, Pydantic AI agent loop, DBOS durable execution, PostgreSQL.

```text
apps/dashboard/
backend/api/
backend/identity/
backend/credentials/
backend/agent/
backend/tools/
backend/workflows/
backend/storage/
migrations/
tests/
infra/
```

- [x] Initialize the repository, dependency lockfiles, pinned versions, and environment configuration validation.
- [x] Configure formatting, linting, type checking, CI, and migration commands.
- [x] Provide a secret-free environment example and local setup instructions.
- [x] Add Docker Compose for dashboard, API, worker, and PostgreSQL.
- [ ] Separate development, test, and production credentials/databases.
- [x] Add health/readiness endpoints and structured request/task logging.
- [ ] Establish a small provider compatibility spike before building deeply against SDK interfaces: selected Gemini model, Pydantic AI tool calling, direct Tavily, and DBOS recovery. Composio live actions are deferred until onboarding UI and do not block tenant foundations.

Scaffold verification (2026-09-15):

- Scope recorded in `docs/scope.md`; pinned dependencies and lockfiles retained without updates.
- Local lint, formatting, type checks, 19 backend tests, and dashboard production build pass.
- Isolated full-stack Compose build/startup and PostgreSQL baseline migration/smoke pass. The baseline creates no tenant product tables.
- AUD-01/AUD-03: configuration errors omit input values and original exception chains; supported driver validation and captured API/worker/migration startup failures are tested with synthetic credentials.
- AUD-02: separate migration owner and runtime roles verified against PostgreSQL. Actual runtime identity sees no rows under deny-all RLS and cannot alter tables/policies, create tables, assume owner, or connect to DBOS storage. Migration credentials are scoped to the migration service.
- Existing-development ownership transition passes twice on a disposable legacy database with data preserved. Existing user volumes were not changed; see `docs/local-verification.md`.
- AUD-04: resident readiness starts after DBOS initialization; negative checks cover worker termination and database outage, with readiness returning after reconnection.
- Genuine interrupted recovery passes: client-only enqueue, stable workflow identity, resident hostname evidence, controlled unfinished boundary, SIGKILL, same-executor restart, SUCCESS, and one execution of the already completed step. This proves only the harmless test workflow, not external-action safety or full Pydantic AI durability.
- CI now runs the isolated integration harness; remote CI results remain unobserved.
- Development and disposable test storage are separated. Production credentials/databases remain unprovisioned, so the environment-separation item stays pending.
- Historical pre-spike status: live Gemini/Pydantic AI tool calls, Composio actions, and Tavily compatibility were unverified; designated test credentials/accounts and budgets are required. See `docs/provider-compatibility.md`.

Historical provider preparation (2026-09-15, before the bounded spike): native Gemini Developer API construction seam and secret-free `.env.spike.example` added; direct Tavily 0.8.3 and Composio 0.21.1 retained. The Gemini model ID remains an explicit pending choice. Offline checks pass: 26 tests, lint/format/types, dashboard build, and Compose configuration validation. No live runner or provider calls were introduced. Prior infrastructure/recovery evidence above is preserved; this provider preparation does not rerun or extend those recovery claims.

Bounded compatibility spike (2026-09-15):

| Capability | Model/tool/SDK | Offline/live | Verdict | Evidence and limitation |
| --- | --- | --- | --- | --- |
| Gemini discovery | gemini-3.5-flash-lite / google-genai 2.23.0 | Live | Pass | One request; listing alone is not compatibility |
| Gemini text/tool/structured | Same model / Pydantic AI 2.43.0 | Live | Failed / blocked | Two generations returned ModelHTTPError HTTP 504; no generation token usage reported |
| Direct Tavily search | Tavily 0.8.3 / basic search | Live | Pass | One search, three relevant usable sources, one reported credit; quotas remain pending |
| Native Google tool/output assertions | Pydantic AI 2.43.0 / synthetic HTTP | Offline | Pass | Actual local tool and validated structured result with mocked responses |
| Adapter recovery | Pydantic AI 2.43.0 / DBOS 2.31.1 | Offline PostgreSQL | Pass | Completed model/tool executed once each; pending model executed twice; no external-effect guarantee |
| Composio imports/signatures | Composio 0.21.1 | Offline | Pass | initiate/execute inspected without network requests |
| Composio app actions | Composio 0.21.1 / tool TBD | Live | Deferred until onboarding UI | Explicit deferral allows tenant foundations and authentication; later ownership-checked live action still required |

Local checks passed: 37 offline tests, formatting/lint/types, dashboard build, and isolated full-stack PostgreSQL integration including adapter recovery and synthetic-secret checks.

The complete report, commands, limits and prior evidence are in `docs/provider-compatibility.md`.
No product features or phase 3 work were started. Remote CI is unverified; production
provisioning remains separate outstanding work.

Gate: local stack startup and test workflow execution verified. Phase 2 remains incomplete while live provider compatibility and full environment provisioning are pending.

## 3. Define data ownership and persistence

- [ ] Create users, tenants, memberships, and signup allowlist tables. Start with personal tenants but keep users and tenants distinct.
- [ ] Create Telegram links and single-use linking token tables.
- [ ] Create credential references, model settings, and connected-account records.
- [ ] Create conversations, messages, tasks, task events, action records, and outbox records.
- [ ] Create schedules, schedule occurrences, preferences, memory facts, activity signals, suggestions, feedback, and usage/budget records.
- [ ] Put tenant ownership on every tenant-owned record and scope uniqueness/foreign keys appropriately.
- [ ] Implement tenant-scoped repositories and row-level security on application tables.
- [ ] Ensure application database roles do not bypass tenant policies; set tenant context transactionally for pooled connections.
- [ ] Keep DBOS system storage and permissions separate from application tables. Do not impose application RLS assumptions on DBOS internals.
- [ ] Namespace caches, artifacts, and tool discovery results by tenant and credential identity where applicable.
- [ ] Define retention, account deletion, credential revocation, and backup retention behavior.
- [ ] Define deletion behavior for derived summaries and memory as well as original records.

Gate: synthetic tenant A cannot access tenant B through repository calls, APIs, guessed IDs, or background jobs.

## 4. Authentication and onboarding

Selected authentication component: WorkOS AuthKit. This is a design decision; integration is not implemented. Complete the phase 3 tenant foundations before wiring authentication to application access. WorkOS sign-in does not replace backend allowlist enforcement or local tenant authorization.

- [ ] Configure WorkOS AuthKit for the Next.js dashboard, with separate environment configuration and callback/logout URLs; pin and verify SDK versions during implementation.
- [ ] Implement verified-email authentication using WorkOS AuthKit.
- [ ] Validate authentication server-side in FastAPI and resolve the authenticated WorkOS user to local users, tenants, and memberships; never trust client-supplied tenant identity.
- [ ] Enforce the allowlist server-side, including direct signup API requests and WorkOS callback/session entry points; successful WorkOS sign-in alone must not grant application access.
- [ ] Normalize email consistently without assuming provider-specific aliases.
- [ ] Create user, tenant, and membership atomically; persist a unique WorkOS user mapping and handle duplicate callbacks safely.
- [ ] Protect sessions and APIs with secure cookies, appropriate CSRF protection, rate limits, and authenticated membership checks.
- [ ] Persist onboarding progress across expired sessions and browser disconnects.
- [ ] Collect timezone (Asia/Kathmandu initially), quiet hours, and user goals.
- [ ] Define authenticated disconnect/relink and account deletion flows.
- [ ] Make account disablement stop new work and prevent queued tasks from starting.

## 5. Credentials, BYOK, and models

- [ ] Encrypt secrets with versioned encryption; keep the encryption key outside PostgreSQL and source control.
- [ ] Build a tenant-authorized credential resolver. Persist references, not raw credentials, in workflow inputs.
- [ ] Redact secrets from prompts, logs, traces, task events, exception messages, and workflow histories.
- [ ] Validate Gemini Developer API keys and inspect available models; require an explicit model ID and separately tested tool/structured-output support.
- [ ] Cache model lists per credential identity; invalidate on key changes.
- [ ] Maintain a tested supported-model list for tool calling and structured output; API listing alone is insufficient.
- [ ] Verify external event requirements separately before submission; Gemini does not imply compliance with NVIDIA/Nebius requirements.
- [ ] Build clients per run/credential scope; never mutate global API keys.
- [ ] Support key replacement, deletion, and unavailable model recovery.
- [ ] Define credential resolution boundaries for queued, running, and recovered tasks.
- [ ] Handle invalid/revoked keys, exhausted credit, model removal, rate limits, timeouts, context limits, and malformed model output.
- [ ] Never silently switch to a platform-funded key or another provider.
- [ ] Explain that schedules and discovery also consume BYOK inference.

## 6. Telegram identity and transport

- [ ] Create the bot through BotFather and securely configure its token.
- [ ] Register an HTTPS webhook and validate Telegram's webhook secret.
- [ ] Generate a sufficiently random, short-lived linking token from the authenticated dashboard; provide a code/deep link.
- [ ] Store its hash, tenant ownership, expiry, and redemption state; rate-limit attempts.
- [ ] Atomically redeem the token and bind numeric Telegram user ID and private chat ID.
- [ ] Reject reused/expired tokens and concurrent second redemptions.
- [ ] Reject automatic takeover when a Telegram identity is already linked; require authenticated relinking.
- [ ] Show the linked identity in the dashboard and allow revocation. Treat forwarded linking tokens as bearer credentials.
- [ ] Reject unlinked users and group chats; do not rely on usernames for identity.
- [ ] Persist and deduplicate update IDs before creating tasks or acknowledging durable acceptance.
- [ ] Implement /start, /help, /status, and /stop.
- [ ] Define ordering for concurrent messages and distinguish new requests from modifications/cancellations.
- [ ] Handle unsupported attachments/voice input explicitly.
- [ ] Handle message length, formatting, rate limits, blocked bots, and delivery failures.

Gate: a linked user can submit one persisted task; repeated delivery of the update creates one logical task.

## 7. Tenant-scoped tools and connections

- [ ] Create a trusted execution context containing tenant ID, task ID, scoped clients, budget, and cancellation state.
- [ ] Map Composio identities/sessions from backend identity, never model arguments.
- [ ] Implement OAuth connection setup with state/expiry validation and idempotent callback handling.
- [ ] Store account labels, ownership, status, and permissions; support multiple accounts with explicit defaults.
- [ ] Discover relevant tools dynamically and cache appropriately instead of loading all app schemas into every prompt.
- [ ] Recheck account ownership, current authorization, and task limits before every action.
- [ ] Handle revoked connections, missing permissions, changed schemas, removed tools, and deleted external records.
- [ ] Record action intent, attempt status, provider identifiers, and item-level outcomes for bulk operations.
- [ ] Use conditional updates or serialize conflicting edits where available.
- [ ] Add Tavily through a separate platform-managed key with per-tenant quotas and usage accounting; keep its billing separate from Gemini BYOK.
- [ ] Bound search counts, result size, timeouts, and sensitive query content.
- [ ] Add tools for schedule management, memory/preferences, and task status.
- [ ] Treat retrieved email/documents/search content as untrusted data; enforce permissions outside the model.
- [ ] Do not expose arbitrary shell, browser, or unrestricted code execution.

## 8. Agent loop and context

- [ ] Implement the Pydantic AI agent with per-run model, history, and dependencies.
- [ ] Write instructions for autonomous action, accurate completion claims, essential clarification, and partial-failure reporting.
- [ ] Load bounded history and relevant memory, preserving explicit constraints outside lossy summaries.
- [ ] Validate tool arguments, identifiers, and structured results.
- [ ] Add request, tool-call, token, duration, retry, and output-size limits.
- [ ] Detect repetitive unsuccessful actions and stop loops.
- [ ] Enforce external-action authorization in tools even if prompts are manipulated.
- [ ] Derive success from actual tool results; distinguish failed, cancelled, partial, and uncertain outcomes.
- [ ] Emit concise progress events and final Telegram responses.
- [ ] Handle user corrections during a task; never imply that cancellation undoes completed actions.
- [ ] Keep evidence/source references attached to search-derived claims.

Gate: a real Telegram request completes a search or connected-app action with the correct tenant's credentials.

## 9. Durable tasks and safe retries

- [ ] Submit tasks through DBOS using stable workflow/task identities.
- [ ] Close the persistence-to-enqueue gap with an outbox or equivalent reconciliation.
- [ ] Integrate durable execution at model/tool boundaries, not only around the entire agent run.
- [ ] Persist queued, running, succeeded, failed, cancelled, and uncertain states; retain item-level partial outcomes.
- [ ] Resolve current tenant/account status and credentials when work executes.
- [ ] Separate interactive and background queues; reserve interactive execution capacity.
- [ ] Add per-tenant admission/concurrency limits and conversation ordering.
- [ ] Implement cancellation checks before steps and external actions, including after recovery.
- [ ] Use provider idempotency keys when supported and reconcile ambiguous outcomes before retries.
- [ ] Make final delivery durable; account for Telegram accepting a message before our completion record is saved.
- [ ] Define workflow versioning and deployment/draining behavior for unfinished tasks.
- [ ] Configure restart recovery and a documented procedure for stuck tasks.
- [ ] Test database outages without falsely acknowledging unpersisted tasks.

Gate: injected crashes before and after an external action do not cause blind duplicate side effects. Exactly-once external effects are not assumed.

## 10. Persistent scheduling

- [ ] Implement one-off and recurring schedule creation, list, edit, pause, resume, and delete through Telegram and dashboard.
- [ ] Store original instruction, normalized timing, timezone, version, optional end date, and next run.
- [ ] Show interpreted timing after creation; clarify genuinely ambiguous timing.
- [ ] Use persistent DBOS scheduling with unique occurrence identities.
- [ ] Allow one active run per schedule and skip overlaps.
- [ ] After downtime execute the latest missed occurrence once, not the full backlog.
- [ ] Recheck schedule version/status before queued work starts.
- [ ] Keep pause-future-occurrences separate from stopping a current task.
- [ ] Apply current keys, permissions, tenant status, and budgets to scheduled work.
- [ ] Define daylight-saving gaps/repeated hours and short-month behavior explicitly.
- [ ] Record occurrence results and suppress repeated identical failure notifications.
- [ ] Spread flexible schedules/discovery work without silently changing exact user-requested times.

## 11. Memory and proactive discovery

- [ ] Separate explicit preferences from inferred facts and store provenance/timestamps.
- [ ] Support inspection, correction, deletion, and conflict-aware updates.
- [ ] Prefer current explicit instructions over stale memory.
- [ ] Redact user-pasted credentials and never store retrieved instructions as user preferences.
- [ ] Record repeated requests, successful tasks, connection capabilities, and feedback.
- [ ] Generate onboarding suggestions from user goals and connected apps.
- [ ] Detect recurring requests suitable for automation.
- [ ] Run one bounded daily discovery pass; skip unnecessary model calls when signals are unchanged.
- [ ] Generate structured suggestions containing evidence, benefit, action, required connections, and proposed schedule.
- [ ] Validate evidence and deduplicate against existing schedules and past/pending suggestions.
- [ ] Send at most one strong proactive suggestion daily; send nothing when no useful opportunity exists.
- [ ] Respect quiet hours and minimize sensitive notification content.
- [ ] Support acceptance, dismissal, less-like-this feedback, and disabling suggestions.
- [ ] Resolve ambiguous 'do that' references and revalidate stale suggestions before acting.
- [ ] Prevent automation-generated activity from creating self-reinforcing suggestion loops.
- [ ] Track adoption and continued usefulness; cap discovery cost independently of live chat.

## 12. Dashboard, budgets, and responsiveness

- [ ] Finish onboarding, Telegram status, key/model management, and app connections.
- [ ] Add task history/details, progress, cancellation, schedules/next run, suggestions, and memory/preferences.
- [ ] Provide actionable reconnect, exhausted-credit, partial-success, and uncertain-result messages.
- [ ] Use idempotent submission IDs for create/retry controls; recover submitted work after browser disconnection.
- [ ] Show authoritative task state and last-updated time.
- [ ] Reserve quota atomically across concurrent runs; settle measured usage afterward.
- [ ] Label estimated usage; do not promise an exact provider spending cap from token estimates.
- [ ] Measure backend acknowledgement, first useful response, end-to-end completion, and queue delay at p50/p95.
- [ ] Use sub-second durable acknowledgement and roughly 3–5 seconds to useful output on simple tasks as initial targets to benchmark, not guarantees.
- [ ] Ensure background schedules/discovery cannot monopolize all worker capacity.

## 13. Release-critical test matrix

- [ ] Tenant isolation: guessed IDs, forged tool connections, background tasks, caches, RLS connection reuse, and account deletion.
- [ ] Authentication: duplicate signup callbacks, expired sessions, direct allowlist bypass, OAuth replay.
- [ ] Telegram: token guessing/expiry/reuse/races, duplicate updates, concurrent messages, blocked bot, unsupported input.
- [ ] Models: revoked/replaced keys, exhausted credit, unavailable models, malformed tool calls, context overflow, timeouts.
- [ ] Actions: ambiguous recipients/accounts, stale external records, partial bulk success, duplicate retries, prompt injection.
- [ ] Durability: crash before action, after provider acceptance, before completion persistence, during delivery, and after cancellation.
- [ ] Schedules: edits/pauses racing queued work, overlap, downtime, timezone/DST, short months, repeated failures.
- [ ] Memory/discovery: incorrect facts, corrections/deletion, unsupported evidence, stale suggestions, deduplication, quiet hours, budget caps.
- [ ] Load: live Telegram traffic alongside synchronized schedules and discovery; tenant fairness and atomic quota checks.
- [ ] Recovery: database outage, backup restore, lost completion records, encryption-key recovery, and incompatible deployment prevention.

Gate: fix failures affecting isolation, duplicate side effects, recovery, scheduling, and interactive starvation before launch.

## 14. Deploy the single-VM PoC

- [ ] Select an always-on VPS; start with approximately 8 GB RAM and adjust using measured load. No local model GPU is required for API inference.
- [ ] Configure domain, HTTPS reverse proxy, firewall, restricted administration, and production secrets.
- [ ] Deploy dashboard, API, worker, and PostgreSQL through Docker Compose with persistent volumes, restart policies, resource limits, and health checks.
- [ ] Keep PostgreSQL private; use least-privilege runtime roles.
- [ ] Register the production Telegram webhook and enable the one-account allowlist.
- [ ] Configure encrypted off-host backups and separately protected encryption-key recovery.
- [ ] Test restoration into an isolated environment; reconcile external actions before replay after restoring old database state.
- [ ] Monitor disk, database connections, queue backlog, worker health, API errors, backup freshness, and recurring schedule failures.
- [ ] Set log/workflow retention and restrict operational access to tenant data.
- [ ] Document deployment, migration, rollback, incident response, and uncertain-action reconciliation.
- [ ] Budget hosting, backups, domain, search, app actions, and optional monitoring separately from BYOK inference; verify checkout prices before purchasing.

Gate: complete end-to-end acceptance on the deployed account. Single-VM durability is not high availability.

## 15. Scale without changing tenant boundaries

- [ ] Establish capacity baselines from simultaneous task count, duration, context size, provider limits, and database load—not registered-user count.
- [ ] Define thresholds for queue latency, memory, CPU, and database saturation.
- [ ] Add worker processes/containers while preserving tenant fairness and interactive reservations.
- [ ] Before multiple machines, configure stable executor identities and coordinated recovery with Conductor or a tested equivalent.
- [ ] Add dedicated/managed PostgreSQL when capacity or availability requires it.
- [ ] Add redundant APIs/workers and load balancing when uptime requirements justify them.
- [ ] Cap database pools and provider concurrency; additional workers do not increase external API quotas.
- [ ] Test rolling deployments, worker loss, and recovery ownership before opening access further.
- [ ] Expand the allowlist gradually and monitor cost and response times.
- [ ] Keep future shell execution behind a sandbox service with tenant-scoped filesystem, network, secrets, and lifecycle boundaries.

## 16. Hackathon and final acceptance

- [ ] Verify current external event eligibility, deadline, provider/model restrictions, and submission requirements; eligibility under the changed architecture remains unresolved.
- [ ] Verify any AI Builder Program credits or partner benefits; record expiry and restrictions without assuming bundled services.
- [ ] Demonstrate signup, BYOK/model selection, Telegram linking, and app authorization.
- [ ] Demonstrate real app action and cited search results.
- [ ] Demonstrate schedule creation and execution.
- [ ] Demonstrate an evidence-backed proactive suggestion and acceptance.
- [ ] Demonstrate safe restart recovery and visible partial/uncertain outcomes.
- [ ] Publish setup instructions, architecture, operating-cost assumptions, and known limitations in the project documentation.
- [ ] Confirm there are no secrets in the repository, demo footage, logs, or exported artifacts.

## Final launch gate

- [ ] Correct tenant and connected account for every action.
- [ ] One logical task for repeated submissions.
- [ ] No blind replay of uncertain external side effects.
- [ ] Cancellation, revocation, and schedule changes respected at execution boundaries.
- [ ] Schedules survive restart with the specified overlap/backfill policy.
- [ ] Suggestions have evidence and do not spam.
- [ ] Interactive work remains responsive under background load.
- [ ] Backups restore successfully and operational recovery is documented.

## Reference links

These are implementation starting points from our discussion. Pin actual SDK versions and verify current API details while implementing.

- Pydantic AI: https://pydantic.dev/docs/ai/overview/
- Pydantic AI DBOS integration: https://pydantic.dev/docs/ai/capabilities/durable_execution/dbos/
- DBOS recovery: https://docs.dbos.dev/production/workflow-recovery
- DBOS schedules: https://docs.dbos.dev/python/tutorials/scheduled-workflows
- Gemini model discovery: https://ai.google.dev/api/models
- Tavily: https://docs.tavily.com/
- Composio: https://docs.composio.dev/
