#!/usr/bin/env bash
# LearnGraph 自托管一键升级：备份 → 拉取新镜像 → 替换容器 → 健康检查 → 失败自动回滚。
#
# 用法:
#   export LEARNGRAPH_EXPECTED_PROJECT=learngraph  # 写操作前填写实际项目名，禁止省略
#   ./scripts/docker-update.sh              # 按 compose 配置的 LEARNGRAPH_IMAGE 升级（默认 pull 最新 tag）
#   ./scripts/docker-update.sh ghcr.io/x/learngraph:v1.2.0   # 升级到指定镜像
#   ./scripts/docker-update.sh --check      # 只读检查：当前版本、栈健康状态、数据卷状态
#   ./scripts/docker-update.sh --backup-only  # 只做数据备份，不升级（可单独用于定时备份）
#
# 数据安全保证（本脚本从不使用 `-v`，绝不删除数据卷）:
#   1. 升级前对当前主库做一致性备份（SQLite 或 PostgreSQL）
#   2. 数据卷整体打包备份（含 storage/memory/密钥/审计）
#   3. 新容器通过健康检查后才算成功；失败自动用旧镜像回滚
#   4. 备份保留策略：保留最近 N 份（BACKUP_KEEP，默认 10）
#
# 环境变量:
#   LEARNGRAPH_EXPECTED_PROJECT 必填（除 --check/--help）；必须与目标容器项目及本脚本目录匹配
#   LEARNGRAPH_IMAGE     升级目标镜像（默认读 compose 默认值 learngraph:local）
#   BACKUP_DIR           备份输出目录（默认 ./backups，相对仓库根）
#   BACKUP_KEEP          保留的备份份数（默认 10）
#   WEB_PORT             主入口宿主端口，用于健康检查（默认 18000，随 LEARNGRAPH_PORT 覆盖）
#   COMPOSE_FILES        compose 文件列表（默认 docker-compose.yml [+ sandbox override 自动探测]）
#   UPDATE_TIMEOUT       健康检查等待秒数（默认 120）

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

STAMP="$(date +%Y%m%d-%H%M%S)-$$"
IMAGE="${LEARNGRAPH_IMAGE:-learngraph:local}"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
BACKUP_KEEP="${BACKUP_KEEP:-10}"
WEB_PORT="${WEB_PORT:-${LEARNGRAPH_PORT:-18000}}"
UPDATE_TIMEOUT="${UPDATE_TIMEOUT:-120}"
readonly EXPECTED_PROJECT="${LEARNGRAPH_EXPECTED_PROJECT:-}"
BACKUP_CONTAINER=""
BACKUP_REMOTE=""
BACKUP_LOCAL=""
DECLARED_SERVICES=""
BUSINESS_SERVICES=(app egress-proxy)

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
  [ -n "$EXPECTED_PROJECT" ] || die "必须显式设置 LEARNGRAPH_EXPECTED_PROJECT；未执行任何 Docker 维护命令"
  [[ "$BACKUP_KEEP" =~ ^[1-9][0-9]{0,3}$ ]] || die "BACKUP_KEEP 必须是正整数"
  [[ "$EXPECTED_PROJECT" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "预期项目名无效"
  if [ -f "$REPO_ROOT/.docker-test-isolation.json" ]; then
    local manifest_project
    manifest_project="$(sed -n 's/.*"project": "\([a-z0-9-]*\)".*/\1/p' "$REPO_ROOT/.docker-test-isolation.json")"
    [ -n "$manifest_project" ] && [ "$EXPECTED_PROJECT" = "$manifest_project" ] || die "Docker test isolation: 测试清单禁止选择正式或其他项目"
  fi
  command -v docker >/dev/null 2>&1 || die "docker 不可用，请在宿主机执行本脚本（不是容器内）"
  docker compose version >/dev/null 2>&1 || die "docker compose v2 不可用"
  # Pin the project explicitly; inherited COMPOSE_PROJECT_NAME cannot redirect it.
  COMPOSE_FILES=(--project-name "$EXPECTED_PROJECT" "${COMPOSE_FILES[@]}")
  DECLARED_SERVICES="$(docker compose "${COMPOSE_FILES[@]}" config --services)" || die "无法核验项目服务声明"
  if has_service preview; then BUSINESS_SERVICES=(app preview egress-proxy); fi
  local app_container containers item
  app_container="$(service_container app)"
  [ -n "$app_container" ] || die "预期的 Compose 项目尚未启动"
  containers="$(docker compose "${COMPOSE_FILES[@]}" ps --all -q)"
  for item in $containers; do
    verify_container_scope "$item"
  done
  case "$EXPECTED_PROJECT" in
    lg-pg-maintenance-*|lg-update-test-*)
      [ -f "$REPO_ROOT/.docker-test-isolation.json" ] || die "测试项目缺少强制隔离清单"
      cmp -s "$REPO_ROOT/compose.json" "$REPO_ROOT/docker-compose.yml" || die "测试 Compose 副本不一致"
      case "$(normalize_path "$BACKUP_DIR")/" in
        "$(normalize_path "$REPO_ROOT")/"*) ;;
        *) die "测试备份输出必须位于隔离项目目录" ;;
      esac
      [ -z "${LEARNGRAPH_DATA_DIR:-}" ] || die "测试模式禁止共享宿主数据目录"
      ;;
  esac
}

normalize_path() {
  local value="$1" windows=""
  if command -v cygpath >/dev/null 2>&1; then value="$(cygpath -m "$value")"; windows=1; fi
  if command -v realpath >/dev/null 2>&1; then
    value="$(realpath -m -- "$value")" || die "无法核验实际路径"
  elif [ -d "$value" ]; then
    value="$(cd -P "$value" && pwd -P)"
  else
    die "缺少 realpath，无法安全核验尚未创建的目录"
  fi
  if command -v cygpath >/dev/null 2>&1; then value="$(cygpath -m "$value")"; fi
  if [ -n "$windows" ]; then
    printf '%s' "$value" | tr '\\' '/' | sed 's:/*$::' | tr '[:upper:]' '[:lower:]'
  else
    printf '%s' "$value" | sed 's:/*$::'
  fi
}

verify_container_scope() {
  local project directory owner expected_owner volume volume_project destination service
  project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$1")"
  directory="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$1")"
  [ -n "$EXPECTED_PROJECT" ] && [ "$project" = "$EXPECTED_PROJECT" ] || die "Compose 项目不符，拒绝维护"
  [ "$(normalize_path "$directory")" = "$(normalize_path "$REPO_ROOT")" ] || die "容器工作目录与脚本项目不符，拒绝维护"
  case "$EXPECTED_PROJECT" in
    lg-pg-maintenance-*|lg-update-test-*)
      expected_owner="$(sed -n 's/.*"owner": "\([0-9a-f]\{32\}\)".*/\1/p' "$REPO_ROOT/.docker-test-isolation.json" 2>/dev/null)"
      owner="$(docker inspect --format '{{index .Config.Labels "io.learngraph.acceptance-owner"}}' "$1")"
      [ -n "$expected_owner" ] && [ "$owner" = "$expected_owner" ] || die "测试容器缺少匹配的隔离所有权标签"
      for destination in $(docker inspect --format '{{range .Mounts}}{{if and .RW (eq .Type "bind")}}{{.Destination}} {{end}}{{end}}' "$1"); do
        service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$1")"
        [ "$destination" = "/var/run/docker.sock" ] && [ "$service" = "database-migration-agent" ] || die "测试项目禁止可写宿主挂载"
      done
      # Tests may not even read a foreign volume. Production's existing
      # read-only integration mounts retain their previous behavior below.
      for volume in $(docker inspect --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}} {{end}}{{end}}' "$1"); do
        verify_backup_volume "$volume"
      done
      ;;
  esac
  for volume in $(docker inspect --format '{{range .Mounts}}{{if and .RW (eq .Type "volume")}}{{.Name}} {{end}}{{end}}' "$1"); do
    volume_project="$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$volume")"
    [ "$volume_project" = "$EXPECTED_PROJECT" ] || die "数据卷不属于预期项目，拒绝维护"
  done
}

scoped_exec() {
  verify_container_scope "$1"
  docker exec "$@"
}

has_service() {
  printf '%s\n' "$DECLARED_SERVICES" | grep -Fxq "$1"
}

service_container() {
  local id service
  has_service "$1" || die "当前项目未声明服务 $1，禁止跨项目查找"
  id="$(docker compose "${COMPOSE_FILES[@]}" ps --all -q "$1")"
  [ -n "$id" ] || die "预期服务 $1 尚未启动"
  case "$id" in *$'\n'*) die "服务 $1 有多个容器，拒绝维护" ;; esac
  verify_container_scope "$id"
  service="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$id")"
  [ "$service" = "$1" ] || die "容器服务名称不符，拒绝维护"
  printf '%s' "$id"
}

verify_backup_volume() {
  local project owner expected_owner
  project="$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$1")"
  [ "$project" = "$EXPECTED_PROJECT" ] || die "备份卷不属于预期项目，拒绝读取"
  case "$EXPECTED_PROJECT" in
    lg-pg-maintenance-*|lg-update-test-*)
      expected_owner="$(sed -n 's/.*"owner": "\([0-9a-f]\{32\}\)".*/\1/p' "$REPO_ROOT/.docker-test-isolation.json")"
      owner="$(docker volume inspect --format '{{index .Labels "io.learngraph.acceptance-owner"}}' "$1")"
      [ -n "$expected_owner" ] && [ "$owner" = "$expected_owner" ] || die "备份卷隔离所有权不符，拒绝读取"
      ;;
  esac
}

stack_running() {
  docker compose "${COMPOSE_FILES[@]}" ps --status running --quiet 2>/dev/null | grep -q .
}

business_healthy() {
  local expected="$1" role container
  for role in "${BUSINESS_SERVICES[@]}"; do
    container="$(service_container "$role")" || return 1
    [ "$(docker inspect --format '{{.Image}} {{.State.Health.Status}}' "$container")" = "$expected healthy" ] || return 1
  done
}

wait_business() {
  local image="$1" database="$2" iteration current_app actual_database
  for iteration in $(seq 1 $((UPDATE_TIMEOUT / 5))); do
    if business_healthy "$image" && curl -fsS --max-time 4 "http://127.0.0.1:${WEB_PORT}/api/v1/health" >/dev/null 2>&1; then
      current_app="$(service_container app)"
      actual_database="$(scoped_exec "$current_app" learngraph-entrypoint python -m app.services.database_maintenance status 2>/dev/null || true)"
      [ "$actual_database" = "$database" ] && return 0
    fi
    sleep 5
  done
  return 1
}

rollback_business() {
  local previous="$1" database="$2"
  [ -n "$previous" ] || die "缺少原镜像，无法回滚；请检查隔离/部署记录"
  LEARNGRAPH_IMAGE="$previous" docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --no-build "${BUSINESS_SERVICES[@]}" \
    || die "回滚失败；数据库备份保留在 ${BACKUP_DIR}"
  wait_business "$previous" "$database" || die "回滚未通过完整健康检查；数据库备份保留在 ${BACKUP_DIR}"
}

verify_image_compatibility() {
  local previous="$1" target="$2" before after
  before="$(docker image inspect --format '{{index .Config.Labels "io.learngraph.update-compatibility"}}' "$previous")"
  after="$(docker image inspect --format '{{index .Config.Labels "io.learngraph.update-compatibility"}}' "$target")"
  [ -n "$before" ] && [ "$before" != "<no value>" ] && [ "$before" = "$after" ] || die "数据库兼容标记不同或缺失，禁止自动更新/回滚"
}

current_version() {
  curl -fsS --max-time 5 "http://127.0.0.1:${WEB_PORT}/api/v1/health" 2>/dev/null \
    | sed -n 's/.*"version":"\([^"]*\)".*/\1/p' || echo "unknown"
}

backup_database() {
  local dest="$1" container remote="/data/.backup-work/learngraph-upgrade-${STAMP}.zip"
  container="$(service_container app)"
  [ -n "$container" ] || die "找不到 app 容器"
  [ ! -e "$dest" ] || die "备份目标已存在，禁止覆盖"
  BACKUP_LOCAL="$dest"
  say "主数据库一致性备份 -> $dest"
  BACKUP_CONTAINER="$container"
  BACKUP_REMOTE="$remote"
  local result expected actual
  result="$(MSYS_NO_PATHCONV=1 scoped_exec "$container" learngraph-entrypoint python -m app.services.database_maintenance backup \
    --output "$remote" --owner "shell-${STAMP}")" || die "主库备份失败，未修改服务"
  docker cp "${container}:${remote}" "$dest" >/dev/null 2>&1 || die "主库备份取出失败"
  chmod 600 "$dest"
  expected="$(printf '%s' "$result" | sed -n 's/.*"sha256": "\([a-f0-9]*\)".*/\1/p')"
  # Read stdin so GNU filename escaping (Windows backslashes/newlines) cannot
  # prepend a backslash to the digest and reject a valid native backup.
  actual="$(sha256sum < "$dest" | cut -d ' ' -f 1)"
  [ -n "$expected" ] && [ "$expected" = "$actual" ] || die "主库备份 SHA-256 不一致"
  BACKUP_LOCAL=""
  scoped_exec "$container" python -c "from pathlib import Path; Path('$remote').unlink(missing_ok=True)" >/dev/null
  BACKUP_REMOTE=""
}

backup_volume() {
  local volume dest stamp data_dir
  dest="$1"
  stamp="$2"
  # bind 挂载形态：LEARNGRAPH_DATA_DIR 设为宿主机路径时，数据直接落在该目录，
  # 直接 tar 打包（含 .master-key 主密钥）；否则走 Docker 命名卷打包。
  data_dir="${LEARNGRAPH_DATA_DIR:-}"
  if [ -n "$data_dir" ]; then
    say "数据目录(bind) $data_dir 打包备份 -> $dest/${stamp}-volume.tar.gz"
    MSYS_NO_PATHCONV=1 tar czf "${dest}/${stamp}-volume.tar.gz" -C "$data_dir" . 2>/dev/null \
      || die "数据目录备份失败（路径: $data_dir）"
  else
    # 卷的实际 docker 名称带 compose 项目前缀（learngraph_learngraph-data）。
    # 精确匹配项目卷，避免误中无前缀的同名卷（docker run -v 自动创建的匿名/裸卷）。
    local app_container
    app_container="$(service_container app)"
    volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$app_container")"
    [ -z "$volume" ] && die "找不到数据卷（docker volume ls 中无 learngraph 数据卷）"
    verify_backup_volume "$volume"
    say "数据卷 $volume 打包备份 -> $dest/${stamp}-volume.tar.gz"
    # MSYS_NO_PATHCONV=1：Git Bash 会把容器内 /backup/... 误转为 Windows 路径
    MSYS_NO_PATHCONV=1 docker run --rm -v "${volume}:/data:ro" -v "${dest}:/backup" alpine \
      tar czf "/backup/${stamp}-volume.tar.gz" -C /data . >/dev/null 2>&1 \
      || die "数据卷备份失败"
  fi

  # sandboxd 状态卷（控制面 state.db）：升级/回滚需要一致快照。
  local sandboxd_volume
  local sandboxd_container
  sandboxd_container=""
  # Never query an undeclared optional service: some client/environment
  # combinations return a container from another project instead of no match.
  if has_service sandboxd; then sandboxd_container="$(service_container sandboxd)"; fi
  sandboxd_volume=""
  if [ -n "$sandboxd_container" ]; then
    sandboxd_volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data/sandboxd"}}{{.Name}}{{end}}{{end}}' "$sandboxd_container")"
  fi
  if [ -n "$sandboxd_volume" ]; then
    verify_backup_volume "$sandboxd_volume"
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
  local pattern file index position
  local -a ordered
  # Keep filenames as array elements. ls|xargs splits spaces/newlines and can
  # turn an isolated backup pathname into unrelated deletion operands.
  for pattern in 'primary-*.backup.zip' '*-volume.tar.gz' '*-sandboxd-state.tar.gz'; do
    ordered=()
    for file in "$BACKUP_DIR"/$pattern; do
      [ -f "$file" ] || continue
      position=${#ordered[@]}
      for ((index=0; index<${#ordered[@]}; index++)); do
        if [ "$file" -nt "${ordered[index]}" ]; then position=$index; break; fi
      done
      ordered=("${ordered[@]:0:position}" "$file" "${ordered[@]:position}")
    done
    for ((index=keep; index<${#ordered[@]}; index++)); do rm -f -- "${ordered[index]}"; done
  done
}

do_backup() {
  umask 077
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  backup_database "${BACKUP_DIR}/primary-${STAMP}.backup.zip"
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
  if [ -n "${LEARNGRAPH_DATA_DIR:-}" ]; then
    echo "数据目录(bind): ${LEARNGRAPH_DATA_DIR}"
  else
    echo "数据卷:"
    docker volume ls --format '{{.Name}}' | grep -E 'learngraph' || warn "未发现数据卷（尚未 docker compose up？）"
  fi
  echo "备份目录: ${BACKUP_DIR}"
  ls -1t "${BACKUP_DIR}" 2>/dev/null | head -5 || true
}

do_update() {
  local target="${1:-$IMAGE}" old_id
  [[ "$UPDATE_TIMEOUT" =~ ^[1-9][0-9]{0,3}$ ]] && [ "$UPDATE_TIMEOUT" -ge 5 ] || die "UPDATE_TIMEOUT 必须至少为 5 秒的正整数"
  require_compose
  stack_running || die "栈未运行，先执行 docker compose up -d"

  say "开始升级，目标镜像: $target"
  local app_container database_status
  app_container="$(service_container app)"
  database_status="$(scoped_exec "$app_container" learngraph-entrypoint python -m app.services.database_maintenance status)" || die "无法确认当前主库"
  scoped_exec "$app_container" learngraph-entrypoint python -m app.services.database_maintenance reserve --owner "shell-${STAMP}" >/dev/null || die "数据库维护正在执行"
  trap 'release_update_reservation' EXIT
  say "升级前备份（数据安全护栏）..."
  do_backup

  old_id="$(docker inspect --format '{{.Image}}' "$app_container" 2>/dev/null || echo "")"
  local role role_id
  for role in preview egress-proxy; do
    role_id="$(service_container "$role")"
    [ "$(docker inspect --format '{{.Image}}' "$role_id")" = "$old_id" ] || die "业务容器镜像不同，请使用保留逐服务镜像记录的独立更新器"
  done

  if [ "$target" != "learngraph:local" ]; then
    say "拉取镜像 $target ..."
    docker pull "$target" || die "镜像拉取失败（已中止，未改动运行中的栈）"
  fi
  if printf '%s' "$database_status" | grep -q '"engine": "postgresql"'; then
    [ "$(docker image inspect --format '{{index .Config.Labels "io.learngraph.primary-database"}}' "$target")" = "sqlite-postgresql-v1" ] || die "目标镜像不支持 PostgreSQL，禁止回退 SQLite"
  fi
  local target_id
  target_id="$(docker image inspect --format '{{.Id}}' "$target")" || die "无法核验目标镜像身份"
  verify_image_compatibility "$old_id" "$target_id"

  say "替换容器（停机窗口开始）..."
  if ! LEARNGRAPH_IMAGE="$target_id" docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --no-build "${BUSINESS_SERVICES[@]}"; then
    warn "容器替换失败，恢复原业务镜像"
    rollback_business "$old_id" "$database_status"
    die "升级未完成，原服务版本已通过健康检查；备份保留在 ${BACKUP_DIR}"
  fi

  say "等待健康检查（最多 ${UPDATE_TIMEOUT}s）..."
  local ok=""
  for _ in $(seq 1 $((UPDATE_TIMEOUT / 5))); do
    if business_healthy "$target_id" && curl -fsS --max-time 4 "http://127.0.0.1:${WEB_PORT}/api/v1/health" >/dev/null 2>&1; then
      local actual_database current_app
      current_app="$(service_container app)"
      actual_database="$(scoped_exec "$current_app" learngraph-entrypoint python -m app.services.database_maintenance status 2>/dev/null || true)"
      [ "$actual_database" = "$database_status" ] || { sleep 5; continue; }
      # sandboxd override 下同时验证控制面健康（不发布宿主端口，走 docker exec 探测）。
      local sandboxd_ok=""
      if has_service sandboxd; then
        local sandboxd_id
        sandboxd_id="$(service_container sandboxd)"
        if scoped_exec "$sandboxd_id" \
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
    rollback_business "$old_id" "$database_status"
    die "升级失败，原服务版本已通过健康检查；数据库备份保留在 ${BACKUP_DIR}"
  fi
}

release_update_reservation() {
  local app_container
  if [ -n "$BACKUP_LOCAL" ] && [ -f "$BACKUP_LOCAL" ]; then
    rm -f -- "$BACKUP_LOCAL"
  fi
  if [ -n "$BACKUP_CONTAINER" ] && [ -n "$BACKUP_REMOTE" ]; then
    (scoped_exec "$BACKUP_CONTAINER" python -c "from pathlib import Path; Path('$BACKUP_REMOTE').unlink(missing_ok=True)") >/dev/null 2>&1 || true
  fi
  app_container="$(service_container app 2>/dev/null || true)"
  if [ -n "$app_container" ]; then
    scoped_exec "$app_container" learngraph-entrypoint python -m app.services.database_maintenance release --owner "shell-${STAMP}" >/dev/null 2>&1 || true
  fi
}

main() {
  case "${1:-}" in
    --check) do_check ;;
    --backup-only)
      require_compose
      container="$(service_container app)"
      scoped_exec "$container" learngraph-entrypoint python -m app.services.database_maintenance reserve --owner "shell-${STAMP}" >/dev/null || die "数据库维护正在执行"
      trap 'release_update_reservation' EXIT
      do_backup
      say "备份完成: ${BACKUP_DIR}"
      ;;
    --help|-h) sed -n '2,20p' "$0"; exit 0 ;;
    *) do_update "${1:-}" ;;
  esac
}

main "$@"
