#!/bin/sh
# Gracefully stop the inner stack AND the wrapper. Step 1 runs a disposable
# wrapper container with LG_WRAPPER_ACTION=down, so this also cleans up ghost
# inner objects if the long-running wrapper was SIGKILLed.
set -eu

cd "$(dirname "$0")/../.." || exit 1

FILE="docker/wrapper/docker-compose.wrapper.yml"

if [ ! -S /var/run/docker.sock ]; then
    echo "error: /var/run/docker.sock not found — cannot reach the daemon to stop the inner stack" >&2
    exit 1
fi
export DOCKER_GID
DOCKER_GID="$(stat -c %g /var/run/docker.sock)"

# 1. bring the inner stack down through a disposable wrapper container.
docker compose -f "$FILE" run --rm --no-deps -T -e LG_WRAPPER_ACTION=down lg-wrapper

# 2. remove the wrapper container itself (token/state volumes persist).
docker compose -f "$FILE" down

echo "wrapper and inner stack stopped (volumes lg-wrapper-secrets / lg-wrapper-state kept)"
