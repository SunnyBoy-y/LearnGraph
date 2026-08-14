#!/bin/sh
set -eu

DATA_ROOT="${LEARNGRAPH_DATA_ROOT:-/data}"

mkdir -p \
  "${DATA_ROOT}/storage" \
  "${DATA_ROOT}/memory" \
  "${DATA_ROOT}/sandbox-workspaces" \
  "${DATA_ROOT}/egress-policies"

if [ "$(id -u)" = "0" ]; then
  # Named volumes and first-run mkdir happen as root. Always hand the
  # known data directories to the runtime user; skip a recursive walk so
  # operator bind-mounts keep their existing file ownership.
  chown learngraph:learngraph \
    "${DATA_ROOT}/storage" \
    "${DATA_ROOT}/memory" \
    "${DATA_ROOT}/sandbox-workspaces" \
    "${DATA_ROOT}/egress-policies"
  if [ "$(stat -c %u "${DATA_ROOT}" 2>/dev/null || echo 0)" = "0" ]; then
    chown learngraph:learngraph "${DATA_ROOT}"
  fi
  exec setpriv --reuid=learngraph --regid=learngraph --init-groups -- "$0" "$@"
fi

if [ "${LEARNGRAPH_SECRET_PROVIDER:-environment}" = "environment" ] && [ -z "${LEARNGRAPH_MASTER_KEY:-}" ]; then
  key_file="${DATA_ROOT}/.master-key"
  if [ ! -f "${key_file}" ]; then
    python -c 'import secrets, sys; sys.stdout.write(secrets.token_urlsafe(48))' > "${key_file}"
    chmod 600 "${key_file}"
    echo "Generated LEARNGRAPH_MASTER_KEY and stored it on the data volume."
  fi
  LEARNGRAPH_MASTER_KEY="$(cat "${key_file}")"
  export LEARNGRAPH_MASTER_KEY
fi

exec "$@"
