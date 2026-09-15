import argparse
import signal
import threading
from uuid import uuid4

from dbos import DBOS, DBOSConfig

from backend.config import get_settings
from backend.logging import configure_logging
from backend.workflows.smoke import smoke_workflow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    config: DBOSConfig = {
        "name": "vespers-worker",
        "application_version": "scaffold-v1",
        "system_database_url": str(settings.dbos_system_database_url),
    }
    DBOS(config=config)
    DBOS.launch()
    try:
        if args.smoke:
            assert smoke_workflow(str(uuid4())) == "ok"
            print("Durable smoke workflow completed")
        else:
            stopped = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stopped.set())
            signal.signal(signal.SIGINT, lambda *_: stopped.set())
            stopped.wait()
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    main()
