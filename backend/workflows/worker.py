import argparse
import signal
import threading
from uuid import uuid4

from dbos import DBOS, DBOSConfig

from backend.config import get_settings
from backend.logging import configure_logging
from backend.workflows.readiness import start_readiness
from backend.workflows.smoke import smoke_workflow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    if settings.recovery_probe_enabled:
        from backend.workflows import adapter_probe, recovery_probe  # noqa: F401

    config: DBOSConfig = {
        "name": "vespers-worker",
        "application_version": "scaffold-v2",
        "executor_id": f"vespers-smoke-{uuid4()}" if args.smoke else "vespers-single-worker",
        "run_admin_server": False,
        "system_database_url": str(settings.dbos_system_database_url),
    }
    DBOS(config=config)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    DBOS.launch()
    server = None
    try:
        if args.smoke:
            result = smoke_workflow(str(uuid4()))
            if result != "ok":
                raise RuntimeError("Smoke workflow failed")
            print("Durable smoke workflow completed")
        else:
            server = start_readiness(
                str(settings.dbos_system_database_url).replace(
                    "postgresql+psycopg:", "postgresql:", 1
                ),
                stopped,
            )
            stopped.wait()
    finally:
        stopped.set()
        if server is not None:
            server.shutdown()
            server.server_close()
        DBOS.destroy()


if __name__ == "__main__":
    main()
