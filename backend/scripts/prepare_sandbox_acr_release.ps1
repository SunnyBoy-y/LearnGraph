param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')]
    [string]$Version,

    [string]$Repository = "crpi-a89c780kegywb9dg.cn-hangzhou.personal.cr.aliyuncs.com/learngraph/learngraph",

    [string]$SourceImage = "learngraph-sandbox:local",

    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backendRoot ".venv\Scripts\python.exe"
$target = "$($Repository.ToLower()):$Version"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment Python was not found at $python"
}

$sourceInfo = docker image inspect $SourceImage --format '{{.Id}}|{{.Architecture}}|{{.Os}}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceInfo)) {
    throw "Source sandbox image was not found: $SourceImage"
}
$id, $architecture, $os = $sourceInfo.Trim().Split('|', 3)
if ($architecture -ne 'amd64' -or $os -ne 'linux') {
    throw "ACR Personal Edition release preparation currently supports linux/amd64 only; source is $architecture/$os"
}

if (-not $SkipSmoke) {
    $smoke = @"
import sys
sys.path.insert(0, r'$backendRoot')
from app.core.config import Settings
from app.services.sandbox_bootstrap import SandboxBootstrapService
result = SandboxBootstrapService()._smoke_test('$id', Settings())
if result:
    raise SystemExit(result)
print('Hardened sandbox smoke passed.')
"@
    & $python -c $smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Refusing to tag an image whose hardened sandbox smoke failed"
    }
}

# This tool intentionally only creates a local tag. It never runs docker login,
# docker push, or any registry write operation.
& docker tag $SourceImage $target
if ($LASTEXITCODE -ne 0) {
    throw "Could not create local ACR release tag: $target"
}

Write-Output "Prepared local ACR release tag: $target"
Write-Output "Architecture: linux/amd64"
Write-Output "Source image ID: $id"
Write-Output ""
Write-Output "No image was pushed and no ACR login was attempted."
Write-Output "When you are ready, run these commands yourself:"
Write-Output "  docker login --username=<你的阿里云账号全名> $($target.Split('/')[0])"
Write-Output "  docker push $target"
Write-Output ""
Write-Output "After the push finishes, obtain the immutable RepoDigest locally:"
Write-Output "  docker inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' $target"
Write-Output "Or copy the digest shown on the ACR console (镜像版本 -> 摘要)."
Write-Output ""
Write-Output "Consumers need NO configuration: the release default lives in code"
Write-Output "  (backend/app/core/config.py -> DEFAULT_SANDBOX_PREBUILT_IMAGE), so upgrading"
Write-Output "  the code picks up this runner version automatically. A version tag works"
Write-Output "  for first-time bootstrap: the application pulls it, resolves the immutable"
Write-Output "  RepoDigest, runs hardened smoke, and persists only that digest."
Write-Output ""
Write-Output "Only pin a specific deployment explicitly (admin lock) via env:"
Write-Output "  LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=$target"
Write-Output "  LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=$($target.Split(':')[0])@sha256:<repo-digest>"
