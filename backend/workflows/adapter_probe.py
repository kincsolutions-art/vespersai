"""Test-only Pydantic AI DBOS adapter probe, registered before resident DBOS launch."""

import os
import socket
import threading
from time import monotonic

from dbos import DBOS
from pydantic_ai import Agent
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from backend.workflows.recovery_probe import connect


def count(label: str) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO recovery_probe.executions VALUES (%s,%s,%s)",
            (label, socket.gethostname(), os.getpid()),
        )


def make_model() -> FunctionModel:
    # The model holds a synthetic credential in a closure; it must not cross durable boundaries.
    sentinel = os.environ["VESPERS_ADAPTER_TEST_SECRET"]

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not sentinel:
            raise RuntimeError("Missing synthetic credential")
        has_tool = any(
            isinstance(part, ToolReturnPart) for message in messages for part in message.parts
        )
        if not has_tool:
            count("adapter:model-first")
            return ModelResponse(parts=[ToolCallPart("adapter_add", {"a": 17, "b": 25})])
        count("adapter:model-final")
        with connect() as connection:
            connection.execute("UPDATE recovery_probe.control SET reached=true WHERE id='adapter'")
        deadline = monotonic() + 90
        event = threading.Event()
        while monotonic() < deadline:
            with connect() as connection:
                row = connection.execute(
                    "SELECT released FROM recovery_probe.control WHERE id='adapter'"
                ).fetchone()
            if row and row[0]:
                return ModelResponse(parts=[TextPart("42")])
            event.wait(0.1)
        raise TimeoutError("Adapter probe boundary deadline")

    return FunctionModel(respond, model_name="vespers-deterministic-adapter")


agent = Agent(
    make_model(),
    name="vespers-adapter-probe",
    retries=0,
    capabilities=[DBOSDurability(parallel_execution_mode="sequential")],
)


@agent.tool_plain
@DBOS.step()
def adapter_add(a: int, b: int) -> int:
    count("adapter:tool")
    return a + b


@DBOS.workflow()
async def adapter_workflow() -> str:
    result = await agent.run("Add 17 and 25 using adapter_add; return the result.")
    if result.output != "42":
        raise ValueError("Wrong deterministic result")
    return result.output
