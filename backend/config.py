from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VESPERS_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: PostgresDsn
    dbos_system_database_url: PostgresDsn
    log_level: Literal["INFO", "WARNING", "ERROR", "DEBUG"] = "INFO"
    signup_allowlist: list[str] = []
    encryption_key: SecretStr | None = None

    @model_validator(mode="after")
    def separate_databases(self) -> "Settings":
        if self.database_url.path == self.dbos_system_database_url.path:
            raise ValueError("Application and DBOS databases must be separate")
        for url in (self.database_url, self.dbos_system_database_url):
            if not (url.path or "").endswith(f"_{self.environment}"):
                raise ValueError("Database names must end with the environment name")
        if self.environment == "production" and not self.encryption_key:
            raise ValueError("Production requires an external encryption key")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
