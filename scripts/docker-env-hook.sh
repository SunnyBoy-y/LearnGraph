# LearnGraph Docker env hook — 由 scripts/install-docker-env-hook.sh 安装，
# 从交互式 shell（Git Bash / bash）source 本文件后生效。
#
# 效果：在 LearnGraph 仓库根目录下执行 `docker compose <任意子命令>` 时，
# 若根目录 .env 不存在，自动从 .env.example 生成（幂等，绝不覆盖已有配置）。
# 只识别包含 "learngraph" 的 compose 项目，避免干扰其它项目的 docker compose。
#
# 卸载：bash scripts/install-docker-env-hook.sh --uninstall

docker() {
  if [ "${1:-}" = "compose" ]; then
    local dir compose_file
    dir="$(pwd)"
    if [ -f "$dir/docker-compose.yml" ]; then
      compose_file="$dir/docker-compose.yml"
    elif [ -f "$dir/docker-compose.yaml" ]; then
      compose_file="$dir/docker-compose.yaml"
    else
      compose_file=""
    fi
    if [ -n "$compose_file" ] \
      && grep -qi 'learngraph' "$compose_file" \
      && [ ! -e "$dir/.env" ] \
      && [ -f "$dir/.env.example" ]; then
      # cp -n：并发竞态下绝不覆盖刚出现的 .env。
      cp -n "$dir/.env.example" "$dir/.env"
      printf '\033[1;34m[learngraph]\033[0m 已自动生成 %s/.env（模板 .env.example，可编辑后重新 docker compose up 生效）\n' "$dir"
    fi
  fi
  command docker "$@"
}
