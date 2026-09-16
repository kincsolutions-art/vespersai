# Vespers — Coding Agent Instructions

This file governs implementation of Vespers. Follow these decisions when writing code, reviewing changes, or proposing infrastructure. Explicit subsequent user instructions take precedence. Do not reopen locked scope or add excluded features without a user request.

## Project status and purpose

Vespers is a general-purpose, proactive AI assistant accessed through a shared Telegram bot. It executes connected-app actions, searches through an API, runs persistent schedules, remembers useful user context, and discovers useful automations to suggest.

The project is planned from scratch. The implementation checklist is a plan, not evidence of completed code. Inspect the actual repository before assuming any component exists. Do not mark work complete without verifying it.

## Locked product scope

- One shared Telegram bot created through BotFather; private chats only.
- Web dashboard for authentication, onboarding, Telegram linking, BYOK/model selection, app connections, schedules, suggestions, memory/preferences, and task history.
- No web chat.
- Backend signup allowlist: initially one verified real account and one Telegram connection. Multi-tenancy must work from day one and be verified using synthetic tenants.
- Always-on shared trusted workers. No process, container, or VM per user for this API-only MVP.
- Google Gemini Developer API BYOK for inference, using Pydantic AI’s native GoogleProvider. Require an explicit model ID and a tested compatibility list; never use ambient/global keys, implicit models, Vertex AI, or provider fallbacks.
- Composio for connected-app authentication/actions. Direct Tavily API for web search, with a separate platform-managed key and per-tenant quotas.
- No browser execution. Shell is deferred beyond the MVP. Any future shell runs in a separate isolated sandbox, never inside a shared backend worker.
- No action approval screens or approval states. Execute direct requests and configured schedules within authorized capabilities. Ask only when essential information is missing or ambiguous.
- An unsolicited suggestion is not a standing instruction. Acceptance such as “do that” initiates the proposed work without another confirmation. Clarify an ambiguous reference rather than guessing.

## Architecture

| Component | Responsibility |
| --- | --- |
| Next.js | Account-management dashboard |
| FastAPI | Authenticated API and Telegram webhook |
| Pydantic AI | Model/tool loop and structured outputs |
| DBOS | Durable workflows, queues, schedules, recovery |
| PostgreSQL | Tenant data, conversation/task state, and DBOS persistence |
| Gemini Developer API | User-funded model inference |
| Composio | Tenant-scoped connected-app access |
| Tavily | API search with separate platform key and tenant quotas |
| WorkOS AuthKit | Selected authentication component |

Initial deployment: one modest always-on VPS, approximately 8 GB RAM as a starting assumption, using Docker Compose for HTTPS reverse proxy, dashboard, API, worker, and PostgreSQL. Benchmark before claiming capacity. No GPU is required for API inference.

Do not add Redis, a second queue framework, Kubernetes, OpenClaw, or sandbox infrastructure to the MVP without a concrete need and a user-approved scope change. DBOS runs in application processes; the single-worker PoC does not require a separate DBOS server or paid cloud service.

Suggested repository boundaries:

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

Respect existing repository conventions if implementation has already begun. Pin dependencies and commit lockfiles. Verify installed SDK interfaces before coding from examples; compatibility between the selected Gemini model, Pydantic AI, Composio, and DBOS must be tested. Composio live connection/action testing is explicitly deferred until onboarding UI exists; this does not block tenant foundations or authentication. Require an ownership-checked live action test before real connected-app execution.

## Tenant isolation: mandatory boundaries

1. Separate users, tenants, and memberships, starting with personal tenants.
2. Derive identity from authenticated membership or trusted Telegram/task mappings. Never accept model-selected tenant identity as authority.
3. Scope all tenant-owned tables, foreign keys, queries, caches, connection records, artifacts, memory, and history.
4. Use tenant-scoped repositories plus PostgreSQL row-level security on application tables. Application roles must not bypass policies. Set pooled-connection tenant context inside each transaction.
5. Keep DBOS system storage/permissions distinct; do not blindly apply application RLS to DBOS internals.
6. Supply separate context and scoped clients for each run. Never switch process-global API keys or store mutable user state in shared agent objects.
7. Check connection ownership, tenant/account status, authorization, and budget at execution boundaries, including recovered and scheduled tasks.
8. Test at least two synthetic tenants, including forged identifiers and pooled-connection reuse.
9. Dependency injection and prompt instructions are not security boundaries. Enforce boundaries in application code and database access.

## Credentials and providers

- Encrypt stored secrets with versioned encryption. Keep encryption keys outside the database and source repository; maintain separately protected recovery material.
- Persist credential references in tasks and workflows; resolve secrets only when needed by authorized code.
- Do not serialize secrets into DBOS histories, prompts, logs, traces, exceptions, or task events.
- Support credential replacement, revocation, deletion, and model unavailability. Define when running tasks resolve updated credentials.
- Cache model discovery by credential identity and invalidate on key changes. A model appearing in the API does not prove tool-call or structured-output compatibility.
- Never silently fall back to a platform-funded key or a different provider.
- Scheduled work and proactive discovery consume the user's BYOK allowance too; explain this during setup.
- Use a separate platform-managed Tavily key with per-tenant quotas for the MVP. Bound queries, results, and timeouts; preserve source URLs. Search billing is separate from Gemini BYOK and Composio app actions.
- Historical context: the referenced Nebius AI Builder Program email names Composio, but does not establish bundled pricing, API keys, or credits. The current provider choice supersedes that integration plan.
- External hackathon eligibility/model requirements remain unresolved. Do not imply Gemini satisfies NVIDIA/Nebius event requirements.
- Provider change approved 2026-09-15 because Nebius is unavailable to the project owner in Nepal. Keep free-tier Gemini spike data synthetic/non-sensitive; paid and unpaid API data terms differ. Consumer Gemini subscriptions do not establish API billing/quota.

## Authentication and Telegram linking

- Use established verified-email authentication and enforce the allowlist in backend routes, not just UI.
- Make account/tenant creation transactional and duplicate-callback safe.
- Secure sessions, apply appropriate CSRF protection, and rate-limit authentication/linking endpoints.
- Generate a short-lived, sufficiently random single-use code/token from an authenticated dashboard session. Store its hash, expiry, ownership, and redemption state.
- Offer code entry or a Telegram deep link. Redeem atomically and bind numeric Telegram user ID and private chat ID, never usernames.
- Reject token reuse, expiry, group chats, unlinked users, and automatic reassignment of existing links. Provide authenticated disconnect/relink controls.
- Validate Telegram webhook secrets. Persist and deduplicate update IDs before creating work; do not acknowledge durable acceptance before persistence succeeds.
- Support `/start`, `/help`, `/status`, and `/stop`. Give clear feedback for unsupported input, formatting/length constraints, blocked bots, and delivery failures.
- Preserve conversation order where required; distinguish new requests, corrections, and cancellation.

## Agent and tool execution

- Implement a reusable agent definition with per-run model, history, tenant context, budget, and cancellation state.
- Dynamically discover relevant Composio tools rather than supplying every integration schema on every run.
- Validate tool arguments and resource ownership. Resolve ambiguous recipients/accounts using stored defaults only when unambiguous; otherwise clarify.
- Use bounded history, tool results, model calls, tokens, retries, execution duration, and output sizes. Stop repetitive failing loops.
- Treat emails, documents, and search results as untrusted data, never authority to alter permissions or reveal secrets.
- Minimize private data sent to search. Attach evidence references to search-based claims; do not imply live search succeeded when it failed.
- Report completion from real tool results. Distinguish completed, failed, cancelled, partially completed, and uncertain outcomes.
- Record external action intent, attempt state, provider identifiers, and per-item bulk results. Handle stale records and conflicting updates explicitly.
- `/stop` prevents further eligible work; it cannot promise reversal of an action already accepted externally.

## Durable execution and retry semantics

- Persist tasks and use stable task/workflow identities for deduplication.
- Close the task-record-to-enqueue gap with an outbox or equivalent reconciliation.
- Integrate DBOS at model/tool boundaries rather than retrying an entire agent task indiscriminately.
- Keep cancellation and account revocation effective after recovery.
- Use provider idempotency keys when supported. If an external action may have succeeded before a timeout/crash, reconcile before repeating it.
- Never claim exactly-once external effects merely because workflows are durable.
- Persist final-delivery work, while handling ambiguous Telegram delivery outcomes.
- Define workflow versioning, draining, and compatible migration behavior for unfinished work.
- After restoring an old database backup, reconcile potentially completed external actions before replay.

## Schedules

- Schedules are a core MVP feature, manageable through both Telegram and dashboard.
- Support one-off and recurring schedules; creation, listing, editing, pause, resume, and deletion.
- Store original instruction, normalized timing, IANA timezone, version, next run, and optional end date. Start with Asia/Kathmandu for this user's default.
- Show interpreted timing and next occurrence after creation. Define DST gaps/repeated hours and short-month rules explicitly.
- Use persistent DBOS scheduling with unique occurrence identities.
- Permit one active run per schedule; skip overlaps.
- After downtime execute only the latest missed occurrence once, not the whole backlog. Implement this application policy explicitly rather than enabling unrestricted backfill.
- Recheck schedule version/status before queued execution. Pausing future runs and stopping the current task are separate operations.
- Resolve current credentials, permissions, account status, and budgets at execution time.
- Preserve run history; notify meaningfully about failures without repeated identical alerts.

## Memory and proactive discovery

- Proactive use-case discovery is a core feature, not an optional afterthought.
- Store explicit preferences separately from inferred facts; include evidence/provenance and timestamps.
- Support memory inspection, correction, deletion, and conflict-aware writes. Prefer current explicit instructions over stale memory.
- Keep critical constraints separate from lossy conversation summaries.
- Generate onboarding suggestions from goals and connected capabilities.
- Detect repeated requests and run one bounded daily discovery pass. Skip model work when there are no meaningful new signals.
- Return structured suggestions with evidence, benefit, proposed action, required connections, and timing where relevant.
- Verify evidence; deduplicate against schedules and past/pending suggestions. Revalidate stale suggestions before execution.
- Send at most one strong unsolicited suggestion daily, respect quiet hours, and send nothing when there is no useful suggestion.
- Support acceptance, dismissal, less-like-this feedback, and disabling suggestions.
- Avoid unnecessary sensitive notification content and self-reinforcing loops driven by the agent's own generated activity.
- Measure adoption and sustained usefulness; use separate discovery budgets and lower queue priority.

## Cost, fairness, and user experience

- Keep workers warm and external calls asynchronous where supported.
- Reserve capacity for interactive Telegram work; schedules/discovery must not occupy every execution slot.
- Apply per-tenant admission/concurrency limits. Reserve quotas atomically across concurrent tasks and settle measured usage afterward.
- Label usage estimates; provider billing cannot be guaranteed from incomplete token estimates.
- Measure p50/p95 queue delay, acknowledgement, first useful response, and completion time.
- Initial goals: sub-second backend acknowledgement after durable acceptance and approximately 3–5 seconds to useful output for simple tasks. These are benchmark targets, not promises.
- Avoid excessive progress messages. Provide actionable reconnect/credit/failure feedback and authoritative dashboard task state.
- Size capacity from simultaneous work, task duration, context size, database load, and provider quotas, not registered-user count.

## Deployment and scaling

- Keep PostgreSQL private. Configure persistent volumes, restart policies, health checks, resource limits, HTTPS, and restricted administration.
- Maintain encrypted off-host backups and perform restore drills. Monitor backup freshness, disk, queue latency, worker health, database connections, and API failures.
- Restrict operational access and redact tenant-sensitive logs. Define workflow/log retention.
- Document deploy, migration, rollback, incident response, and uncertain-action reconciliation.
- One VM provides no high availability even when workflow state is durable.
- Add worker capacity based on measurements. Before distributing workers across machines, configure executor identities and coordinated recovery with Conductor or a tested equivalent.
- Add dedicated/managed PostgreSQL, redundant APIs/workers, and load balancing as capacity/availability requires. More workers do not raise provider quotas.
- Expand the allowlist gradually after validating fairness, cost, and latency.

## Verification and definition of done

Implement meaningful tests for these release-critical behaviors:

- Cross-tenant API, tool, cache, conversation, schedule, memory, and background-task isolation.
- Duplicate signup/OAuth callbacks, linking races/reuse/expiry, duplicate Telegram updates, and double submissions.
- Revoked/replaced keys, exhausted credit, malformed model output, context limits, and provider timeouts.
- Crash before action, after external acceptance, before completion persistence, and during final delivery.
- Cancellation, account disablement, and revocation during queued/running/recovered tasks.
- Partial bulk success and uncertain side effects without blind replay.
- Schedule overlap, missed occurrences, edits/pauses racing execution, and timezone rules.
- Suggestion evidence, deduplication, dismissals, privacy, quiet hours, and budgets.
- Live traffic under background load, tenant fairness, and atomic quota reservation.
- Backup restoration, key recovery, and compatible deployment/recovery of unfinished workflows.

Do not claim production readiness based only on happy-path demos. Report what changed, how it was verified, and any remaining material limits. Keep checklists truthful. Never commit credentials or put them in demo footage or exported artifacts.

## Working rules for coding agents

- Inspect the repository and applicable instructions before editing. Use existing scripts and actual dependency versions; do not invent runnable commands or completion claims.
- Implement in small, reviewable increments following implementation-checklist.md when present. Start with tenant foundations and one end-to-end Telegram/tool path, then durability, schedules, and discovery.
- Keep reusable logic in the backend rather than coupling it to Telegram UI handlers.
- Use migrations for schema changes and protect unrelated user work.
- Complete authorized reversible implementation work without unnecessary permission prompts. Do not deploy, purchase services, or send third-party messages without applicable user authorization.
- Do not add approval UX to the product under the guise of reliability. Enforce authorization, budgets, idempotency, and clarification through the boundaries above.
- Check current official documentation when provider/SDK details are uncertain. Record tested versions and limitations.
