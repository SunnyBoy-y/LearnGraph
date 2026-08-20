# LearnGraph Docker env hook — 由 scripts/install-docker-env-hook.ps1 安装，
# 从 PowerShell profile source 本文件后生效（单一来源：升级仓库即自动更新 hook）。
#
# 效果：在 LearnGraph 仓库根目录下执行 `docker compose <任意子命令>` 时，
# 若根目录 .env 不存在，自动从 .env.example 生成（幂等，绝不覆盖已有配置）。
# 只识别包含 "learngraph" 的 compose 项目，避免干扰其它项目的 docker compose。
#
# 卸载：powershell -ExecutionPolicy Bypass -File scripts/install-docker-env-hook.ps1 -Uninstall

function docker {
  if ($args.Count -gt 0 -and $args[0] -eq 'compose') {
    $dir = (Get-Location).Path
    $composeFile = Join-Path $dir 'docker-compose.yml'
    $composeFileYaml = Join-Path $dir 'docker-compose.yaml'
    $envFile = Join-Path $dir '.env'
    $template = Join-Path $dir '.env.example'
    $isLearnGraph = (Test-Path $composeFile -PathType Leaf) -and (Select-String -Path $composeFile -Pattern 'learngraph' -Quiet)
    if (-not $isLearnGraph) {
      $isLearnGraph = (Test-Path $composeFileYaml -PathType Leaf) -and (Select-String -Path $composeFileYaml -Pattern 'learngraph' -Quiet)
    }
    if ($isLearnGraph -and -not (Test-Path $envFile) -and (Test-Path $template -PathType Leaf)) {
      # overwrite=false：目标已存在（含竞态窗口内刚出现）绝不覆盖，抛错即跳过。
      try { [System.IO.File]::Copy($template, $envFile, $false) | Out-Null } catch { }
      if (Test-Path $envFile -PathType Leaf) {
        Write-Host "[learngraph] 已自动生成 $envFile（模板 .env.example，可编辑后重新 docker compose up 生效）" -ForegroundColor Cyan
      }
    }
  }
  $dockerCmd = Get-Command docker -CommandType Application | Select-Object -First 1
  if (-not $dockerCmd) { throw 'docker 命令未找到（请确认 Docker Desktop 已安装并加入 PATH）' }
  & $dockerCmd @args
}
