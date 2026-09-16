"""Scoped spike configuration, separate from the future user credential resolver."""

from pydantic import SecretStr, field_validator
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_settings import SettingsConfigDict

from backend.config import SafeSettings


class SpikeSettings(SafeSettings):
    diagnostic_hint = (
        "Check .env.spike.example; supply explicit test keys, and an explicit model ID."
    )
    model_config = SettingsConfigDict(
        env_prefix="VESPERS_SPIKE_",
        env_file=".env.spike",
        extra="ignore",
        hide_input_in_errors=True,
    )
    gemini_api_key: SecretStr
    gemini_model_id: str
    composio_api_key: SecretStr = SecretStr("")
    tavily_api_key: SecretStr
    composio_test_user_id: str = ""
    # Composio read-only app-action test only; Tavily uses its own key.
    composio_test_connected_account_id: str = ""

    @field_validator("gemini_api_key", "tavily_api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Explicit test key required")
        return value

    @field_validator("gemini_model_id")
    @classmethod
    def explicit_selection(cls, value: str) -> str:
        if not value.strip() or value.strip().lower() == "latest":
            raise ValueError("Explicit selection required")
        return value.strip()


def build_spike_model(settings: SpikeSettings) -> GoogleModel:
    """Construct fresh scoped objects; performs no model request or compatibility assertion."""
    provider = GoogleProvider(api_key=settings.gemini_api_key.get_secret_value())
    return GoogleModel(settings.gemini_model_id, provider=provider)
