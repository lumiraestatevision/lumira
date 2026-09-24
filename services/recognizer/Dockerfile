# syntax=docker/dockerfile:1.7
# Lumira recognizer – Multi-Stage-Build mit uv. Build-Context ist das Repo-Root:
#   CPU:  docker build -f services/recognizer/Dockerfile --target runtime -t lumira/recognizer:cpu .
#   GPU:  docker build -f services/recognizer/Dockerfile --target runtime \
#           --build-arg TORCH_VARIANT=cu130 -t lumira/recognizer:cu130 .
#
# GPU-Betrieb braucht KEIN CUDA-Basis-Image: Die CUDA-Laufzeit kommt mit den PyTorch-Wheels,
# den Treiber stellt das NVIDIA Container Toolkit bereit (docker run --gpus all).

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.12.18

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ------------------------------------------------------------------ base
FROM python:${PYTHON_VERSION}-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    EASYOCR_MODULE_PATH=/models/easyocr \
    TORCH_HOME=/models/torch
# libglib2.0: von OpenCV benötigt; /models: Volume für Modellgewichte
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0t64 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 app \
 && useradd --uid 1000 --gid app --shell /usr/sbin/nologin --create-home app \
 && mkdir -p /models && chown app:app /models
WORKDIR /app

# ------------------------------------------------------------------ deps
FROM base AS deps
ARG TORCH_VARIANT=cpu
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY services/recognizer/pyproject.toml services/recognizer/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-recognizer --extra "${TORCH_VARIANT}" --no-install-workspace --no-dev

# ------------------------------------------------------------------ dev
FROM deps AS dev
ARG TORCH_VARIANT=cpu
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-recognizer --extra "${TORCH_VARIANT}" --no-install-workspace
COPY packages/shared packages/shared
COPY services/recognizer services/recognizer
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-recognizer --extra "${TORCH_VARIANT}"
USER app
ENV PORT=8002
EXPOSE 8002
CMD ["uvicorn", "lumira_recognizer.main:app", "--host", "0.0.0.0", "--port", "8002"]

# ------------------------------------------------------------------ build
FROM deps AS build
ARG TORCH_VARIANT=cpu
COPY packages/shared packages/shared
COPY services/recognizer services/recognizer
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package lumira-recognizer --extra "${TORCH_VARIANT}" --no-dev --no-editable

# ------------------------------------------------------------------ runtime
FROM base AS runtime
COPY --from=build --chown=app:app /app/.venv /app/.venv
USER app
ENV PORT=8002
EXPOSE 8002
VOLUME ["/models"]
HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=3 \
    CMD ["python", "-m", "lumira_shared.healthcheck"]
CMD ["uvicorn", "lumira_recognizer.main:app", "--host", "0.0.0.0", "--port", "8002", "--proxy-headers"]
