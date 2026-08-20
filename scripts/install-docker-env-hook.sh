#!/usr/bin/env bash
# LearnGraph Docker env hook 安装器（Git Bash / bash）。
#
# 安装后：在 LearnGraph 仓库根目录下直接执行 `docker compose up -d --build`，
# 首次运行会自动从 .env.example 生成根目录 .env（幂等，绝不覆盖已有配置）——
# 无需 npm / scripts/compose.mjs 等任何额外入口。
#
# 用法:
#   bash scripts/install-docker-env-hook.sh            # 安装到 ~/.bashrc（Git Bash）
#   bash scripts/install-docker-env-hook.sh --uninstall  # 卸载（删除标记块）
#   LG_HOOK_PROFILE=/path/to/custom/.bashrc bash scripts/install-docker-env-hook.sh  # 自定义 profile（测试/CI）
#
# 安装内容：在 profile 末尾追加一个带标记的 source 块；hook 逻辑本体始终
# 保持在仓库 scripts/docker-env-hook.sh（单一来源，卸载只删 source 行）。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_FILE="$REPO_ROOT/scripts/docker-env-hook.sh"
PROFILE="${LG_HOOK_PROFILE:-$HOME/.bashrc}"

MARK_START="# >>> LearnGraph docker env hook (managed by scripts/install-docker-env-hook.sh) >>>"
MARK_END="# <<< LearnGraph docker env hook <<<"

say() { printf '\033[1;34m[hook]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[hook]\033[0m %s\n' "$*" >&2; }

is_installed() {
  [ -f "$PROFILE" ] && grep -qF "$MARK_START" "$PROFILE"
}

do_install() {
  if is_installed; then
    warn "$PROFILE 已安装过 hook（幂等，跳过）"
    return 0
  fi
  [ -f "$HOOK_FILE" ] || { echo "缺少 $HOOK_FILE" >&2; exit 1; }

  mkdir -p "$(dirname "$PROFILE")"
  cat >> "$PROFILE" <<EOF

$MARK_START
_LG_ENV_HOOK="$HOOK_FILE"
if [ -f "\$_LG_ENV_HOOK" ]; then . "\$_LG_ENV_HOOK"; fi
$MARK_END
EOF
  say "已写入 $PROFILE（source 仓库内 $HOOK_FILE）"
  say "当前 shell 立即生效："
  # 在当前进程 source 一次，无需重开终端。
  . "$HOOK_FILE"
  say "✅ 安装完成。现在直接执行 \`docker compose up -d --build\` 即可（.env 缺失时自动生成）。"
}

do_uninstall() {
  if ! is_installed; then
    warn "$PROFILE 未安装 hook"
    return 0
  fi
  # 整行字符串比较删除标记块（awk $0 == start，不涉及正则；临时文件 + mv 原子替换，
  # 不用 sed -i —— Git Bash 的 sed -i 在标记含括号/长参数时会被 MSYS 污染）。
  awk -v start="$MARK_START" -v end="$MARK_END" '
    $0 == start { skip = 1; next }
    skip { if ($0 == end) { skip = 0 } next }
    { print }
  ' "$PROFILE" > "$PROFILE.lg-tmp" && mv "$PROFILE.lg-tmp" "$PROFILE"
  say "已从 $PROFILE 移除 hook（当前 shell 中 docker() 函数仍在内存中，重开终端后完全移除）"
}

case "${1:-}" in
  --uninstall|-u) do_uninstall ;;
  --help|-h) sed -n '2,12p' "$0" ;;
  *) do_install ;;
esac
