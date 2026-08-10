FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

# Dependencies first, and without the project itself: this layer is keyed on the lockfile alone, so editing
# source does not reinstall SQLAlchemy, aiokafka and the rest on every build.
# README.md is here because pyproject declares it as the package readme, and hatchling refuses to build the
# wheel without it — not because the image has any use for the file.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY src ./src
COPY alembic.ini ./
RUN uv sync --frozen

ARG VERSION=local
ENV SERVICE_VERSION=$VERSION \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

# 8093: the next free port after search-service's 8092 in the platform's shared compose file and Prometheus
# scrape config.
EXPOSE 8093

# Probes readiness, not liveness. `/livez` deliberately checks nothing, so a container that had lost its
# database would keep reporting healthy — and conflating the two is how a brief database blip restarts every
# replica and turns a short outage into a long one.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8093/readyz > /dev/null || exit 1

CMD ["uvicorn", "arcadia_recommendation.main:app", "--host", "0.0.0.0", "--port", "8093"]
