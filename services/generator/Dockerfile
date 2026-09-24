# syntax=docker/dockerfile:1.7
# Lumira generator – Multi-Stage-Build mit uv + Blender (headless). Build-Context ist das Repo-Root:
#   docker build -f services/generator/Dockerfile --target runtime -t lumira/generator .
#
# Blender läuft mit seinem EIGENEN Python (3.11) als Subprozess, der Service mit 3.12.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.18
ARG BLENDER_VERSION=4.5.14

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ------------------------------------------------------------------ blender (nur Download)
FROM debian:trixie-slim AS blender
ARG BLENDER_VERSION
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    series="${BLENDER_VERSION%.*}"; \
    file="blender-${BLENDER_VERSION}-linux-x64.tar.xz"; \
    base="https://download.blender.org/release/Blender${series}"; \
    curl -fsSLo "/tmp/${file}" "${base}/${file}"; \
    curl -fsSLo /tmp/blender.sha256 "${base}/blender-${BLENDER_VERSION}.sha256"; \
    (cd /tmp && grep " ${file}\$" blender.sha256 | sha256sum -c -); \
    mkdir -p /opt/blender; \
    tar -xJf "/tmp/${file}" -C /opt/blender --strip-components=1; \
    rm -f "/tmp/${file}" /tmp/blender.sha256

# ------------------------------------------------------------------ base
FROM python:${PYTHON_VERSION}-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    BLENDER_BIN=/opt/blender/blender \
    BLENDER_SCRIPT=/app/services/generator/blender_scripts/build_scene.py
# Laufzeitbibliotheken, die das Blender-Binary auch im Hintergrundmodus lädt
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxext6 libxkbcommon0 \
      libsm6 libice6 libgl1 libegl1 libgomp1 libwayland-client0 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 app \
 && useradd --uid 1000 --gid app --shell /usr/sbin/nologin --create-home app
COPY --from=blender /opt/blender /opt/blender
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
COPY services/generator/pyproject.toml services/generator/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-generator --no-install-workspace --no-dev

# ------------------------------------------------------------------ dev
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-generator --no-install-workspace
COPY packages/shared packages/shared
COPY services/generator services/generator
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-generator
USER app
ENV PORT=8005
EXPOSE 8005
CMD ["uvicorn", "lumira_generator.main:app", "--host", "0.0.0.0", "--port", "8005"]

# ------------------------------------------------------------------ build
FROM deps AS build
COPY packages/shared packages/shared
COPY services/generator services/generator
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-generator --no-dev --no-editable

# ------------------------------------------------------------------ runtime
FROM base AS runtime
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app services/generator/blender_scripts services/generator/blender_scripts
USER app
ENV PORT=8005
EXPOSE 8005
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "lumira_shared.healthcheck"]
CMD ["uvicorn", "lumira_generator.main:app", "--host", "0.0.0.0", "--port", "8005", "--proxy-headers"]
