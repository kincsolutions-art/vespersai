FROM ghcr.io/astral-sh/uv:0.12.14 AS uv
FROM python:3.12.14-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend ./backend
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home app && chown -R app:app /app
USER app
CMD ["uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
