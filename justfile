default: check

install:
    uv sync

run:
    uv run uvicorn arcadia_recommendation.main:app --reload --port 8093

migrate:
    uv run alembic upgrade head

migration name:
    uv run alembic revision --autogenerate -m "{{name}}"

lint:
    uv run ruff check .
    uv run ruff format --check .

format:
    uv run ruff format .
    uv run ruff check --fix .

typecheck:
    uv run mypy

# .github/workflows/ci.yml mirrors this. The behaviour of this service against real events is covered by the
# platform's end-to-end suite (infra/test/e2e/test_15_recommendations.py), which needs Kafka, Postgres and
# four other services — a job for the platform, not for this repository's pipeline.
check: lint typecheck
