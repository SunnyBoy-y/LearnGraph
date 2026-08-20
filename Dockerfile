# syntax=docker/dockerfile:1.7
# LearnGraph application image: Vite production build + FastAPI.
#
# The sandbox runner (backend/sandbox/Dockerfile) stays a separate image.
# This file packages the product UI and API for self-hosted Compose deploys.
#
# NOTE: the uv 镜像引用必须带 digest 锁定（tag 引用每次构建都会回源
# ghcr.io 重新解析 tag，网络抖动会以 `Head .../manifests/0.12: EOF` 失败；
# digest 引用优先命中 BuildKit 本地内容库，无需 registry 往返）。

FROM node:22-bookworm-slim AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY frontend/ ./
# Same-origin /api/v1. Leave empty so the browser never hard-codes a backend host.
ENV VITE_API_BASE_URL=
RUN npm run build

FROM python:3.12-slim-bookworm AS backend

COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY backend/app ./app
COPY backend/sandbox ./sandbox

FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 1000 --create-home --home-dir /home/learngraph \
        --shell /usr/sbin/nologin learngraph \
    && mkdir -p /app /data \
    && chown learngraph:learngraph /app /data

WORKDIR /app

COPY --from=backend --chown=learngraph:learngraph /app/.venv /app/.venv
COPY --from=backend --chown=learngraph:learngraph /app/app /app/app
COPY --from=backend --chown=learngraph:learngraph /app/sandbox /app/sandbox
COPY --from=frontend --chown=learngraph:learngraph /frontend/dist /app/frontend-dist
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/learngraph-entrypoint

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    LEARNGRAPH_FRONTEND_DIST=/app/frontend-dist \
    LEARNGRAPH_DATA_ROOT=/data \
    LEARNGRAPH_DATABASE_URL=sqlite:////data/learngraph.db \
    LEARNGRAPH_STORAGE_ROOT=/data/storage \
    LEARNGRAPH_MEMORY_ROOT=/data/memory \
    LEARNGRAPH_SANDBOX_WORKSPACE_ROOT=/data/sandbox-workspaces \
    LEARNGRAPH_SANDBOX_EGRESS_POLICY_DIR=/data/egress-policies \
    LEARNGRAPH_SECRET_PROVIDER=environment \
    LEARNGRAPH_DEPLOYMENT_PROFILE=self_hosted_team

USER root
EXPOSE 8000 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/livez', timeout=4)"

ENTRYPOINT ["tini", "--", "learngraph-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
