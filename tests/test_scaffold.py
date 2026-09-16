import json
import logging

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.config import ConfigurationError, Settings
from backend.logging import JsonFormatter


def settings(**overrides: object) -> Settings:
    values = {
        "environment": "test",
        "database_url": "postgresql+psycopg://app:password@127.0.0.1:1/app_test?connect_timeout=1",
        "dbos_system_database_url": "postgresql+psycopg://dbos:password@127.0.0.1:1/dbos_test",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_rejects_shared_database() -> None:
    with pytest.raises(ConfigurationError, match="separate app/DBOS"):
        settings(dbos_system_database_url="postgresql+psycopg://dbos:password@localhost/app_test")


def test_rejects_cross_environment_database() -> None:
    with pytest.raises(ConfigurationError, match="environment suffixes"):
        settings(database_url="postgresql+psycopg://app:password@localhost/app_production")


def test_production_requires_encryption_key() -> None:
    with pytest.raises(ConfigurationError, match="production encryption key"):
        settings(
            environment="production",
            database_url="postgresql+psycopg://app:password@localhost/app_production",
            dbos_system_database_url="postgresql+psycopg://dbos:password@localhost/dbos_production",
        )


def test_health_distinguishes_liveness_from_database_outage() -> None:
    with TestClient(create_app(settings())) as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.headers["X-Request-ID"]
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json() == {"status": "unavailable"}
        assert "password" not in ready.text


def test_log_fields_do_not_copy_arbitrary_payloads() -> None:
    record = logging.LogRecord("vespers", logging.INFO, "", 0, "smoke_step", (), None)
    record.task_id = "test-task"
    record.secret = "must-not-appear"
    result = JsonFormatter().format(record)
    assert json.loads(result)["task_id"] == "test-task"
    assert "must-not-appear" not in result


def test_request_logs_record_the_path_but_never_the_query_string() -> None:
    """Query strings carry tenant ids; the path is what makes a log readable."""
    import json
    import logging

    from backend.logging import JsonFormatter

    record = logging.LogRecord("vespers.api", logging.INFO, __file__, 1, "http_request", None, None)
    record.method = "GET"
    record.path = "/api/account"
    record.status_code = 200
    payload = json.loads(JsonFormatter().format(record))
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/account"
    assert not any("tenant_id" in str(value) for value in payload.values())
