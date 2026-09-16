"""Explicit bounded live runner; never imported or invoked by ordinary CI."""

import argparse
import asyncio
import json
import logging
import warnings
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from google.genai.types import HttpRetryOptions
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits
from tavily import AsyncTavilyClient  # type: ignore[import-untyped]

from backend.agent.spike import SpikeSettings

MODEL = "gemini-3.5-flash-lite"
QUERY = "Python official documentation asyncio library"


class BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(self, provider: str, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.provider = provider
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.counts = {"generation": 0, "discovery": 0, "search": 0}
        self.usage: list[dict[str, int]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.provider == "gemini" and request.url.host == "generativelanguage.googleapis.com":
            if request.method == "POST" and path.endswith(f"/models/{MODEL}:generateContent"):
                kind, limit = "generation", 6
                body = json.loads(request.content)
                if not 0 < body["generationConfig"]["maxOutputTokens"] <= 2048:
                    raise ValueError("Output-token cap exceeded")
                if len(request.content) >= 20000:
                    raise ValueError("Prompt size cap exceeded")
            elif request.method == "GET" and path.endswith(f"/models/{MODEL}"):
                kind, limit = "discovery", 2
            else:
                raise ValueError("Unapproved Gemini request")
        elif (
            self.provider == "tavily" and request.url.host == "api.tavily.com" and path == "/search"
        ):
            kind, limit = "search", 1
            body = json.loads(request.content)
            if (
                body["search_depth"] != "basic"
                or body["max_results"] != 3
                or body.get("include_images")
                or body.get("include_raw_content")
                or body["query"] != QUERY
            ):
                raise ValueError("Unapproved search parameters")
        else:
            raise ValueError("Unapproved endpoint")
        if self.counts[kind] >= limit:
            raise ValueError("Request cap reached")
        self.counts[kind] += 1
        response = await self.inner.handle_async_request(request)
        await response.aread()
        if len(response.content) > 256000:
            raise ValueError("Provider response exceeds byte limit")
        if response.is_success:
            payload = response.json()
            usage = payload.get("usageMetadata", payload.get("usage", {}))
            self.usage.append({k: v for k, v in usage.items() if isinstance(v, int)})
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


def normalize_search(payload: dict[str, Any]) -> list[dict[str, str]]:
    rows = payload.get("results")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        raise ValueError("Invalid result count")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid result")
        if not all(
            isinstance(row.get(k), str) and row[k].strip() for k in ("title", "url", "content")
        ):
            raise ValueError("Missing citation fields")
        if (
            urlsplit(row["url"]).scheme not in ("https", "http")
            or not urlsplit(row["url"]).netloc
            or len(row["url"]) > 2000
        ):
            raise ValueError("Invalid citation URL")
        normalized.append(
            {
                "title": row["title"][:200],
                "url": row["url"],
                "content": row["content"][:2000],
            }
        )
    return normalized


def failure(error: Exception) -> dict[str, Any]:
    code = getattr(error, "status_code", getattr(error, "code", None))
    return {
        "status": "failed",
        "error_type": type(error).__name__,
        "http_status": code if isinstance(code, int) else None,
    }


class Answer(BaseModel):
    label: str
    total: int


async def gemini(settings: SpikeSettings, report: dict[str, Any]) -> None:
    transport = BoundedTransport("gemini")
    transport.counts.update(report.get("counts", {}))
    transport.usage.extend(report.get("provider_usage", []))
    report["counts"] = transport.counts
    report["provider_usage"] = transport.usage
    async with httpx.AsyncClient(transport=transport, timeout=30, trust_env=False) as client:
        provider = GoogleProvider(
            api_key=settings.gemini_api_key.get_secret_value(),
            http_client=client,
            retry_options=HttpRetryOptions(attempts=1),
        )
        model = GoogleModel(MODEL, provider=provider)
        try:
            if report.get("discovery") != "pass":
                discovered = await provider.client.aio.models.get(model=MODEL)
                assert discovered.name == f"models/{MODEL}"
                report["discovery"] = "pass"
            config: ModelSettings = {"max_tokens": 2048, "temperature": 0, "timeout": 30}
            text_agent = Agent(model, retries=0, model_settings=config)
            text = await text_agent.run(
                "Reply with exactly VESPERS_OK.", usage_limits=UsageLimits(request_limit=1)
            )
            assert text.output.strip() == "VESPERS_OK"
            report["text"] = "pass"
            executed = []
            tool_agent = Agent(model, retries=0, model_settings=config)

            @tool_agent.tool_plain
            def synthetic_lookup(code: int) -> str:
                """Return the synthetic verification token for code 17."""
                assert code == 17
                executed.append(code)
                return "VESPERS_TOOL_731"

            result = await tool_agent.run(
                "Call synthetic_lookup with code 17. Reply with exactly the returned token.",
                usage_limits=UsageLimits(request_limit=3, tool_calls_limit=1),
            )
            assert executed == [17] and result.output.strip() == "VESPERS_TOOL_731"
            report["tool_and_final"] = "pass"
            structured_agent = Agent(
                model, output_type=NativeOutput(Answer), retries=0, model_settings=config
            )
            result2 = await structured_agent.run(
                "Return label 'synthetic' and total 42.", usage_limits=UsageLimits(request_limit=1)
            )
            assert result2.output == Answer(label="synthetic", total=42)
            report["structured"] = "pass"
            report["status"] = "pass"
        except Exception as error:
            report.update(failure(error))


async def tavily(settings: SpikeSettings, report: dict[str, Any]) -> None:
    transport = BoundedTransport("tavily")
    transport.counts.update(report.get("counts", {}))
    transport.usage.extend(report.get("provider_usage", []))
    report["counts"] = transport.counts
    report["provider_usage"] = transport.usage
    async with httpx.AsyncClient(
        base_url="https://api.tavily.com", transport=transport, timeout=30, trust_env=False
    ) as http:
        client = AsyncTavilyClient(api_key=settings.tavily_api_key.get_secret_value(), client=http)
        try:
            raw = await client.search(
                QUERY,
                search_depth="basic",
                max_results=3,
                include_images=False,
                include_raw_content=False,
                include_answer=False,
                auto_parameters=False,
                include_usage=True,
                timeout=30,
            )
            rows = normalize_search(raw)
            report.update(
                status="pass",
                results=len(rows),
                relevant=any("asyncio" in r["url"].lower() for r in rows),
                source_urls=[r["url"] for r in rows],
            )
        except Exception as error:
            report.update(failure(error))


async def run(
    settings: SpikeSettings, providers: list[str], previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "model": MODEL,
        "versions": {
            name: version(name)
            for name in ("pydantic-ai-slim", "google-genai", "tavily-python", "dbos")
        },
    }
    report.update(previous or {})
    for name in providers:
        report.setdefault(name, {})
        if report[name].get("status") == "pass":
            continue
        report[name]["status"] = "started"
        try:
            async with asyncio.timeout(90):
                await (gemini if name == "gemini" else tavily)(settings, report[name])
        except Exception as error:
            report[name].update(failure(error))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--free-allowance-confirmed", nargs="+", choices=["gemini", "tavily"])
    parser.add_argument("--resume-report", type=Path)
    args = parser.parse_args()
    if not args.live or not args.free_allowance_confirmed:
        parser.error("Explicit --live and provider free-allowance confirmation required")
    logging.disable(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    try:
        settings = SpikeSettings()
        if settings.gemini_model_id != MODEL:
            raise ValueError("Configured model must match approved model")
        previous = json.loads(args.resume_report.read_text()) if args.resume_report else None
        report = asyncio.run(
            run(settings, list(dict.fromkeys(args.free_allowance_confirmed)), previous)
        )
    except Exception as error:
        report = failure(error)
    print(json.dumps(report, indent=2))
    if report.get("status") == "failed" or any(
        isinstance(report.get(name), dict) and report[name].get("status") == "failed"
        for name in ("gemini", "tavily")
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
