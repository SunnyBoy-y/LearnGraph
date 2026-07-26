# LearnGraph unified sandbox runner: Python + Node + Chromium/Playwright +
# ffmpeg + CJK fonts + pre-installed frontend toolchain. One image serves every
# runtime kind; containers always run with network none.
# Base digest is pinned for reproducibility; Bootstrap builds and re-pins the result.
FROM python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

# Mirror overrides for constrained networks (only used at build time).
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org/

RUN apk add --no-cache nodejs npm chromium chromium-swiftshader nss freetype harfbuzz ca-certificates \
        ttf-freefont font-noto-cjk ffmpeg \
    && addgroup -g 65532 learngraph \
    && adduser -D -H -u 65532 -G learngraph learngraph

# Document/media libraries importable from agent scripts (python -I keeps
# system site-packages on the path).
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" \
        mammoth==1.9.0 \
        pypdf==5.1.0 \
        openpyxl==3.1.5 \
        Pillow==11.1.0 \
        pydub==0.25.1

# Frontend toolchain lives at /node_modules: Node resolves imports upward from
# /workspace, so per-session workspaces use vue/react/vite with zero downloads
# and zero workspace disk quota. playwright-core stays global for NODE_PATH.
COPY toolchain/package.json /package.json
RUN npm config set registry "${NPM_REGISTRY}" \
    && npm install --global --omit=dev playwright-core@1.62.0 \
    && cd / && npm install --omit=dev --no-audit --no-fund \
    && npm cache clean --force

COPY --chown=65532:65532 runner.py /opt/learngraph/runner.py
COPY --chown=65532:65532 browser-smoke.js /opt/learngraph/browser-smoke.js
COPY --chown=65532:65532 tasks/render.js /opt/learngraph/tasks/render.js
COPY tasks/learngraph_tasks /usr/local/lib/python3.12/site-packages/learngraph_tasks

ENV NODE_PATH=/usr/local/lib/node_modules \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
    PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium-browser

RUN chmod 0555 /opt/learngraph/runner.py /opt/learngraph/browser-smoke.js /opt/learngraph/tasks/render.js \
    && chmod -R a+rX /usr/local/lib/python3.12/site-packages/learngraph_tasks /node_modules \
    && python --version \
    && node --version \
    && ffmpeg -version \
    && python -c "import mammoth, pypdf, openpyxl, PIL, pydub, learngraph_tasks" \
    && cd /tmp && node -e "require('playwright-core'); Promise.all(['vite','vue','react','react-dom','@vitejs/plugin-vue','@vitejs/plugin-react','vite-plugin-singlefile'].map((m) => import(m))).then(() => process.exit(0)).catch((e) => { console.error(e); process.exit(1); })"

USER 65532:65532
WORKDIR /workspace
ENTRYPOINT []
CMD ["sleep", "infinity"]
