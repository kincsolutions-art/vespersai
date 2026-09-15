import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.base import RequestResponseEndpoint

from backend.config import Settings, get_settings
from backend.logging import configure_logging

logger = logging.getLogger("vespers.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings or get_settings()
        configure_logging(config.log_level)
        engine = create_async_engine(str(config.database_url), pool_pre_ping=True)
        app.state.engine = engine
        yield
        await engine.dispose()

    app = FastAPI(title="Vespers API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_log(request: Request, call_next: RequestResponseEndpoint) -> Response:

        request_id = str(uuid4())
        started = monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request",
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )
        return response

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(request: Request) -> JSONResponse:
        try:
            async with request.app.state.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return app


app = create_app()
