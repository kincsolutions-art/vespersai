# Provider compatibility spike

Status: live compatibility is pending. Locked dependencies establish reproducible
imports, not supported models or verified provider actions.

Before implementing the agent path:

1. With an authorized test Nebius key, list available models and select an eligible
   NVIDIA model. Verify structured output and actual Pydantic AI tool calls.
2. With a synthetic Composio identity and a designated test account, discover a tool
   and execute a reversible action; verify account binding and result shape.
3. Integrate Pydantic AI's DBOS adapter at model/tool boundaries. Inject process
   failures before and after step completion and verify recovery with PostgreSQL.
4. Record exact versions from uv.lock, model ID, date, results, and limitations.
   Never put keys or sensitive provider payloads in these records.

The infrastructure smoke workflow has no external effects and does not establish
safe retries for app actions or live-model compatibility.

Official interface references consulted while scaffolding:
- https://docs.dbos.dev/python/reference/dbos-class
- https://docs.dbos.dev/python/reference/configuration
- https://pydantic.dev/docs/ai/capabilities/durable_execution/dbos/
- https://docs.composio.dev/docs/quickstart
- https://nextjs.org/docs/app/getting-started/installation

Installed interface imports verified: DBOS 2.31.1, Pydantic AI 2.43.0
(including DBOSDurability), Composio 0.21.1. No live provider requests performed.
