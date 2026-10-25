# syntax=docker/dockerfile:1.7

# Build: resolve exactly what uv.lock pins into a virtual environment.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# Run: no build tools, no source tree, a non-root user.
FROM python:3.13-slim
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY packs ./packs
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    FAA_PACK_DIR=/app/packs/investor_portal
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"]
CMD ["faa", "serve", "--host", "0.0.0.0", "--port", "8000"]
