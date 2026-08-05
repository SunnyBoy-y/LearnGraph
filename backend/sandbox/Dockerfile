# LearnGraph unified sandbox runner: Python + Node + Playwright chromium-headless
# + ffmpeg + CJK fonts + pre-installed frontend toolchain. One image serves every
# runtime kind; containers always run with network none.
# Base digest is pinned for reproducibility; Bootstrap builds and re-pins the result.
#
# Debian base (glibc): the musl chromium from alpine cannot work with Playwright
# (crashpad init bug -> SIGTRAP, and Playwright ships no musl build), which
# broke web_render and the html_to_pdf/html_to_png learner tasks. Playwright's
# official glibc chromium-headless-shell is used instead for all headless
# rendering.
#
# Slim layout (two stages): npm and its Debian toolchain (~190MB under
# /usr/share/nodejs) exist only in the `toolchain` stage to produce /node_modules,
# then /node_modules is copied into the final image -- npm never lands in it.
# Debian's ffmpeg is kept as-is: replacing it with a static build buys nothing
# (its ~150MB removable codec libs equal the static binary size, and the
# mesa/libllvm chain it would also drop is pinned by chromium's libgbm dep).
FROM python:3.12-slim@sha256:507285ff17cb1d1756d3711c7543e82188f7a3752c79a9b3a4f9320640390300 AS base

LABEL com.learngraph.legacy-doc-extract="true"

# Mirror overrides for constrained networks (only used at build time).
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org/

# Fixed path Playwright resolves browsers from. Headless shell is the only
# browser component the image needs (it covers render / PDF / screenshot).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

# Frontend toolchain stage: node + npm are build-time only. package-lock.json
# makes the dependency graph reproducible; npm ci never mutates it during image
# builds. The stage's apt layer stays cached because COPY runs after it.
FROM base AS toolchain
COPY toolchain/package.json toolchain/package-lock.json /tmp/toolchain/
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm config set registry "${NPM_REGISTRY}" \
    && cd /tmp/toolchain \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund \
    && mv node_modules /node_modules \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# Final runtime image.
FROM base

# Runtime essentials. xvfb and the CJK fonts that duplicate fonts-noto-cjk
# (wqy/ipa/unifont/freefont) ride in transitively with ffmpeg; they are purged
# in this same RUN so no layer ever stores them. fonts-noto-cjk + color-emoji
# are kept for HTML/PDF rendering.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        nodejs ffmpeg antiword fonts-noto-cjk ca-certificates \
    && apt-get purge -y \
        xvfb fonts-unifont fonts-wqy-zenhei fonts-ipafont-gothic fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 65532 --no-create-home --shell /usr/sbin/nologin learngraph \
    && mkdir -p /workspace /ms-playwright \
    && chown 65532:65532 /workspace

# Document/media libraries importable from agent scripts (python -I keeps
# system site-packages on the path).
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
        mammoth==1.9.0 \
        pypdf==5.1.0 \
        openpyxl==3.1.5 \
        Pillow==11.1.0 \
        pydub==0.25.1 \
        httpx==0.28.1 \
        trafilatura==2.2.0

# Frontend toolchain built in the `toolchain` stage. Node resolves imports
# upward from /workspace, so per-session workspaces use vue/react/vite with
# zero downloads and zero workspace disk quota.
COPY --from=toolchain /node_modules /node_modules

# Playwright chromium-headless-shell (glibc) + its runtime libraries. This is
# the browser used by web_render (web_fetch JS fallback), render.js
# (html_to_pdf/html_to_png) and browser-smoke.js. --with-deps installs the
# required shared libraries via apt.
RUN node /node_modules/playwright-core/cli.js install --with-deps chromium-headless-shell \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /root/.cache/ms-playwright

COPY --chown=65532:65532 runner.py /opt/learngraph/runner.py
COPY --chown=65532:65532 browser-smoke.js /opt/learngraph/browser-smoke.js
COPY --chown=65532:65532 tasks/render.js /opt/learngraph/tasks/render.js
COPY --chown=65532:65532 tasks/web_render.js /opt/learngraph/tasks/web_render.js
COPY tasks/learngraph_tasks /usr/local/lib/python3.12/site-packages/learngraph_tasks

ENV NODE_PATH=/node_modules \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell

RUN chmod 0555 /opt/learngraph/runner.py /opt/learngraph/browser-smoke.js /opt/learngraph/tasks/render.js /opt/learngraph/tasks/web_render.js \
    && chmod -R a+rX /usr/local/lib/python3.12/site-packages/learngraph_tasks /node_modules \
    && python --version \
    && node --version \
    && ffmpeg -version \
    && command -v antiword >/dev/null \
    && python -c "import mammoth, pypdf, openpyxl, PIL, pydub, httpx, trafilatura, learngraph_tasks" \
    && cd /tmp && node -e "require('playwright-core'); Promise.all(['vite','vue','react','react-dom','@vitejs/plugin-vue','@vitejs/plugin-react','vite-plugin-singlefile'].map((m) => import(m))).then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); })" \
    && test -x "${PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}" \
    && cd /tmp && ffmpeg -f lavfi -i "sine=frequency=440:duration=1" -c:a pcm_s16le -y smoke.wav >/dev/null 2>&1 \
    && test -s /tmp/smoke.wav \
    && rm -f /tmp/smoke.wav

USER 65532:65532
WORKDIR /workspace
ENTRYPOINT []
CMD ["sleep", "infinity"]
