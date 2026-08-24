#!/usr/bin/env bash
# sqlite-gate-watchdog.sh — LearnGraph 宿主机自愈脚本（纯 ASCII，兼容 Linux/macOS/Git Bash）
#
# 背景：SQLite 写门闸一旦被跨线程移交卡死（历史 bug），app 容器会陷入
# 「写请求 30s 超时 → 无闸放行 → busy_timeout 兜底 → 请求堆积」，
# 最终被 Docker Healthcheck 判为 unhealthy。修复版内置计数锁 + watchdog 后
# 该故障已自愈，但升级前的存量部署仍可用本脚本兜底：探测到异常即
# `docker compose restart app`（优雅重启，WAL 安全，不丢已提交数据）。
#
# 用法（宿主 cron，每 1-2 分钟一次）：
#   # Linux crontab
#   */1 * * * * /path/to/LearnGraph/scripts/sqlite-gate-watchdog.sh \
#       --compose-dir /path/to/LearnGraph >> /var/log/learngraph-watchdog.log 2>&1
#
#   # 手动执行一次
#   ./scripts/sqlite-gate-watchdog.sh --compose-dir /path/to/LearnGraph --force-check
#
# 退出码：0=正常/已处理，1=环境错误（未安装 docker / 找不到容器），
#         2=探测到异常并已触发重启（供上层监控感知）。
set -u

COMPOSE_DIR=""
UNHEALTHY_STREAK=0        # 连续 unhealthy 多少次触发重启
UNHEALTHY_MAX=3
TIMEOUT_LOG_WINDOW="3m"   # 日志窗口
TIMEOUT_LOG_MAX=5         # 窗口内 30s 超时日志超过该值视为异常
LOG_FILE="/tmp/learngraph-gate-watchdog.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] $*"; }

usage() {
  echo "usage: $0 --compose-dir DIR [--unhealthy-max N] [--timeout-log-max N] [--force-check]"
  exit 1
}

while [ $# -gt 0 ]; do
  case "$1" in
    --compose-dir) COMPOSE_DIR="$2"; shift 2 ;;
    --unhealthy-max) UNHEALTHY_MAX="$2"; shift 2 ;;
    --timeout-log-max) TIMEOUT_LOG_MAX="$2"; shift 2 ;;
    --force-check) UNHEALTHY_STREAK=$UNHEALTHY_MAX ;;  # 立即进入判定
    *) usage ;;
  esac
done

[ -n "$COMPOSE_DIR" ] || usage
command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }

cd "$COMPOSE_DIR" || { echo "compose dir not found: $COMPOSE_DIR"; exit 1; }

# 定位 app 容器（compose 项目名兼容 -p 与目录名两种形态）
CONTAINER_ID=""
for candidate in $(docker compose ps -q app 2>/dev/null); do
  CONTAINER_ID="$candidate"
  break
done
if [ -z "$CONTAINER_ID" ]; then
  log "app container not found in $COMPOSE_DIR; skipping"
  exit 1
fi

HEALTH=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || echo "unknown")
TIMEOUTS=$(docker logs --since "$TIMEOUT_LOG_WINDOW" "$CONTAINER_ID" 2>&1 | grep -c "not acquired within 30s" || true)

log "app health=$HEALTH gate_timeouts_in_${TIMEOUT_LOG_WINDOW}=$TIMEOUTS (max=$TIMEOUT_LOG_MAX)"

RESTART=0
if [ "$HEALTH" = "unhealthy" ]; then
  UNHEALTHY_STREAK=$((UNHEALTHY_STREAK + 1))
  if [ "$UNHEALTHY_STREAK" -ge "$UNHEALTHY_MAX" ]; then
    log "app unhealthy $UNHEALTHY_STREAK times in a row; restarting"
    RESTART=1
  fi
else
  UNHEALTHY_STREAK=0
fi

if [ "$RESTART" = "0" ] && [ "$TIMEOUTS" -ge "$TIMEOUT_LOG_MAX" ]; then
  log "gate timeout storm detected ($TIMEOUTS >= $TIMEOUT_LOG_MAX); restarting"
  RESTART=1
fi

if [ "$RESTART" = "1" ]; then
  # 优雅重启：WAL 模式正常退出会完整 checkpoint，不丢已提交数据。
  if docker compose restart app >>"$LOG_FILE" 2>&1; then
    log "restart issued (see $LOG_FILE)"
    exit 2
  else
    log "restart FAILED; manual intervention required"
    exit 1
  fi
fi

exit 0
