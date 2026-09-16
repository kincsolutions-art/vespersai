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

    # --- WorkOS AuthKit -------------------------------------------------
    # All optional: absent configuration must fail protected routes closed
    # without breaking offline tests or liveness checks.
    workos_client_id: str | None = None
    workos_api_key: SecretStr | None = None
    workos_api_base: str = "https://api.workos.com"
    # Accepted `iss` values. Leave empty to derive the default, which is what
    # WorkOS actually issues (verified against a live token on 2026-09-16):
    #
    #     https://api.workos.com/user_management/<client_id>
    #
    # NOT the value either documentation page shows. The AuthKit sessions guide
    # says "https://api.workos.com/" and the API reference "https://api.workos.com";
    # both are wrong for AuthKit session tokens. The real value is client-scoped,
    # which makes the issuer a second application-binding check alongside the
    # per-client key set. Set this explicitly for a custom auth domain, reading
    # the value from a decoded token as WorkOS advises.
    workos_issuers: list[str] = []
    # AuthKit session access tokens carry no `aud` claim by default; the key set
    # is per-client instead. WorkOS supports adding one through a JWT template,
    # which is the documented way to bind a token to a specific API. Set this only
    # when such a template is configured, or every live token is rejected.
    workos_audience: str | None = None
    # Explicit override for a custom auth domain whose key set is not served from
    # `{workos_api_base}/sso/jwks/{client_id}`.
    workos_jwks_url_override: str | None = None
    workos_algorithms: list[str] = ["RS256"]
    workos_leeway_seconds: int = 60
    # Bounded in-process limiter for auth/provisioning routes.
    auth_rate_limit_per_minute: int = 10

    @field_validator("workos_audience", "workos_jwks_url_override", "workos_api_key", mode="before")
    @classmethod
    def blank_is_unset(cls, value: Any) -> Any:
        """Compose supplies `VAR: ${VAR:-}` as an empty string, not as absent.

        Without this, an unset audience would arrive as "" — which is not None,
        so audience verification would switch on and reject every live token.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def workos_configured(self) -> bool:
        return bool(self.workos_client_id and self.workos_api_key)

    @property
    def workos_jwks_url(self) -> str:
        """Client-scoped key set: `{base}/sso/jwks/{client_id}`.

        Matches `@workos-inc/node` 10.13.0, whose `getJwksUrl(clientId)` returns
        exactly `${baseURL}/sso/jwks/${clientId}`. WorkOS signs with a key set
        unique to the client id, which is what stops another WorkOS application's
        token from verifying here.
        """
        if self.workos_jwks_url_override:
            return self.workos_jwks_url_override
        return f"{self.workos_api_base.rstrip('/')}/sso/jwks/{self.workos_client_id}"

    def _derived_issuers(self) -> list[str]:
        if not self.workos_client_id:
            return []
        base = self.workos_api_base.rstrip("/")
        return [f"{base}/user_management/{self.workos_client_id}"]

    @property
    def workos_accepted_issuers(self) -> tuple[str, ...]:
        """Configured issuers, each accepted with and without a trailing slash.

        `https://api.workos.com` and `https://api.workos.com/` name the same
        authority and WorkOS's own documentation uses both, so accepting the pair
        is not a weakening. Any other origin still fails.
        """
        configured = self.workos_issuers or self._derived_issuers()
        accepted: list[str] = []
        for issuer in configured:
            value = issuer.strip()
            if not value:
                continue
            for candidate in (value.rstrip("/"), value.rstrip("/") + "/"):
                if candidate not in accepted:
                    accepted.append(candidate)
        return tuple(accepted)

    @model_validator(mode="after")
    def safe_auth_configuration(self) -> "Settings":
        if any(
            algorithm.upper().startswith(("HS", "NONE")) for algorithm in self.workos_algorithms
        ):
            # Symmetric or "none" algorithms would let a JWKS public key be
            # replayed as a signing secret.
            raise ValueError("Asymmetric signing algorithms only")
        if self.environment == "production":
            if not self.workos_configured:
                raise ValueError("Production requires WorkOS client id and API key")
            if not self.workos_api_base.startswith("https://"):
                raise ValueError("Production requires an HTTPS WorkOS API base")
            if not self.workos_jwks_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS WorkOS JWKS URL")
            if not all(issuer.startswith("https://") for issuer in self.workos_accepted_issuers):
                raise ValueError("Production requires HTTPS WorkOS issuers")
        if self.workos_configured and not self.workos_accepted_issuers:
            # An empty issuer list would disable issuer verification entirely.
            raise ValueError("At least one WorkOS issuer is required")
        return self

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
