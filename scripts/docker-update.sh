#!/usr/bin/env bash
# LearnGraph 自托管一键升级：备份 → 拉取新镜像 → 替换容器 → 健康检查 → 失败自动回滚。
#
# 用法:
#   ./scripts/docker-update.sh              # 按 compose 配置的 LEARNGRAPH_IMAGE 升级（默认 pull 最新 tag）
#   ./scripts/docker-update.sh ghcr.io/x/learngraph:v1.2.0   # 升级到指定镜像
#   ./scripts/docker-update.sh --check      # 只读检查：当前版本、栈健康状态、数据卷状态
#   ./scripts/docker-update.sh --backup-only  # 只做数据备份，不升级（可单独用于定时备份）
#
# 数据安全保证（本脚本从不使用 `-v`，绝不删除数据卷）:
#   1. 升级前对 SQLite 做在线备份（WAL 安全，业务不停）
#   2. 数据卷整体打包备份（含 storage/memory/密钥/审计）
#   3. 新容器通过健康检查后才算成功；失败自动用旧镜像回滚
#   4. 备份保留策略：保留最近 N 份（BACKUP_KEEP，默认 10）
#
# 环境变量:
#   LEARNGRAPH_IMAGE     升级目标镜像（默认读 compose 默认值 learngraph:local）
#   BACKUP_DIR           备份输出目录（默认 ./backups，相对仓库根）
#   BACKUP_KEEP          保留的备份份数（默认 10）
#   WEB_PORT             主入口宿主端口，用于健康检查（默认 18000，随 LEARNGRAPH_PORT 覆盖）
#   COMPOSE_FILES        compose 文件列表（默认 docker-compose.yml [+ sandbox override 自动探测]）
#   UPDATE_TIMEOUT       健康检查等待秒数（默认 120）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STAMP="$(date +%Y%m%d-%H%M%S)"
IMAGE="${LEARNGRAPH_IMAGE:-learngraph:local}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-10}"
WEB_PORT="${WEB_PORT:-${LEARNGRAPH_PORT:-18000}}"
UPDATE_TIMEOUT="${UPDATE_TIMEOUT:-120}"

COMPOSE_FILES=(-f docker-compose.yml)
if [ -f docker-compose.sandbox.yml ] && grep -q "docker-compose.sandbox.yml" README.md 2>/dev/null; then
  : # 探测逻辑见下方：仅当宿主是 Linux 且用户显式要求时叠加 sandbox override
fi
if [ -n "${LEARNGRAPH_SANDBOX_OVERRIDE:-}" ]; then
  COMPOSE_FILES+=(-f docker-compose.sandbox.yml)
fi

say() { printf '\033[1;34m[update]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[update]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[update]\033[0m %s\n' "$*" >&2; exit 1; }

require_compose() {
  command -v docker >/dev/null 2>&1 || die "docker 不可用，请在宿主机执行本脚本（不是容器内）"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 不可用"
}

stack_running() {
  docker compose "${COMPOSE_FILES[@]}" ps --status running --quiet 2>/dev/null | grep -q .
}

current_version() {
  curl -fsS --max-time 5 "http://127.0.0.1:${WEB_PORT}/api/v1/health" 2>/dev/null \
    | sed -n 's/.*"version":"\([^"]*\)".*/\1/p' || echo "unknown"
}

backup_sqlite() {
  local dest="$1" container
  container="$(docker compose "${COMPOSE_FILES[@]}" ps -q app 2>/dev/null | head -1)"
  [ -n "$container" ] || die "找不到 app 容器"
  say "SQLite 在线备份 -> $dest"
  # 容器内在线备份（WAL 安全），再拷回宿主，避免跨容器路径问题。
  docker compose "${COMPOSE_FILES[@]}" exec -T app python -c "
import sqlite3
src = sqlite3.connect('/data/learngraph.db')
dst = sqlite3.connect('/tmp/learngraph-upgrade-backup.db')
try:
    src.backup(dst)
finally:
    dst.close(); src.close()
print('sqlite backup ok')
" >/dev/null || die "SQLite 备份失败"
  docker cp "${container}:/tmp/learngraph-upgrade-backup.db" "$dest" >/dev/null 2>&1 \
    || die "SQLite 备份文件取出失败"
  docker compose "${COMPOSE_FILES[@]}" exec -T app rm -f /tmp/learngraph-upgrade-backup.db >/dev/null 2>&1 || true
}

backup_volume() {
  local volume dest stamp
  # 卷的实际 docker 名称带 compose 项目前缀（learngraph_learngraph-data）。
  # 精确匹配项目卷，避免误中无前缀的同名卷（docker run -v 自动创建的匿名/裸卷）。
  volume="$(docker volume ls --format '{{.Name}}' | grep -E '^learngraph_learngraph-data$' | head -1)"
  [ -z "$volume" ] && volume="$(docker volume ls --format '{{.Name}}' | grep -E 'learngraph[-_]data' | head -1)"
  [ -z "$volume" ] && die "找不到数据卷（docker volume ls 中无 learngraph 数据卷）"
  dest="$1"
  stamp="$2"
  say "数据卷 $volume 打包备份 -> $dest/${stamp}-volume.tar.gz"
  # MSYS_NO_PATHCONV=1：Git Bash 会把容器内 /backup/... 误转为 Windows 路径
  MSYS_NO_PATHCONV=1 docker run --rm -v "${volume}:/data:ro" -v "${dest}:/backup" alpine \
    tar czf "/backup/${stamp}-volume.tar.gz" -C /data . >/dev/null 2>&1 \
    || die "数据卷备份失败"

  # sandboxd 状态卷（控制面 state.db）：升级/回滚需要一致快照。
  local sandboxd_volume
  sandboxd_volume="$(docker volume ls --format '{{.Name}}' | grep -E 'learngraph.*sandboxd.*state|sandboxd-state' | head -1)"
  if [ -n "$sandboxd_volume" ]; then
    say "sandboxd 状态卷 $sandboxd_volume 打包备份 -> $dest/${stamp}-sandboxd-state.tar.gz"
    MSYS_NO_PATHCONV=1 docker run --rm -v "${sandboxd_volume}:/data:ro" -v "${dest}:/backup" alpine \
      tar czf "/backup/${stamp}-sandboxd-state.tar.gz" -C /data . >/dev/null 2>&1 \
      || warn "sandboxd 状态卷备份失败（继续主数据备份）"
  else
    warn "未发现 sandboxd 状态卷（当前栈可能未启用 sandboxd override）"
  fi
}

prune_backups() {
  local keep="$1"
  say "清理旧备份（保留最近 $keep 份）"
  ls -1t "${BACKUP_DIR}"/learngraph.db-*.backup 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
  ls -1t "${BACKUP_DIR}"/*-volume.tar.gz 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
  ls -1t "${BACKUP_DIR}"/*-sandboxd-state.tar.gz 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm -f
}

do_backup() {
  mkdir -p "$BACKUP_DIR"
  backup_sqlite "${BACKUP_DIR}/learngraph.db-${STAMP}.backup"
  backup_volume "$BACKUP_DIR" "$STAMP"
  prune_backups "$BACKUP_KEEP"
}

do_check() {
  say "当前版本: $(current_version)"
  if stack_running; then
    docker compose "${COMPOSE_FILES[@]}" ps --format 'table {{.Service}}\t{{.Status}}'
  else
    warn "栈未在运行"
  fi
  echo "数据卷:"
  docker volume ls --format '{{.Name}}' | grep -E 'learngraph' || warn "未发现数据卷（尚未 docker compose up？）"
  echo "备份目录: ${BACKUP_DIR}"
  ls -1t "${BACKUP_DIR}" 2>/dev/null | head -5 || true
}

do_update() {
  local target="${1:-$IMAGE}" old_id
  require_compose
  stack_running || die "栈未运行，先执行 docker compose up -d"

  say "开始升级，目标镜像: $target"
  say "升级前备份（数据安全护栏）..."
  do_backup

  old_id="$(docker inspect --format '{{.Image}}' "$(docker compose "${COMPOSE_FILES[@]}" ps -q app 2>/dev/null | head -1)" 2>/dev/null || echo "")"

  if [ "$target" != "learngraph:local" ]; then
    say "拉取镜像 $target ..."
    docker pull "$target" || die "镜像拉取失败（已中止，未改动运行中的栈）"
  fi

  say "替换容器（停机窗口开始）..."
  LEARNGRAPH_IMAGE="$target" docker compose "${COMPOSE_FILES[@]}" up -d --no-build

  say "等待健康检查（最多 ${UPDATE_TIMEOUT}s）..."
  local ok=""
  for _ in $(seq 1 $((UPDATE_TIMEOUT / 5))); do
    if curl -fsS --max-time 4 "http://127.0.0.1:${WEB_PORT}/api/v1/health" >/dev/null 2>&1; then
      # sandboxd override 下同时验证控制面健康（不发布宿主端口，走 docker exec 探测）。
      local sandboxd_ok=""
      if docker compose "${COMPOSE_FILES[@]}" ps -q sandboxd >/dev/null 2>&1; then
        if docker compose "${COMPOSE_FILES[@]}" exec -T sandboxd \
          python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/v1/health/live', timeout=4)" >/dev/null 2>&1; then
          sandboxd_ok=1
        fi
      else
        sandboxd_ok=1
      fi
      if [ -n "$sandboxd_ok" ]; then
        ok=1
        break
      fi
    fi
    sleep 5
  done

  if [ -n "$ok" ]; then
    say "✅ 升级成功，新版本: $(current_version)"
  else
    warn "⚠️ 健康检查未通过，自动回滚到旧镜像 ..."
    if [ -n "$old_id" ]; then
      LEARNGRAPH_IMAGE="$old_id" docker compose "${COMPOSE_FILES[@]}" up -d --no-build \
        || die "回滚失败！请手动检查（数据备份在 ${BACKUP_DIR}）"
    fi
    die "升级失败已回滚，数据未丢失（备份: ${BACKUP_DIR}）"
  fi
}

main() {
  case "${1:-}" in
    --check) do_check ;;
    --backup-only) require_compose; do_backup; say "备份完成: ${BACKUP_DIR}" ;;
    --help|-h) sed -n '2,20p' "$0"; exit 0 ;;
    *) do_update "${1:-}" ;;
  esac
}

main "$@"
