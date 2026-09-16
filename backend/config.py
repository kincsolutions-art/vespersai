from functools import lru_cache
from typing import Any, ClassVar, Literal

from pydantic import PostgresDsn, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(ValueError):
    """Safe startup diagnostic containing field names only."""


class SafeSettings(BaseSettings):
    diagnostic_hint: ClassVar[str] = (
        "Check .env.example; use postgresql+psycopg URLs, valid ports, "
        "matching environment suffixes, "
        "separate app/DBOS databases, and a production encryption key."
    )
    model_config = SettingsConfigDict(
        env_prefix="VESPERS_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    environment: Literal["development", "test", "production"] = "development"

    def __init__(self, **values: Any) -> None:
        diagnostic: str | None = None
        try:
            super().__init__(**values)
        except ValidationError as error:
            fields = sorted(
                {
                    str(part)
                    for item in error.errors(include_input=False)
                    for part in item["loc"]
                    if str(part) in type(self).model_fields
                }
            )
            diagnostic = ", ".join(fields) or "environment/database separation/encryption_key"
        except ValueError:
            diagnostic = "environment settings"
        if diagnostic is not None:
            # Raise outside the handler: no original exception or input-bearing chain survives.
            raise ConfigurationError(f"Invalid configuration: {diagnostic}. {self.diagnostic_hint}")

    @field_validator("*", mode="after")
    @classmethod
    def supported_driver(cls, value: Any) -> Any:
        if isinstance(value, PostgresDsn) and value.scheme != "postgresql+psycopg":
            raise ValueError("Use postgresql+psycopg")
        return value

    @model_validator(mode="after")
    def environment_databases(self) -> "SafeSettings":
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, PostgresDsn):
                if not (value.path or "").endswith(f"_{self.environment}"):
                    raise ValueError("Database name must match environment")
        return self


class Settings(SafeSettings):
    database_url: PostgresDsn
    dbos_system_database_url: PostgresDsn
    log_level: Literal["INFO", "WARNING", "ERROR", "DEBUG"] = "INFO"
    signup_allowlist: list[str] = []
    encryption_key: SecretStr | None = None
    recovery_probe_enabled: bool = False

    @model_validator(mode="after")
    def separate_databases(self) -> "Settings":
        if self.database_url.path == self.dbos_system_database_url.path:
            raise ValueError("Application and DBOS databases must be separate")
        if self.environment == "production" and not self.encryption_key:
            raise ValueError("Production requires an external encryption key")
        if self.recovery_probe_enabled and self.environment != "test":
            raise ValueError("Recovery probe is test-only")
        return self


class MigrationSettings(SafeSettings):
    model_config = SettingsConfigDict(
        env_prefix="VESPERS_", env_file=".env.migrations", extra="ignore", hide_input_in_errors=True
    )
    migration_database_url: PostgresDsn


@lru_cache
def get_settings() -> Settings:
    return Settings()
