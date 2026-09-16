.PHONY: install check format dev-api dev-dashboard worker migrate smoke up
install:
	uv sync --frozen
	npm ci --prefix apps/dashboard
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy backend migrations
	uv run pytest
	npm run lint --prefix apps/dashboard
	npm run typecheck --prefix apps/dashboard
	npm run format:check --prefix apps/dashboard
format:
	uv run ruff check --fix .
	uv run ruff format .
	npm run format --prefix apps/dashboard
dev-api:
	uv run uvicorn backend.api.main:app --reload --no-access-log
dev-dashboard:
	npm run dev --prefix apps/dashboard
worker:
	uv run python -m backend.workflows.worker
migrate:
	uv run alembic upgrade head
smoke:
	uv run python -m backend.workflows.worker --smoke
up:
	docker compose up --build -d

.PHONY: integration
integration:
	uv run python tests/integration/run.py
