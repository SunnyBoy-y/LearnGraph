# Use Docker's bundled BuildKit frontend; no external syntax image is required.
# This avoids a bootstrap pull through registry mirrors before the build starts.
# LearnGraph application image: Vite production build + FastAPI.
#
# The sandbox runner (backend/sandbox/Dockerfile) stays a separate image.
# This file packages the product UI and API for self-hosted Compose deploys.
#
# NOTE: the uv 镜像引用必须带 digest 锁定（tag 引用每次构建都会回源
# ghcr.io 重新解析 tag，网络抖动会以 `Head .../manifests/0.12: EOF` 失败；
# digest 引用优先命中 BuildKit 本地内容库，无需 registry 往返）。

# Defaults remain Docker Hub; Compose can override the complete image references
# when the host's transparent registry mirror fails authentication.
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${NODE_IMAGE} AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY frontend/ ./
# Same-origin /api/v1. Leave empty so the browser never hard-codes a backend host.
ENV VITE_API_BASE_URL=
ARG SOURCE_CACHE_BUST=dev
RUN echo "LearnGraph source cache: ${SOURCE_CACHE_BUST}"
RUN npm run build

FROM ${PYTHON_IMAGE} AS backend

COPY --from=ghcr.io/astral-sh/uv:0.12@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_HTTP_TIMEOUT=300 \
    UV_CONCURRENT_DOWNLOADS=2 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY backend/pyproject.toml backend/uv.lock ./
# ``--frozen`` is required so the lock stays authoritative: without it uv
# silently re-resolves whenever the lock is out of date with pyproject.toml,
# which previously left the optional voice stack (pipecat-ai) unpinned at
# image build time.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev --extra voice --extra database-migration --frozen

ARG SOURCE_CACHE_BUST=dev
RUN echo "LearnGraph backend source cache: ${SOURCE_CACHE_BUST}"
COPY backend/app ./app
COPY backend/sandbox ./sandbox

FROM ${PYTHON_IMAGE} AS runtime

ARG APP_VERSION=v0.6
ARG UPDATE_COMPATIBILITY=1
ARG SOURCE_REPOSITORY=https://github.com/SunnyBoy-y/LearnGraph
LABEL org.opencontainers.image.version=$APP_VERSION \
      org.opencontainers.image.source=$SOURCE_REPOSITORY \
      io.learngraph.update-compatibility=$UPDATE_COMPATIBILITY \
      io.learngraph.app-topology=combined-preview-v1 \
      io.learngraph.primary-database=sqlite-postgresql-v1
ENV LEARNGRAPH_APP_VERSION=$APP_VERSION
COPY VERSION /app/VERSION
RUN python -c "import os,pathlib; expected=pathlib.Path('/app/VERSION').read_text().strip(); assert os.environ['LEARNGRAPH_APP_VERSION']==expected, 'APP_VERSION must exactly match VERSION / GitHub Release tag'"

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
    WORKING_DEBIAN_MIRROR=""; \
    set_debian_mirror() { \
        cp /tmp/debian.sources.orig /etc/apt/sources.list.d/debian.sources; \
        if [ "$1" != "deb.debian.org" ]; then \
            sed -i "s|deb\.debian\.org|$1|g" /etc/apt/sources.list.d/debian.sources; \
        fi; \
    }; \
    apt_install() { \
        mirrors="${WORKING_DEBIAN_MIRROR:-$preferred_mirror}"; \
        for fallback in "$preferred_mirror" deb.debian.org mirrors.aliyun.com mirrors.ustc.edu.cn mirrors.tuna.tsinghua.edu.cn; do \
            case " $mirrors " in \
                *" $fallback "*) ;; \
                *) mirrors="$mirrors $fallback" ;; \
            esac; \
        done; \
        for mirror in $mirrors; do \
            echo "Trying Debian apt mirror: $mirror"; \
            set_debian_mirror "$mirror"; \
            rm -rf /var/lib/apt/lists/*; \
            if apt-get update && apt-get install -y --no-install-recommends "$@"; then \
                WORKING_DEBIAN_MIRROR="$mirror"; \
                return 0; \
            fi; \
            echo "Debian apt mirror failed: $mirror" >&2; \
        done; \
        return 1; \
    }; \
    apt_install tini ca-certificates curl; \
    install -d /usr/share/postgresql-common/pgdg; \
    pgdg_key_ok=0; \
    for key_url in \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        https://mirrors.aliyun.com/postgresql/repos/apt/ACCC4CF8.asc; do \
        if curl --fail --location --connect-timeout 15 --max-time 60 --retry 3 \
            -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc "$key_url"; then \
            pgdg_key_ok=1; \
            break; \
        fi; \
    done; \
    [ "$pgdg_key_ok" -eq 1 ]; \
    pg_ok=0; \
    for pg_repo in \
        https://apt.postgresql.org/pub/repos/apt \
        https://mirrors.aliyun.com/postgresql/repos/apt; do \
        echo "Trying PostgreSQL apt repo: $pg_repo"; \
        echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] ${pg_repo} bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
        if apt_install postgresql-client-18; then \
            pg_ok=1; \
            break; \
        fi; \
    done; \
    [ "$pg_ok" -eq 1 ]; \
    rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/99retries /tmp/debian.sources.orig; \
    useradd --system --uid 1000 --create-home --home-dir /home/learngraph \
        --shell /usr/sbin/nologin learngraph; \
    mkdir -p /app /data; \
    chown learngraph:learngraph /app /data

WORKDIR /app

COPY --from=backend --chown=learngraph:learngraph /app/.venv /app/.venv
COPY --from=backend --chown=learngraph:learngraph /app/app /app/app
COPY --from=backend --chown=learngraph:learngraph /app/sandbox /app/sandbox
COPY --from=frontend --chown=learngraph:learngraph /frontend/dist /app/frontend-dist
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/learngraph-entrypoint

# Pipecat 的句级切分（pipecat.utils.string.match_endofsentence → NLTK
# sent_tokenize）每次 LLM 文本帧都会调用，缺少 punkt_tab 时语音管线每轮抛
# ErrorFrame，并把上一轮答复重新拼进本轮 TTS 文本（用户会听到上一轮回答被
# 重复朗读）。官方下载要访问 raw.githubusercontent.com（构建/运行容器都不可达），
# 故直接内置最小语料：见 docker/nltk_data/.../README。
COPY --chown=learngraph:learngraph docker/nltk_data /usr/local/share/nltk_data
ENV NLTK_DATA=/usr/local/share/nltk_data

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
    CMD python -m app.container_runtime health

ENTRYPOINT ["tini", "--", "learngraph-entrypoint"]
CMD ["python", "-m", "app.container_runtime"]
