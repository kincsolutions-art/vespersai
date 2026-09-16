import os
import subprocess
import sys

import pytest

from backend.config import ConfigurationError, Settings

SENTINEL = "SYNTHETIC_PASSWORD_DO_NOT_LOG"


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql+psycopg://u:{SENTINEL}@host:bad/app_test",
        f"postgresql://u:{SENTINEL}@host/app_test",
        f"postgresql+asyncpg://u:{SENTINEL}@host/app_test",
    ],
)
def test_safe_invalid_configuration(url: str) -> None:
    with pytest.raises(ConfigurationError) as caught:
        Settings(
            _env_file=None,
            environment="test",
            database_url=url,
            dbos_system_database_url="postgresql+psycopg://u:x@host/dbos_test",
        )
    assert SENTINEL not in str(caught.value)
    assert caught.value.__context__ is None
    assert "database_url" in str(caught.value)


@pytest.mark.parametrize(
    "command",
    [
        ["-m", "backend.workflows.worker"],
        ["-m", "uvicorn", "backend.api.main:app", "--port", "0"],
        ["-m", "alembic", "upgrade", "head"],
    ],
)
@pytest.mark.parametrize(
    "scheme_port", ["postgresql+psycopg|bad", "postgresql|5432", "postgresql+asyncpg|5432"]
)
def test_startup_diagnostics(command: list[str], scheme_port: str) -> None:
    scheme, port = scheme_port.split("|")
    env = {k: v for k, v in os.environ.items() if not k.startswith("VESPERS_")}
    env.update(
        {
            "VESPERS_ENVIRONMENT": "test",
            "VESPERS_DATABASE_URL": f"{scheme}://u:{SENTINEL}@host:{port}/app_test",
            "VESPERS_MIGRATION_DATABASE_URL": f"{scheme}://u:{SENTINEL}@host:{port}/app_test",
            "VESPERS_DBOS_SYSTEM_DATABASE_URL": "postgresql+psycopg://u:x@host/dbos_test",
        }
    )
    result = subprocess.run(
        [sys.executable, *command], env=env, capture_output=True, text=True, timeout=20
    )
    assert result.returncode != 0
    assert SENTINEL not in result.stdout + result.stderr
    assert "Invalid configuration" in result.stderr


def test_supported_driver() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://u:x@host/app_test",
        dbos_system_database_url="postgresql+psycopg://u:x@host/dbos_test",
    )
    assert settings.database_url.scheme == "postgresql+psycopg"
