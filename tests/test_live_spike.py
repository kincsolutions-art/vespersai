import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from backend.agent.live_spike import MODEL, QUERY, BoundedTransport, normalize_search, tavily
from backend.agent.spike import SpikeSettings


def test_generation_cap_counts_attempts() -> None:
    async def run() -> None:
        transport = BoundedTransport(
            "gemini", httpx.MockTransport(lambda r: httpx.Response(200, json={}))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(6):
                await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                    json={"generationConfig": {"maxOutputTokens": 2048}},
                )
            with pytest.raises(ValueError, match="cap"):
                await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                    json={"generationConfig": {"maxOutputTokens": 2048}},
                )
        assert transport.counts["generation"] == 6

    asyncio.run(run())


@pytest.mark.parametrize("payload", [{}, {"results": []}, {"results": [{"title": "x"}]}])
def test_malformed_search(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        normalize_search(payload)


@pytest.mark.parametrize("status", [401, 429, 200, 0])
def test_tavily_error_handling(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["query"] == QUERY
        if status == 0:
            raise httpx.ReadTimeout("synthetic-tavily-secret")
        return httpx.Response(status, json={"detail": "synthetic-tavily-secret"})

    transport = BoundedTransport("tavily", httpx.MockTransport(respond))
    monkeypatch.setattr("backend.agent.live_spike.BoundedTransport", lambda _: transport)
    config = SpikeSettings(
        _env_file=None,
        gemini_api_key=SecretStr("synthetic-gemini-secret"),
        gemini_model_id=MODEL,
        tavily_api_key=SecretStr("synthetic-tavily-secret"),
    )
    report: dict[str, object] = {}
    asyncio.run(tavily(config, report))
    assert report["status"] == "failed"
    assert "synthetic-tavily-secret" not in json.dumps(report)
    assert transport.counts["search"] == 1


def test_gemini_complete_protocol_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agent.live_spike import gemini

    generations = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal generations
        if request.method == "GET":
            return httpx.Response(200, json={"name": f"models/{MODEL}"})
        generations += 1
        part: dict[str, object]
        if generations == 1:
            part = {"text": "VESPERS_OK"}
        elif generations == 2:
            part = {"functionCall": {"name": "synthetic_lookup", "args": {"code": 17}}}
        elif generations == 3:
            part = {"text": "VESPERS_TOOL_731"}
        else:
            part = {"text": '{"label":"synthetic","total":42}'}
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    transport = BoundedTransport("gemini", httpx.MockTransport(respond))
    monkeypatch.setattr("backend.agent.live_spike.BoundedTransport", lambda _: transport)
    config = SpikeSettings(
        _env_file=None,
        gemini_api_key="synthetic-key",
        gemini_model_id=MODEL,
        tavily_api_key="synthetic-key",
    )
    report: dict[str, object] = {}
    asyncio.run(gemini(config, report))
    assert report["status"] == "pass", report
    assert report["tool_and_final"] == "pass"
    assert report["structured"] == "pass"
    assert transport.counts == {"discovery": 1, "generation": 4, "search": 0}


def test_search_normalization_preserves_citations() -> None:
    rows = normalize_search(
        {"results": [{"title": "Python", "url": "https://docs.python.org/", "content": "x" * 3000}]}
    )
    assert rows[0]["url"] == "https://docs.python.org/"
    assert len(rows[0]["content"]) == 2000


def test_live_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from backend.agent.live_spike import main

    monkeypatch.setattr(sys, "argv", ["live_spike"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
