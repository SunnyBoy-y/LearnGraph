param(
    [string]$Tag = "learngraph-sandbox:local",
    [string]$PipIndexUrl,
    [string]$NpmRegistry,
    # Leave empty to build for the local Docker daemon platform. Set an
    # explicit value (linux/amd64 or linux/arm64) when the image will run on
    # a different CPU architecture. Buildx + --load keeps the result
    # available for the local smoke/bootstrap workflow.
    [string]$Platform = ""
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$sandboxRoot = Join-Path $backendRoot "sandbox"

if (-not (Test-Path -LiteralPath (Join-Path $sandboxRoot "Dockerfile"))) {
    throw "Sandbox Dockerfile was not found at $sandboxRoot"
}

if (-not [string]::IsNullOrWhiteSpace($Platform) -and $Platform -notin @("linux/amd64", "linux/arm64")) {
    throw "Platform must be linux/amd64, linux/arm64, or empty (native build); received '$Platform'"
}

# Bootstrap persists the resulting immutable image ID in sandbox-runtime.json.
# This helper never edits .env: deployment overrides remain explicit and mirror
# URLs are build-time-only arguments.
if ([string]::IsNullOrWhiteSpace($Platform)) {
    $buildArgs = @("build", "--progress=plain", "--tag", $Tag)
}
else {
    $buildArgs = @("buildx", "build", "--progress=plain", "--platform", $Platform, "--load", "--tag", $Tag)
}
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

$imageInfo = docker image inspect $Tag --format '{{.Architecture}}|{{.Os}}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($imageInfo)) {
    throw "Docker did not return the built image platform"
}
$builtArchitecture, $builtOs = $imageInfo.Trim().Split('|', 2)
if (-not [string]::IsNullOrWhiteSpace($Platform)) {
    $expectedOs, $expectedArchitecture = $Platform.Split('/', 2)
    if ($builtOs -ne $expectedOs -or $builtArchitecture -ne $expectedArchitecture) {
        throw "Built image platform is $builtOs/$builtArchitecture, expected $Platform"
    }
}

Write-Output "Sandbox image built successfully. digest: $imageId"
Write-Output "Platform: $builtOs/$builtArchitecture"
Write-Output "Runtime digest is auto-read from data/sandbox-runtime.json (Bootstrap persists it"
Write-Output "on build); no .env pin is needed. Set LEARNGRAPH_SANDBOX_IMAGE only for CI/offline."
