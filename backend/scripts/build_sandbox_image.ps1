param(
    [string]$Tag = "learngraph-sandbox:local",
    [string]$PipIndexUrl,
    [string]$NpmRegistry
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$sandboxRoot = Join-Path $backendRoot "sandbox"

if (-not (Test-Path -LiteralPath (Join-Path $sandboxRoot "Dockerfile"))) {
    throw "Sandbox Dockerfile was not found at $sandboxRoot"
}

# Bootstrap persists the resulting immutable image ID in sandbox-runtime.json.
# This helper never edits .env: deployment overrides remain explicit and mirror
# URLs are build-time-only arguments.
$buildArgs = @("build", "--progress=plain", "--tag", $Tag)
if (-not [string]::IsNullOrWhiteSpace($PipIndexUrl)) {
    $buildArgs += @("--build-arg", "PIP_INDEX_URL=$($PipIndexUrl.Trim())")
}
if (-not [string]::IsNullOrWhiteSpace($NpmRegistry)) {
    $buildArgs += @("--build-arg", "NPM_REGISTRY=$($NpmRegistry.Trim())")
}
$buildArgs += $sandboxRoot

$previousBuildKit = $env:DOCKER_BUILDKIT
$env:DOCKER_BUILDKIT = "1"
try {
    & docker @buildArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Docker sandbox image build failed"
    }
}
finally {
    if ($null -eq $previousBuildKit) {
        Remove-Item Env:DOCKER_BUILDKIT -ErrorAction SilentlyContinue
    }
    else {
        $env:DOCKER_BUILDKIT = $previousBuildKit
    }
}

$imageId = (docker image inspect --format '{{.Id}}' $Tag).Trim()
if ($LASTEXITCODE -ne 0 -or -not $imageId.StartsWith("sha256:")) {
    throw "Docker did not return an immutable image ID"
}

Write-Output "Sandbox image built successfully. digest: $imageId"
Write-Output "Runtime digest is auto-read from data/sandbox-runtime.json (Bootstrap persists it"
Write-Output "on build); no .env pin is needed. Set LEARNGRAPH_SANDBOX_IMAGE only for CI/offline."
