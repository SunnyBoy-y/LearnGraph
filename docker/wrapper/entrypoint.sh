#!/bin/sh
# lg-wrapper entrypoint: orchestrates the inner LearnGraph compose stack
# through the host Docker socket (DooD). Runs as non-root uid 65532; the
# docker-socket GID is granted at runtime via group_add, never baked in.
#
# Behaviour knobs (env):
#   LG_WRAPPER_ACTION          up|down               (default: up)
#   LG_WRAPPER_BUILD           1|0  add --build      (default: 1)
#   LG_WRAPPER_WAIT_HEALTHY    1|0  add --wait       (default: 0)
#   LG_WRAPPER_DOWN_ON_EXIT    down|stop|keep        (default: down)
#   LG_WRAPPER_FOLLOW_LOGS     1|0  stream inner logs to stdout (default: 0)
#   LG_WRAPPER_SANDBOX         1|0  include docker-compose.sandbox.yml (default: 1)
#
# Every LEARNGRAPH_* env var is passed through to the inner compose
# interpolation unchanged.

set -eu

PROJECT_DIR="${LG_COMPOSE_PROJECT_DIR:-/project}"
STATE_DIR="/run/wrapper-state"
SECRETS_DIR="/run/secrets"
SOCKET="/var/run/docker.sock"

log()  { echo "[lg-wrapper] $*"; }
fail() { echo "[lg-wrapper] ERROR: $*" >&2; exit 1; }

mkdir -p "$STATE_DIR/.docker" "$SECRETS_DIR"
chmod 700 "$STATE_DIR" "$SECRETS_DIR"

# --- 1. socket presence + daemon reachability (fail fast) -------------------
[ -S "$SOCKET" ] || fail "docker socket missing at $SOCKET (mount /var/run/docker.sock)"
export DOCKER_GID
DOCKER_GID="$(stat -c %g "$SOCKET" 2>/dev/null || echo 0)"
docker info >/dev/null 2>&1 || fail "cannot talk to Docker Engine via $SOCKET (wrong GID or daemon down)"

# --- 2. sandboxd tokens (idempotent, 0600, contents never logged) -----------
if [ ! -s "$SECRETS_DIR/sandboxd-token" ]; then
    (umask 077 && openssl rand -hex 32 > "$SECRETS_DIR/sandboxd-token")
    log "generated sandboxd token"
fi
if [ ! -s "$SECRETS_DIR/sandboxd-admin-token" ]; then
    (umask 077 && openssl rand -hex 32 > "$SECRETS_DIR/sandboxd-admin-token")
    log "generated sandboxd admin token"
fi
chmod 600 "$SECRETS_DIR/sandboxd-token" "$SECRETS_DIR/sandboxd-admin-token"

# Point the inner compose `secrets:` file refs at the mounted volumes (the
# bundled /project is read-only at runtime, so ./secrets cannot be written).
export LEARNGRAPH_SANDBOXD_TOKEN_FILE="$SECRETS_DIR/sandboxd-token"
export LEARNGRAPH_SANDBOXD_ADMIN_TOKEN_FILE="$SECRETS_DIR/sandboxd-admin-token"
# Host Service Bridge token: optional. An absent file becomes an empty dir
# mount and the backend skips the header (see docker-compose.yml comment).
: "${LEARNGRAPH_HOST_BRIDGE_TOKEN_FILE:=$STATE_DIR/host-bridge-token}"
export LEARNGRAPH_HOST_BRIDGE_TOKEN_FILE

# --- 3. run the inner stack --------------------------------------------------
cd "$PROJECT_DIR"

compose() {
    if [ "${LG_WRAPPER_SANDBOX:-1}" = "1" ]; then
        docker compose -f docker-compose.yml -f docker-compose.sandbox.yml "$@"
    else
        docker compose -f docker-compose.yml "$@"
    fi
}

ACTION="${LG_WRAPPER_ACTION:-up}"
case "$ACTION" in
    up)
        BUILD_FLAG=""
        [ "${LG_WRAPPER_BUILD:-1}" = "1" ] && BUILD_FLAG="--build"
        WAIT_FLAG=""
        [ "${LG_WRAPPER_WAIT_HEALTHY:-0}" = "1" ] && WAIT_FLAG="--wait"
        # shellcheck disable=SC2086
        compose up -d --remove-orphans $BUILD_FLAG $WAIT_FLAG
        touch "$STATE_DIR/ready"
        log "stack started (project=$PROJECT_DIR docker_gid=$DOCKER_GID build=${LG_WRAPPER_BUILD:-1})"
        ;;
    down)
        compose down
        rm -f "$STATE_DIR/ready"
        log "stack removed"
        exit 0
        ;;
    *)
        fail "unknown LG_WRAPPER_ACTION=$ACTION (expected up|down)"
        ;;
esac

# --- 4. stay alive; clean up on graceful stop -------------------------------
_cleaned=0
cleanup() {
    [ "$_cleaned" -eq 1 ] && return
    _cleaned=1
    log "shutting down"
    case "${LG_WRAPPER_DOWN_ON_EXIT:-down}" in
        down) compose down || log "compose down failed (daemon gone?)" ;;
        stop) compose stop || log "compose stop failed (daemon gone?)" ;;
        keep) log "leaving inner stack running" ;;
        *)    compose down || log "compose down failed (daemon gone?)" ;;
    esac
    rm -f "$STATE_DIR/ready"
    exit 0
}
trap cleanup INT TERM

if [ "${LG_WRAPPER_FOLLOW_LOGS:-0}" = "1" ]; then
    compose logs -f --tail 200 || log "inner log stream ended"
fi

# Idle until terminated. tini -g forwards SIGTERM to the whole process group,
# so cleanup() above fires promptly on `docker stop`.
while :; do
    sleep 3600
done
