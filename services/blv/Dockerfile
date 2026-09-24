# syntax=docker/dockerfile:1.7
# Lumira blv – Multi-Stage-Build mit uv. Build-Context ist das Repo-Root:
#   docker build -f services/blv/Dockerfile --target runtime -t lumira/blv .
# Den ANTHROPIC_API_KEY nie ins Image backen – nur zur Laufzeit als Umgebungsvariable/Secret.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.18

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ------------------------------------------------------------------ base
FROM python:${PYTHON_VERSION}-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid app --shell /usr/sbin/nologin --create-home app
WORKDIR /app

# ------------------------------------------------------------------ deps
FROM base AS deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY services/blv/pyproject.toml services/blv/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-blv --no-install-workspace --no-dev

# ------------------------------------------------------------------ dev
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-blv --no-install-workspace
COPY packages/shared packages/shared
COPY services/blv services/blv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-blv
USER app
ENV PORT=8004
EXPOSE 8004
CMD ["uvicorn", "lumira_blv.main:app", "--host", "0.0.0.0", "--port", "8004"]

# ------------------------------------------------------------------ build
FROM deps AS build
COPY packages/shared packages/shared
COPY services/blv services/blv
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-blv --no-dev --no-editable

# ------------------------------------------------------------------ runtime
FROM base AS runtime
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
ENV PORT=8004
EXPOSE 8004
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "lumira_shared.healthcheck"]
CMD ["uvicorn", "lumira_blv.main:app", "--host", "0.0.0.0", "--port", "8004", "--proxy-headers"]
