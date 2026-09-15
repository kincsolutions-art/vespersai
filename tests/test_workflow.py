from pathlib import Path

from dbos import DBOS, SetWorkflowID

from backend.workflows.smoke import smoke_workflow


def test_smoke_retrieval_after_runtime_restart(tmp_path: Path) -> None:
    config = {
        "name": "vespers-test",
        "system_database_url": f"sqlite:///{tmp_path / 'dbos.sqlite'}",
        "run_admin_server": False,
    }
    DBOS(config=config)
    DBOS.launch()
    try:
        with SetWorkflowID("scaffold-smoke"):
            assert smoke_workflow("test-task") == "ok"
    finally:
        DBOS.destroy()
    DBOS(config=config)
    DBOS.launch()
    try:
        assert DBOS.retrieve_workflow("scaffold-smoke").get_result() == "ok"
    finally:
        DBOS.destroy()
