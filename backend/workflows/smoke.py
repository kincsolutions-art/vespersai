import logging

from dbos import DBOS

logger = logging.getLogger("vespers.workflows")


@DBOS.step()
def smoke_step(task_id: str) -> str:
    logger.info("smoke_step", extra={"task_id": task_id})
    return "ok"


@DBOS.workflow()
def smoke_workflow(task_id: str) -> str:
    """Infrastructure-only workflow: no tenant data or external side effects."""
    return smoke_step(task_id)
