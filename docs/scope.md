# Locked product scope

Vespers is a general-purpose Telegram agent with connected-app actions, API search,
persistent schedules, memory, and proactive use-case discovery. One shared bot serves
private chats. The Next.js dashboard is for onboarding and account management only.

Initially allow one verified real account and one Telegram link through a backend
allowlist. Build tenant isolation and test two synthetic tenants before exposing user
features. Shared always-on workers receive per-run tenant context and scoped clients.

Direct requests and configured schedules execute without approval screens. Clarify
missing recipients or accounts. Unsolicited suggestions require user acceptance;
acceptance initiates execution without another approval step.

Use FastAPI, Pydantic AI, DBOS, PostgreSQL, Nebius Token Factory BYOK, Composio,
and Tavily with a separate platform key. No browser, shell execution, or web chat.
Future shell work requires a separate sandbox service. No provider credits or shared
billing are assumed. The referenced program email is not present in this repository.

AGENTS.md and implementation-checklist.md contain the complete requirements.
