param(
    [string]$Tag = "learngraph-sandbox:local"
)

$ErrorActionPreference = "Stop"
$backendRoot = Split-Path -Parent $PSScriptRoot
$sandboxRoot = Join-Path $backendRoot "sandbox"

if (-not (Test-Path -LiteralPath (Join-Path $sandboxRoot "Dockerfile"))) {
    throw "Sandbox Dockerfile was not found at $sandboxRoot"
}

# The Dockerfile pins its base image by digest.  We intentionally do not edit
# .env here: operators must review and persist the resulting immutable image
# ID in their own deployment configuration.
docker build --tag $Tag $sandboxRoot
if ($LASTEXITCODE -ne 0) {
    throw "Docker sandbox image build failed"
}

$imageId = (docker image inspect --format '{{.Id}}' $Tag).Trim()
if ($LASTEXITCODE -ne 0 -or -not $imageId.StartsWith("sha256:")) {
    throw "Docker did not return an immutable image ID"
}

Write-Output "Sandbox image built successfully. Add this to backend/.env:"
Write-Output "LEARNGRAPH_SANDBOX_IMAGE=$imageId"
