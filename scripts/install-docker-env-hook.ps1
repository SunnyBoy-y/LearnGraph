<#
.SYNOPSIS
LearnGraph Docker env hook 安装器（Windows PowerShell / PowerShell 7）。

安装后：在 LearnGraph 仓库根目录下直接执行 `docker compose up -d --build`，
首次运行会自动从 .env.example 生成根目录 .env（幂等，绝不覆盖已有配置）——
无需 npm / scripts/compose.mjs 等任何额外入口。

Hook 逻辑本体在仓库 scripts/docker-env-hook.ps1（单一来源，升级仓库即自动更新）；
本安装器只在 profile 中写入一段带标记的 source 块。

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/install-docker-env-hook.ps1
powershell -ExecutionPolicy Bypass -File scripts/install-docker-env-hook.ps1 -Uninstall
powershell -ExecutionPolicy Bypass -File scripts/install-docker-env-hook.ps1 -ProfilePath C:	mp	est-profile.ps1   # 测试/CI
#>
param(
  [switch]$Uninstall,
  [string]$ProfilePath
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$hookFile = Join-Path $repoRoot 'scripts\docker-env-hook.ps1'
$profileFile = if ($ProfilePath) { $ProfilePath } else { $PROFILE.CurrentUserAllHosts }
$markStart = "# >>> LearnGraph docker env hook (managed by scripts/install-docker-env-hook.ps1) >>>"
$markEnd = "# <<< LearnGraph docker env hook <<<"
$hookLine = ". '$hookFile'"

function Say([string]$text) { Write-Host "[hook] $text" -ForegroundColor Cyan }

function Test-Installed {
  if (-not (Test-Path $profileFile -PathType Leaf)) { return $false }
  return Select-String -Path $profileFile -Pattern ([regex]::Escape($markStart)) -Quiet
}

function Remove-HookBlock {
  $lines = Get-Content -LiteralPath $profileFile -Encoding UTF8
  $inBlock = $false
  $kept = foreach ($line in $lines) {
    if ($line -eq $markStart) { $inBlock = $true; continue }
    if ($inBlock) {
      if ($line -eq $markEnd) { $inBlock = $false }
      continue
    }
    $line
  }
  Set-Content -LiteralPath $profileFile -Value $kept -Encoding UTF8
}

if ($Uninstall) {
  if (-not (Test-Installed)) {
    Write-Warning "$profileFile 未安装 hook"
    exit 0
  }
  Remove-HookBlock
  Say "已从 $profileFile 移除 hook（当前会话的 docker 函数仍在内存中，重开终端后完全移除）"
  exit 0
}

if (Test-Installed) {
  Write-Warning "$profileFile 已安装过 hook（幂等，跳过）"
  exit 0
}

if (-not (Test-Path $hookFile -PathType Leaf)) {
  throw "缺少 hook 逻辑文件: $hookFile"
}

$dir = Split-Path -Parent $profileFile
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

$content = if (Test-Path $profileFile -PathType Leaf) { Get-Content -LiteralPath $profileFile -Raw -Encoding UTF8 } else { '' }
$add = "`r`n$markStart`r`n$hookLine`r`n$markEnd`r`n"
Set-Content -LiteralPath $profileFile -Value ($content + $add) -Encoding UTF8 -NoNewline

Say "已写入 $profileFile（source 仓库内 $hookFile）"
Say "✅ 安装完成。重新打开终端后，直接执行 docker compose up -d --build 即可（.env 缺失时自动生成）。"
