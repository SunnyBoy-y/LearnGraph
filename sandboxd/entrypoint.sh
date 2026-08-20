#!/bin/sh
# sandboxd container entrypoint.
#
# Self-contained credential bootstrap for the default compose stack: on first
# start the daemon generates its service + admin tokens into the shared state
# volume (SANDBOXD_TOKEN_FILE / SANDBOXD_ADMIN_TOKEN_FILE point there), so
# `docker compose up` needs zero host-side setup and no Node runtime. When an
# operator mounts reviewed token files (docker-compose.sandbox.yml secrets),
# the files already exist and this script is a no-op — existing tokens are
# never overwritten.
set -eu

bootstrap_token() {
  path="${1:-}"
  if [ -n "$path" ] && [ ! -f "$path" ]; then
    mkdir -p "$(dirname "$path")"
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$path"
    # The shared volume is mounted read-only into the app container, whose
    # service user is NOT root — 600 would make it unreadable there. The
    # tokens never leave the compose-internal network (no published ports).
    chmod 644 "$path"
    echo "sandboxd: bootstrapped credential at $path"
  fi
}

bootstrap_token "${SANDBOXD_TOKEN_FILE:-}"
bootstrap_token "${SANDBOXD_ADMIN_TOKEN_FILE:-}"

exec tini -- python -m sandboxd.main
