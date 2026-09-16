from unittest.mock import patch

import pytest

from backend.agent.spike import SpikeSettings, build_spike_model
from backend.config import ConfigurationError


def settings(**overrides: object) -> SpikeSettings:
    values = dict(
        gemini_api_key="synthetic-gemini-secret",
        gemini_model_id="gemini-synthetic-test",
        composio_api_key="synthetic-composio-secret",
        tavily_api_key="synthetic-tavily-secret",
        composio_test_user_id="synthetic-user",
        _env_file=None,
    )
    values.update(overrides)
    return SpikeSettings(**values)


def test_model_factory_is_scoped_and_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "ambient-must-not-be-used")
    with (
        patch("backend.agent.spike.GoogleProvider", autospec=True) as provider,
        patch("backend.agent.spike.GoogleModel", autospec=True) as model,
    ):
        first = settings()
        second = settings(gemini_api_key="other-synthetic-secret")
        build_spike_model(first)
        build_spike_model(second)
        assert provider.call_count == 2
        assert provider.call_args_list[0].kwargs == {"api_key": "synthetic-gemini-secret"}
        assert provider.call_args_list[1].kwargs == {"api_key": "other-synthetic-secret"}
        model.assert_called_with("gemini-synthetic-test", provider=provider.return_value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"gemini_api_key": ""},
        {"gemini_model_id": ""},
        {"tavily_api_key": ""},
        {"gemini_model_id": "latest"},
    ],
)
def test_invalid_spike_configuration_is_redacted(overrides: dict[str, object]) -> None:
    with pytest.raises(ConfigurationError) as caught:
        settings(**overrides)
    assert "synthetic-gemini-secret" not in str(caught.value)
    assert "synthetic-composio-secret" not in str(caught.value)
    assert "synthetic-tavily-secret" not in str(caught.value)
    assert caught.value.__context__ is None


def test_search_does_not_require_app_connection() -> None:
    config = settings()
    assert config.composio_test_connected_account_id == ""
    assert "synthetic-gemini-secret" not in repr(config)


def test_native_google_provider_uses_developer_api() -> None:
    from pydantic_ai.providers.google import GoogleProvider

    with patch("pydantic_ai.providers.google.Client", autospec=True) as client:
        GoogleProvider(api_key="synthetic-explicit-key")
        assert client.call_args.kwargs["api_key"] == "synthetic-explicit-key"
        assert client.call_args.kwargs["vertexai"] is False
