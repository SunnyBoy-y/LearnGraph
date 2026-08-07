param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9]+(?:[._-][a-z0-9]+)*/[a-z0-9]+(?:[._/-][a-z0-9]+)*$')]
    [string]$Repository,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')]
    [string]$Version,

    [string]$SourceImage = "learngraph-sandbox:local",

    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $backendRoot ".venv\Scripts\python.exe"
$target = "docker.io/$($Repository.ToLower()):$Version"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment Python was not found at $python"
}

$sourceInfo = docker image inspect $SourceImage --format '{{.Id}}|{{.Architecture}}|{{.Os}}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceInfo)) {
    throw "Source sandbox image was not found: $SourceImage"
}
$id, $architecture, $os = $sourceInfo.Trim().Split('|', 3)
if ($architecture -ne 'amd64' -or $os -ne 'linux') {
    throw "Docker Hub release preparation currently supports linux/amd64 only; source is $architecture/$os"
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
    throw "Could not create local Docker Hub release tag: $target"
}

Write-Output "Prepared local Docker Hub release tag: $target"
Write-Output "Architecture: linux/amd64"
Write-Output "Source image ID: $id"
Write-Output ""
Write-Output "No image was pushed and no Docker Hub login was attempted."
Write-Output "When you are ready, run these commands yourself:"
Write-Output "  docker login"
Write-Output "  docker push $target"
Write-Output "  docker buildx imagetools inspect $target"
Write-Output ""
Write-Output "After Docker Hub shows the RepoDigest, configure consumers with:"
Write-Output "  LEARNGRAPH_SANDBOX_PREBUILT_IMAGE=docker.io/$Repository@sha256:<repo-digest>"
Write-Output "A version tag also works for first-time bootstrap: the application pulls it, resolves"
Write-Output "the resulting immutable RepoDigest, runs hardened smoke, and persists only that digest."
