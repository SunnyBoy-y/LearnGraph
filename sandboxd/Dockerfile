# syntax=docker/dockerfile:1.7
# sandboxd image: the isolated sandbox control plane. This is the ONLY image
# that mounts /var/run/docker.sock in the Compose stack; LearnGraph app never
# talks to Docker Engine directly.
#
# The sandbox runner (backend/sandbox/Dockerfile) stays a separate image.
#
# NOTE: uv 镜像引用带 digest 锁定（tag 引用每次构建回源 ghcr.io 重验证，
# 网络抖动会以 `Head .../manifests/0.12: EOF` 失败；digest 引用优先命中
# BuildKit 本地内容库，无需 registry 往返）。

FROM python:3.12-slim-bookworm AS sandboxd-build

COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY sandboxd/pyproject.toml sandboxd/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY sandboxd/sandboxd ./sandboxd
# Seccomp profiles for the hardened runner (shared with the LearnGraph app
# image layout so the daemon finds them at its configured seccomp dir).
COPY backend/sandbox ./sandbox-profiles

FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /app /var/lib/sandboxd \
    && chown 1000:1000 /app /var/lib/sandboxd

WORKDIR /app

COPY --from=sandboxd-build /app/.venv /app/.venv
COPY --from=sandboxd-build /app/sandboxd /app/sandboxd
COPY --from=sandboxd-build /app/sandbox-profiles /app/sandbox-profiles
# Credential self-bootstrap entrypoint (token generation for the default
# compose stack; a no-op when reviewed secrets are mounted).
COPY sandboxd/entrypoint.sh /usr/local/bin/sandboxd-entrypoint.sh
RUN chmod +x /usr/local/bin/sandboxd-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    SANDBOXD_STATE_PATH=/var/lib/sandboxd/state.db \
    SANDBOXD_SECCOMP_DIR=/app/sandbox-profiles

# The daemon runs as root so it can access the mounted Docker socket on both
# Docker Desktop (WSL2) and native Linux without host GID plumbing.
USER root
EXPOSE 8090

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/v1/health/live', timeout=4)"

ENTRYPOINT ["/usr/local/bin/sandboxd-entrypoint.sh"]
