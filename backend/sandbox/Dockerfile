# Chromium-only LearnGraph sandbox runner.
#
# The image intentionally packages one Debian Chromium binary rather than using
# Playwright's browser downloader or the three-engine Playwright base image.
# Playwright Core remains the CDP client only; it never downloads a browser.
# A separate Node stage supplies a current, reproducible Node 22 runtime while
# Python remains the project-standard 3.12 runtime.
#
# Base images are parameterized so registry-restricted build hosts (e.g. Aliyun
# ACR build runners that cannot reach Docker Hub) can point at a mirrored copy
# via build args NODE_BASE / PYTHON_BASE. Local builds keep the official
# defaults. Global ARGs must live above the first FROM to be usable there.

ARG NODE_BASE=node:22-bookworm-slim
ARG PYTHON_BASE=python:3.12-slim-bookworm

# Runner ABI contract: sandboxd refuses to bootstrap any image whose
# com.learngraph.runner-abi label is missing or outside the accepted range
# (sandboxd/sandboxd/config.py -> RUNNER_ABI_MIN / RUNNER_ABI_MAX). Bump this
# ARG together with the sandboxd range whenever the runner contract changes.
ARG RUNNER_ABI=1

FROM ${NODE_BASE} AS node-runtime

FROM node-runtime AS toolchain
ARG NPM_REGISTRY=https://registry.npmjs.org/
COPY toolchain/package.json toolchain/package-lock.json /tmp/toolchain/
RUN npm config set registry "${NPM_REGISTRY}" \
    && cd /tmp/toolchain \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && mv node_modules /node_modules \
    && npm cache clean --force

FROM ${PYTHON_BASE} AS runtime
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG RUNNER_ABI

# sandboxd verifies this label during prebuilt bootstrap
# (install_runtime -> runner_abi_mismatch if missing or out of range).
LABEL com.learngraph.runner-abi="${RUNNER_ABI}"

# Copy the official Node runtime as a complete /usr/local tree: node, npm,
# npx, corepack and their symlinks stay version-consistent without installing
# Debian's potentially stale nodejs package.
COPY --from=node-runtime /usr/local /usr/local

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_PATH=/node_modules \
    HOME=/tmp \
    XDG_CONFIG_HOME=/tmp/.config \
    XDG_CACHE_HOME=/tmp/.cache \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

# Chromium-only browser stack plus the fixed task toolchain. Do not add
# LibreOffice, Poppler, WPS tooling, Firefox, WebKit or OpenCV: the product
# supports content-level legacy conversion via deterministic extractors and
# agent reconstruction instead.
RUN set -eu; \
    apt_retry() { \
        attempts=4; \
        until apt-get update && apt-get install -y --no-install-recommends "$@"; do \
            attempts=$((attempts - 1)); \
            if [ "$attempts" -le 0 ]; then \
                echo "apt dependencies failed after repeated attempts" >&2; \
                exit 1; \
            fi; \
            echo "apt dependencies failed, retrying (${attempts} attempts left)…" >&2; \
            rm -rf /var/lib/apt/lists/*; \
            sleep 5; \
        done; \
    }; \
    apt_retry \
        antiword \
        bash \
        bzip2 \
        catdoc \
        chromium \
        chromium-sandbox \
        curl \
        ffmpeg \
        file \
        fontconfig \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        git \
        jq \
        libmagic1 \
        libsndfile1 \
        p7zip-full \
        procps \
        qpdf \
        tini \
        unzip \
        xz-utils \
        zip \
        zstd; \
    rm -rf /var/lib/apt/lists/*; \
    useradd --system --uid 65532 --no-create-home --shell /usr/sbin/nologin learngraph; \
    mkdir -p /workspace; \
    chown 65532:65532 /workspace

# Keep each dependency group explicit so build failures name the failing class.
# Only binary wheels are accepted: sandbox bootstrap never introduces a compiler
# or native build toolchain into the runtime image.
RUN set -eu; \
    pip_retry() { \
        group="$1"; shift; \
        attempts=4; \
        until pip install --disable-pip-version-check --timeout 60 --retries 10 \
            --prefer-binary --only-binary=:all: --index-url "${PIP_INDEX_URL}" "$@"; do \
            attempts=$((attempts - 1)); \
            if [ "$attempts" -le 0 ]; then \
                echo "pip ${group} dependencies failed after repeated attempts" >&2; \
                exit 1; \
            fi; \
            echo "pip ${group} dependencies failed, retrying (${attempts} attempts left)…" >&2; \
            sleep 5; \
        done; \
    }; \
    pip_retry core \
        mammoth==1.9.0 \
        pypdf==5.1.0 \
        openpyxl==3.1.5 \
        Pillow==11.1.0 \
        pydub==0.25.1 \
        httpx==0.28.1 \
        trafilatura==2.2.0; \
    pip_retry documents \
        PyMuPDF==1.26.7 \
        pdfplumber==0.11.8 \
        python-docx==1.2.0 \
        python-pptx==1.0.2 \
        lxml==6.1.1 \
        defusedxml==0.7.1 \
        beautifulsoup4==4.13.5 \
        charset-normalizer==3.4.9 \
        markdown-it-py==4.0.0 \
        striprtf==0.0.29; \
    pip_retry spreadsheets \
        numpy==2.3.2 \
        pandas==2.3.2 \
        XlsxWriter==3.2.9 \
        xlrd==2.0.2 \
        pyxlsb==1.0.10; \
    # odfpy is pure Python and publishes no wheel; permit only this source
    # distribution without introducing a compiler or native build dependency.
    pip_retry structured-text --no-binary=odfpy odfpy==1.4.1; \
    pip_retry media \
        mutagen==1.47.0 \
        av==16.0.0; \
    rm -rf /root/.cache/pip

COPY --from=toolchain /node_modules /node_modules
COPY --chown=65532:65532 runner.py /opt/learngraph/runner.py
COPY --chown=65532:65532 environment-manifest.json /opt/learngraph/environment-manifest.json
COPY --chown=65532:65532 browser-smoke.js /opt/learngraph/browser-smoke.js
COPY --chown=65532:65532 tasks/render.js /opt/learngraph/tasks/render.js
COPY --chown=65532:65532 tasks/web_render.js /opt/learngraph/tasks/web_render.js
COPY tasks/learngraph_tasks /usr/local/lib/python3.12/site-packages/learngraph_tasks

# Fail the build as soon as a dependency, browser executable or JavaScript
# toolchain drifts. Browser launch itself is exercised by the hardened smoke
# container because it must use the production seccomp profile.
RUN chmod 0555 /opt/learngraph/runner.py /opt/learngraph/browser-smoke.js /opt/learngraph/tasks/render.js /opt/learngraph/tasks/web_render.js \
    && test -s /opt/learngraph/environment-manifest.json \
    && chmod -R a+rX /usr/local/lib/python3.12/site-packages/learngraph_tasks /node_modules \
    && python --version \
    && node --version \
    && npm --version \
    && chromium --version \
    && ffmpeg -version \
    && command -v antiword >/dev/null \
    && command -v catppt >/dev/null \
    && command -v 7z >/dev/null \
    && python -c "import av, bs4, docx, fitz, mammoth, markdown_it, numpy, odf, openpyxl, pandas, pdfplumber, PIL, pydub, pypdf, pptx, pyxlsb, trafilatura, xlsxwriter, learngraph_tasks" \
    && cd /tmp && node -e "require('playwright-core'); Promise.all(['vite','vue','react','react-dom','typescript','tsx','pptxgenjs','@vitejs/plugin-vue','@vitejs/plugin-react','vite-plugin-singlefile'].map((m) => import(m))).then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); })"

USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sleep", "infinity"]
