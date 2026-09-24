# syntax=docker/dockerfile:1.7
# Lumira classifier – Multi-Stage-Build mit uv. Build-Context ist das Repo-Root:
#   docker build -f services/classifier/Dockerfile --target runtime -t lumira/classifier .

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
COPY services/classifier/pyproject.toml services/classifier/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-classifier --no-install-workspace --no-dev

# ------------------------------------------------------------------ dev
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-classifier --no-install-workspace
COPY packages/shared packages/shared
COPY services/classifier services/classifier
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-classifier
USER app
ENV PORT=8003
EXPOSE 8003
CMD ["uvicorn", "lumira_classifier.main:app", "--host", "0.0.0.0", "--port", "8003"]

# ------------------------------------------------------------------ build
FROM deps AS build
COPY packages/shared packages/shared
COPY services/classifier services/classifier
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-classifier --no-dev --no-editable

# ------------------------------------------------------------------ runtime
FROM base AS runtime
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
ENV PORT=8003
EXPOSE 8003
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "lumira_shared.healthcheck"]
CMD ["uvicorn", "lumira_classifier.main:app", "--host", "0.0.0.0", "--port", "8003", "--proxy-headers"]
