#!/bin/sh
# Build and start the hardened DooD wrapper on a Linux host. The wrapper runs
# the whole LearnGraph compose stack against the host Docker Engine; nothing
# here is privileged and no ports are published by the wrapper itself.
#
# The wrapper image is built from a CLEAN TAR CONTEXT (not `docker compose
# build`): Docker Desktop's lazy context fetch has been observed bypassing
# .dockerignore for very large directories (.claude, node_modules), silently
# baking local dev history into the image. The tar exclusion list below
# mirrors .dockerignore and MUST stay in sync with it.
#
# Requirements: Linux + Docker Engine (not Docker Desktop), GNU tar, repo
# checked out locally.
set -eu

cd "$(dirname "$0")/../.." || exit 1

if [ ! -S /var/run/docker.sock ]; then
    echo "error: /var/run/docker.sock not found — this wrapper needs a Linux host Docker Engine" >&2
    exit 1
fi

# The socket GID is granted to the wrapper as a supplementary group. Without
# it the docker CLI inside the wrapper gets permission denied.
export DOCKER_GID
DOCKER_GID="$(stat -c %g /var/run/docker.sock)"
echo "host docker socket gid: $DOCKER_GID"

WRAPPER_IMAGE="${LEARNGRAPH_WRAPPER_IMAGE:-learngraph-wrapper:local}"

# --- 1. build the wrapper image from a clean tar context ---------------------
echo "building $WRAPPER_IMAGE from a clean tar context"
tar --wildcards-match-slash \
    --exclude=.git --exclude=.github --exclude=.claude --exclude=.agents \
    --exclude=.idea --exclude=.vscode \
    --exclude='*node_modules' --exclude='*.venv' \
    --exclude='*__pycache__*' --exclude='*.pyc' \
    --exclude='*.db' --exclude='*.log' --exclude='*.tmp' \
    --exclude='*.tsbuildinfo' \
    --exclude=data --exclude='*backend/data*' --exclude='frontend/dist' \
    --exclude='frontend/coverage' --exclude='*.env' \
    --exclude=doc --exclude=docs --exclude=audit --exclude=blog-output \
    --exclude='backups;C' --exclude='*.md' --exclude=LICENSE \
    -cf - . | docker build -q -f docker/wrapper/Dockerfile -t "$WRAPPER_IMAGE" -

# Sanity guard: the clean image is ~150 MB; a multi-hundred-MB image means the
# exclusion list drifted and local dev artifacts leaked in.
SIZE_RAW="$(docker images --format '{{.Size}}' "$WRAPPER_IMAGE" | head -n 1 || true)"
TOO_BIG=0
case "$SIZE_RAW" in
    *GB) TOO_BIG=1 ;;
    *MB) [ "${SIZE_RAW%MB}" -gt 400 ] 2>/dev/null && TOO_BIG=1 ;;
esac
if [ "$TOO_BIG" -eq 1 ]; then
    echo "error: $WRAPPER_IMAGE is $SIZE_RAW — suspiciously large, refusing to start (exclusion list drift?)" >&2
    exit 1
fi

# --- 2. start the wrapper -----------------------------------------------------
docker compose -f docker/wrapper/docker-compose.wrapper.yml up -d

echo
echo "wrapper started. LearnGraph will be available at"
echo "  http://127.0.0.1:${LEARNGRAPH_PORT:-18000}"
echo "logs:  docker compose -f docker/wrapper/docker-compose.wrapper.yml logs -f lg-wrapper"
echo "stop:  sh docker/wrapper/stop.sh"
