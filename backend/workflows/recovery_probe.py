"""Harmless test-only queue. Requires disposable probe tables provisioned by the harness."""

import os
import socket
import threading
from time import monotonic

import psycopg
from dbos import DBOS, Queue

from backend.config import get_settings

probe_queue = Queue("vespers-recovery-probe", concurrency=1)


def connect() -> psycopg.Connection[tuple[object, ...]]:
    url = str(get_settings().dbos_system_database_url).replace(
        "postgresql+psycopg:", "postgresql:", 1
    )
    return psycopg.connect(url, connect_timeout=2)


@DBOS.step()
def completed_step(probe_id: str) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO recovery_probe.executions VALUES (%s, %s, %s)",
            (probe_id, socket.gethostname(), os.getpid()),
        )


@DBOS.step()
def unfinished_boundary(probe_id: str) -> None:
    deadline = monotonic() + 90
    with connect() as connection:
        connection.execute(
            "UPDATE recovery_probe.control SET reached = true WHERE id = %s", (probe_id,)
        )
    poll = threading.Event()
    while monotonic() < deadline:
        with connect() as connection:
            row = connection.execute(
                "SELECT released FROM recovery_probe.control WHERE id = %s", (probe_id,)
            ).fetchone()
        if row and row[0]:
            return
        poll.wait(0.1)
    raise TimeoutError("Recovery probe release deadline exceeded")


@DBOS.workflow()
def recovery_workflow(probe_id: str) -> str:
    completed_step(probe_id)
    unfinished_boundary(probe_id)
    return "recovered"
