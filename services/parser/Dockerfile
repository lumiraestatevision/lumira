# syntax=docker/dockerfile:1.7
# Lumira parser – Multi-Stage-Build mit uv. Build-Context ist das Repo-Root:
#   docker build -f services/parser/Dockerfile --target runtime -t lumira/parser .
#
# Stages:  base → deps → dev      (docker-compose, Hot Reload, mit Test-Tools)
#                     └→ build → runtime  (Produktion: nur .venv, kein uv, kein Quellcode)

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.18

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ------------------------------------------------------------------ base
FROM python:${PYTHON_VERSION}-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"
# UID 1000 = Standard-Benutzer in WSL/Linux → keine Rechteprobleme bei Bind-Mounts
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
# Nur Metadaten → diese Schicht wird nur neu gebaut, wenn sich Abhängigkeiten ändern.
# --frozen statt --locked: Im Image fehlen die anderen Workspace-Member absichtlich.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY services/parser/pyproject.toml services/parser/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-parser --no-install-workspace --no-dev

# ------------------------------------------------------------------ dev
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-parser --no-install-workspace
COPY packages/shared packages/shared
COPY services/parser services/parser
# Editierbare Installation: docker-compose mountet src/ darüber → uvicorn --reload
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-parser
USER app
ENV PORT=8001
EXPOSE 8001
CMD ["uvicorn", "lumira_parser.main:app", "--host", "0.0.0.0", "--port", "8001"]

# ------------------------------------------------------------------ build
FROM deps AS build
COPY packages/shared packages/shared
COPY services/parser services/parser
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-parser --no-dev --no-editable

# ------------------------------------------------------------------ runtime
FROM base AS runtime
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
ENV PORT=8001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "lumira_shared.healthcheck"]
CMD ["uvicorn", "lumira_parser.main:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers"]
