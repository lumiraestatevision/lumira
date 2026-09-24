# syntax=docker/dockerfile:1.7
# Lumira frontend – Next.js mit pnpm. Build-Context ist frontend/:
#   docker build --target runtime -t lumira/frontend ./frontend
#
# Stages:  base → deps → dev      (docker-compose, next dev mit Hot Reload)
#                     └→ build → runtime  (Produktion: Next.js standalone, nur node)

ARG NODE_VERSION=24

# ------------------------------------------------------------------ base
FROM node:${NODE_VERSION}-alpine AS base
ENV NEXT_TELEMETRY_DISABLED=1 \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
# pnpm-Version kommt aus "packageManager" in package.json
RUN corepack enable
WORKDIR /app

# ------------------------------------------------------------------ deps
FROM base AS deps
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# ------------------------------------------------------------------ dev
FROM deps AS dev
COPY . .
# .next vorab anlegen: docker-compose mountet dort ein anonymes Volume, das sonst root gehört.
RUN mkdir -p /app/.next && chown -R node:node /app
USER node
EXPOSE 3000
CMD ["pnpm", "dev"]

# ------------------------------------------------------------------ build
FROM deps AS build
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN pnpm build

# ------------------------------------------------------------------ runtime
FROM node:${NODE_VERSION}-alpine AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["wget", "-qO-", "http://127.0.0.1:3000/"]
CMD ["node", "server.js"]
