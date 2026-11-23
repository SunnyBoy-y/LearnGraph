# Use Docker's bundled BuildKit frontend; no external syntax image is required.
# This avoids a bootstrap pull through registry mirrors before the build starts.
# sandboxd image: the isolated sandbox control plane. This is the ONLY image
# that mounts /var/run/docker.sock in the Compose stack; LearnGraph app never
# talks to Docker Engine directly.
#
# The sandbox runner (backend/sandbox/Dockerfile) stays a separate image.
#
# NOTE: uv 镜像引用带 digest 锁定（tag 引用每次构建回源 ghcr.io 重验证，
# 网络抖动会以 `Head .../manifests/0.12: EOF` 失败；digest 引用优先命中
# BuildKit 本地内容库，无需 registry 往返）。

# Match the application's Python base, including any explicit registry override.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS sandboxd-build

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

FROM ${PYTHON_IMAGE} AS runtime

# 可选：国内构建时用 DEBIAN_MIRROR 覆盖 deb.debian.org（Fastly 线路偶发 502，
# 例如 --build-arg DEBIAN_MIRROR=mirrors.tuna.tsinghua.edu.cn）。
# 无论默认源或自定义源遇到 502 / 超时，都会自动在候选镜像间回退重试。
ARG DEBIAN_MIRROR=deb.debian.org
RUN set -eu; \
    cp /etc/apt/sources.list.d/debian.sources /tmp/debian.sources.orig; \
    { \
        echo 'Acquire::Retries "1";'; \
        echo 'Acquire::http::Timeout "15";'; \
        echo 'Acquire::https::Timeout "15";'; \
        echo 'Acquire::http::Pipeline-Depth "0";'; \
    } > /etc/apt/apt.conf.d/99retries; \
    preferred_mirror="${DEBIAN_MIRROR:-deb.debian.org}"; \
    mirrors="$preferred_mirror"; \
    for fallback in deb.debian.org mirrors.aliyun.com mirrors.ustc.edu.cn mirrors.tuna.tsinghua.edu.cn; do \
        case " $mirrors " in \
            *" $fallback "*) ;; \
            *) mirrors="$mirrors $fallback" ;; \
        esac; \
    done; \
    installed=0; \
    for mirror in $mirrors; do \
        echo "Trying Debian apt mirror: $mirror"; \
        cp /tmp/debian.sources.orig /etc/apt/sources.list.d/debian.sources; \
        if [ "$mirror" != "deb.debian.org" ]; then \
            sed -i "s|deb\.debian\.org|$mirror|g" /etc/apt/sources.list.d/debian.sources; \
        fi; \
        rm -rf /var/lib/apt/lists/*; \
        if apt-get update && apt-get install -y --no-install-recommends tini; then \
            installed=1; \
            break; \
        fi; \
        echo "Debian apt mirror failed: $mirror" >&2; \
    done; \
    [ "$installed" -eq 1 ]; \
    rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99retries /tmp/debian.sources.orig; \
    mkdir -p /app /var/lib/sandboxd; \
    chown 1000:1000 /app /var/lib/sandboxd

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
